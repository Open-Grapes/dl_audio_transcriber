"""The Translate and Summary tabs. Both run their work in a background worker process (AiRunner), like the
transcriber, so the window never freezes and Stop works at once."""

import bisect
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine, langs, models, pinyin, theme, tts
from .common import APP_NAME, FORMATS, IS_MAC, fmt_hms, lang_name, log, open_path
from .theme import UI
from .widgets import FindBar, searchable
from .worker import Worker

AUTO = "Auto-detect"
SRC_CHOICES = [AUTO] + [langs.choice(c) for c in langs.NLLB]
TGT_CHOICES = [langs.choice(c) for c in langs.NLLB]
ENGINES = {"auto": "Auto (picks the best translator)", "ai": "AI: Qwen3 (most natural)",
           "nllb": "NLLB-200 (200 languages, fast)"}
DETAIL = {"short": "Short", "normal": "Normal", "detailed": "Detailed"}
TR_FORMATS = FORMATS


def _format_row(parent, app, key, choices, default, export_cmd):
    """“Save as [PDF] [TXT] …  [Export]” — each tab keeps its own ticks."""
    saved = app.s.get(key)
    on = [f for f in (saved if isinstance(saved, list) else default) if f in choices]
    vars_ = {f: tk.BooleanVar(value=f in on) for f in choices}

    def save(*_):
        app.s[key] = [f for f, v in vars_.items() if v.get()]
        app._save()
    row = ttk.Frame(parent)
    ttk.Label(row, text="Save as").pack(side="left", padx=(0, 6))
    for f in choices:
        vars_[f].trace_add("write", save)
        ttk.Checkbutton(row, text=f.upper(), variable=vars_[f],
                        style="Chip.TCheckbutton" if theme.mint() else "TCheckbutton").pack(side="left", padx=(0, 6))
    btn = ttk.Button(row, text="Export", style="Accent.TButton", command=export_cmd, width=10)
    btn.pack(side="right")
    ttk.Button(row, text="Save to…", command=app.choose_outdir).pack(side="right", padx=6)
    lbl = ttk.Label(row, style="Small.TLabel", text="Saves beside the recording (Transcripts folder), or in the "
                                                     "folder picked with Save to…")
    lbl.pack(side="right", padx=6)
    return row, lambda: [f for f, v in vars_.items() if v.get()]
NBSP = " "


def _short_code(code):
    return {"zho_Hans": "zh-Hans", "zho_Hant": "zh-Hant", "yue_Hant": "yue"}.get(code) or \
        langs.NLLB_TO_ISO.get(code) or code


split_text = engine.split_text


def read_text_file(p):
    """Text from a TXT / SRT / VTT / Markdown / CSV / Word file (subtitle numbers and times left out)."""
    p = Path(p)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        pages = [(pg.extract_text() or "").strip() for pg in PdfReader(str(p)).pages]
        text = "\n\n".join(x for x in pages if x)
        if not text.strip():
            raise RuntimeError("this PDF has no text in it (a scan or picture?)")
        return text
    if p.suffix.lower() == ".docx":
        import html
        import zipfile
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        paras = []
        for para in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            para = para.replace("<w:tab/>", "<w:t>\t</w:t>").replace("<w:br/>", "<w:t>\n</w:t>")
            paras.append(html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", para, re.S))))
        return "\n".join(paras)
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        for enc in ("utf-8-sig", "gb18030", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
    if p.suffix.lower() in (".srt", ".vtt"):
        keep = []
        for ln in text.splitlines():
            s_ = ln.strip()
            if s_.isdigit() or "-->" in s_ or s_.upper().startswith("WEBVTT") or s_.startswith(("NOTE", "STYLE")):
                continue
            keep.append(re.sub(r"<[^>]+>", "", ln))
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(keep))
    return text


class AiRunner:
    """One background worker for translation, summaries and their model downloads (one job at a time)."""

    BACKGROUND = ("names", "suggest")       # jobs nobody waits for: they give way to anything the user starts

    def __init__(self, app):
        self.app = app
        self.wk = None
        self.owner = None
        self.bg = False

    def busy(self):
        return self.owner is not None

    def label(self):
        """Which tab is using the AI right now (for messages)."""
        return getattr(self.owner, "ai_label", "Another tab")

    def free_for(self, owner):
        """Make the AI worker available to owner. Background work (finding speaker names, chat suggestions) is
        stopped to make room. If another tab is really working, ask whether to stop it. True = go ahead."""
        if self.owner is None or self.owner is owner:
            return True
        if self.bg:
            old = self.stop()
            if hasattr(old, "ai_cancelled"):
                old.ai_cancelled()
            return True
        name = self.label()
        if messagebox.askyesno(APP_NAME, f"The {name} tab is still using the AI.\n\nStop it and start this "
                                         "instead?", parent=self.app):
            old = self.owner
            if hasattr(old, "stop"):
                old.stop()
            if self.owner is not None:
                self.stop()
            return True
        return False

    def run(self, owner, jobs):
        if self.wk is None or self.wk.proc is None or not self.wk.proc.is_alive():
            self.wk = Worker(lambda *a: self.app.events.put(("ai",) + a))
        self.owner = owner
        self.bg = all(j["kind"] in self.BACKGROUND or j.get("bg") for j in jobs)
        for j in jobs:
            self.wk.submit(j)

    def handle(self, kind, *a):
        if kind == "device" or self.owner is None:
            return
        self.owner.on_ai(kind, *a)

    def release(self):
        self.owner = None
        self.bg = False

    def stop(self):
        owner, self.owner = self.owner, None
        self.bg = False
        if self.wk is not None:
            self.wk.kill()
            self.wk = None
        return owner

    def close(self):
        if self.wk is not None:
            self.wk.kill()


def ensure_models(app, keys, purpose):
    """Ask once before downloading models a feature needs. Returns the keys to download, or None (cancel)."""
    missing = [k for k in dict.fromkeys(keys) if not models.installed(k)]
    if not missing:
        return []
    lines = "\n".join(f"•  {models.EXTRA[k][0]}  ({models.fmt_size(models.size_mb(k) * 1e6)})" for k in missing)
    if messagebox.askyesno(APP_NAME, f"{purpose} needs:\n\n{lines}\n\nIt's a one-time download; after that it "
                                     "works offline. Download now?", parent=app):
        return missing
    return None


class _Pane(ttk.Frame):
    """A titled, read-only text view showing segments, with clickable sentences (tags t0, t1, ...)."""

    def __init__(self, parent, app, title):
        super().__init__(parent)
        self.app = app
        hdr = ttk.Frame(self)
        hdr.pack(fill="x", pady=(6, 4))
        self.title = ttk.Label(hdr, text=title, style="Muted.TLabel")
        self.title.pack(side="left")
        self.hdr = hdr
        self.on_change = None
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", height=6, font=(theme.TEXT, 11 + theme._S), relief="flat", padx=14,
                            pady=10, borderwidth=1, highlightthickness=1, spacing1=1, spacing3=1, cursor="arrow",
                            undo=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.seg_text = None            # i -> that sentence's text (for "Copy this sentence")
        self.copy_all = None            # () -> copies the whole pane nicely (the Copy button)
        for ev in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            self.text.bind(ev, self._menu)

    # ------------------------------------------------------------ right-click menu
    def _editable(self):
        return str(self.text.cget("state")) == "normal"

    def _menu(self, e):
        t = self.text
        p = self.app.pal
        m = tk.Menu(t, tearoff=0, bg=p["panel"], fg=p["text"], activebackground=p["accent"],
                    activeforeground="#ffffff", disabledforeground=p["faint"])
        has_sel = bool(t.tag_ranges("sel"))
        i = self.seg_at(e)
        edit = self._editable()
        if edit:
            m.add_command(label="Cut", command=lambda: t.event_generate("<<Cut>>"),
                          state="normal" if has_sel else "disabled")
        # one Copy: the highlighted text, or (with nothing highlighted) the sentence that was right-clicked
        sent = i is not None and self.seg_text and not edit
        m.add_command(label="Copy", command=self._copy_sel if has_sel else lambda: self._copy(self.seg_text(i)),
                      state="normal" if has_sel or sent else "disabled")
        if edit:
            m.add_command(label="Paste", command=lambda: (t.focus_set(), t.event_generate("<<Paste>>")))
        m.add_separator()
        m.add_command(label="Select all", command=lambda: (t.tag_add("sel", "1.0", "end-1c"), t.focus_set()))
        if self.copy_all:
            m.add_command(label="Copy all", command=self.copy_all)
        m.tk_popup(e.x_root, e.y_root)
        return "break"

    def _copy_sel(self):
        try:
            self._copy(self.text.get("sel.first", "sel.last"))
        except tk.TclError:
            pass

    def _copy(self, text):
        if text:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)

    def recolor(self, p, dark):
        sel = p.get("select", "#cce0ff")
        t = self.text
        t.configure(bg=p["field"], fg=p["text"], insertbackground=p["text"], highlightbackground=p["line"],
                    highlightcolor=p["accent"], selectbackground=sel, inactiveselectbackground=sel)
        t.tag_configure("ts", foreground=p["faint"], font=(UI, 9 + theme._S))
        t.tag_configure("spk", foreground=p["accent"], font=(UI, 10 + theme._S, "bold"), spacing1=4)
        t.tag_configure("hint", foreground=p["faint"])
        t.tag_configure("pair", background="#2f3b52" if dark else "#fff3b0")
        t.tag_configure("h", foreground=p["accent"], font=(UI, 12 + theme._S, "bold"), spacing1=8, spacing3=2)
        t.tag_configure("py", foreground=p["muted"], font=(UI, 10 + theme._S), spacing1=2, spacing3=3)
        t.tag_configure("en", foreground=p["muted"], font=(theme.TEXT, 10 + theme._S, "italic"), spacing1=2,
                        spacing3=3)
        t.tag_configure("gap", spacing3=12)
        t.tag_raise("gap")
        t.tag_raise("sel")

    def show(self, res, timestamps, editable=False):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        ts = timestamps and not res.get("no_times")
        j = " " if ts else ("" if res.get("language") in engine.CJK else " ")
        want_py = self.app.v_py.get()
        lang = res.get("language") or res.get("spoken_language")
        want_en = self.app.v_en.get() and res.get("task") != "translate" or (
            self.app.v_en.get() and lang and lang != "en")
        from . import english
        if want_en:
            todo = [s["text"] for s in res.get("segments") or [] if english.needs_english(s["text"], lang)]
            hint = langs.from_iso(lang) if lang else None
            if hint == "zho_Hans" and todo:
                hint = langs.chinese_variant(" ".join(todo[:50]))
            if todo:
                self.app.english.want(todo, hint)
        idx = 0
        for pi, (name, para) in enumerate(engine.labelled(res)):
            if pi:
                t.insert("end", "\n\n")
            if name:
                t.insert("end", name + "\n", ("spk",))
            prev_py = False
            for k, s in enumerate(para):
                txt = s["text"]
                py = want_py and pinyin.is_chinese(txt)
                en = None
                if want_en and txt and english.needs_english(txt, lang):
                    en = self.app.english.get(txt) or "…"
                lines = py or bool(en)
                if k:
                    t.insert("end", "\n" if (lines or prev_py or ts) else j)   # times / pinyin: a line each
                tag = f"t{s.get('_i', idx)}"
                gap = ("gap",) if (lines or ts) else ()
                if ts:   # a line's spacing comes from its first character: the time carries the gap too
                    t.insert("end", f"({fmt_hms(s['start'])}){NBSP}", ("ts", tag) + (() if lines else gap))
                t.insert("end", txt if txt else "…", (tag,) + (() if txt else ("hint",)) + (() if lines else gap))
                if py:
                    t.insert("end", "\n" + pinyin.line(txt), ("py", tag) + (() if en else gap))
                if en:
                    t.insert("end", "\n" + en, ("en", tag) + gap)
                prev_py = lines
                idx += 1
        t.configure(state="normal" if editable else "disabled")
        if self.on_change:
            self.on_change()

    def message(self, msg):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", msg, ("hint",))
        t.configure(state="disabled")

    def seg_at(self, e):
        idx = self.text.index(f"@{e.x},{e.y}")
        for tg in self.text.tag_names(idx):
            if tg[0] == "t" and tg[1:].isdigit():
                return int(tg[1:])
        return None

    def mark(self, i, scroll=True):
        t = self.text
        t.tag_remove("pair", "1.0", "end")
        if i is None:
            return
        rng = t.tag_ranges(f"t{i}")
        if rng:
            t.tag_add("pair", rng[0], rng[-1])
            if scroll:
                t.see(rng[-1])
                t.see(rng[0])


# ============================================================================ Translate
class TranslateTab(ttk.Frame):
    ai_label = "Translate"

    def __init__(self, nb, app):
        super().__init__(nb, padding=(12, 10, 12, 12))
        self.app = app
        s = app.s
        self.src = None            # dict(kind, item, file, res(meta+segments), no_times, hint)
        self.trans = []            # translated text per segment
        self.result = None         # the translation, shaped like a transcription result (for export)
        self.running = False
        self.now = None
        self._redraw = None
        # From: Auto-detect until there's something to translate (an imported recording sets its language)
        self.v_src = tk.StringVar(value=AUTO)
        tgt = s.get("tr_tgt", "eng_Latn")
        self.v_tgt = tk.StringVar(value=langs.choice(tgt if tgt in langs.NLLB else "eng_Latn"))
        self.v_eng = tk.StringVar(value=ENGINES.get(s.get("tr_engine"), ENGINES["auto"]))
        for v in (self.v_src, self.v_tgt, self.v_eng):
            v.trace_add("write", lambda *_: self._save())

        r1 = ttk.Frame(self)
        r1.pack(fill="x")
        ttk.Label(r1, text="From").pack(side="left", padx=(0, 6))
        self.src_box = ttk.Combobox(r1, textvariable=self.v_src, values=SRC_CHOICES, width=30)
        self.src_box.pack(side="left")
        searchable(self.src_box, SRC_CHOICES, AUTO)
        ttk.Label(r1, text="→  To").pack(side="left", padx=(12, 6))
        self.tgt_box = ttk.Combobox(r1, textvariable=self.v_tgt, values=TGT_CHOICES, width=30)
        self.tgt_box.pack(side="left")
        searchable(self.tgt_box, TGT_CHOICES, langs.choice("eng_Latn"))
        ttk.Label(r1, text="Translator").pack(side="left", padx=(16, 6))
        eng = ttk.Combobox(r1, textvariable=self.v_eng, values=list(ENGINES.values()), state="readonly", width=32)
        eng.pack(side="left")
        eng.bind("<<ComboboxSelected>>", lambda e: eng.selection_clear())

        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=(10, 0))
        ttk.Button(r2, text="Import from Transcribe", command=self.import_transcript).pack(side="left")
        ttk.Button(r2, text="Open file…", command=self.open_file).pack(side="left", padx=(6, 0))
        ttk.Button(r2, text="Paste text", command=self.paste_mode).pack(side="left", padx=6)
        ttk.Button(r2, text="Clear", command=self.clear).pack(side="left")
        self.go = ttk.Button(r2, text="Translate", style="Accent.TButton", command=self.translate, width=12)
        self.go.pack(side="right")
        self.voice_btn = ttk.Button(r2, text="🔊  Read aloud ▾", command=self._voice_menu)
        self.voice_btn.pack(side="right", padx=(0, 8))
        self._await = None             # Transcribe row that Open file… is transcribing for this tab
        self.info = ttk.Label(r2, style="Muted.TLabel")
        self.info.pack(side="left", padx=12, fill="x", expand=True)
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(10, 0))

        pane = ttk.PanedWindow(self, orient="vertical")
        pane.pack(fill="both", expand=True, pady=(4, 0))
        self.top = _Pane(pane, app, "Original")
        self.bot = _Pane(pane, app, "Translation")
        pane.add(self.top, weight=1)
        pane.add(self.bot, weight=1)
        theme.SashGrip(pane)
        ttk.Button(self.top.hdr, text="Copy", command=lambda: self.copy(False)).pack(side="right")
        ttk.Button(self.bot.hdr, text="Copy", command=lambda: self.copy(True)).pack(side="right")
        self.find = FindBar(self, [self.top.text, self.bot.text])
        self.find.place_fn = lambda: self.find.pack(fill="x", pady=(8, 0), before=pane)
        ttk.Button(self.top.hdr, text="Find", command=self.find.open, width=6).pack(side="right", padx=(0, 6))
        for p in (self.top, self.bot):
            p.on_change = self.find.refresh
        ttk.Checkbutton(self.bot.hdr, text="Show timestamps", variable=app.v_ts).pack(side="right", padx=10)
        ttk.Checkbutton(self.bot.hdr, text="English", variable=app.v_en).pack(side="right", padx=(0, 10))
        ttk.Checkbutton(self.bot.hdr, text="Pinyin", variable=app.v_py).pack(side="right", padx=(0, 10))
        app.english.listeners.append(lambda: app.v_en.get() and not self.running and self.render())
        for p in (self.top, self.bot):
            p.text.bind("<ButtonRelease-1>", self._click)
        self.top.seg_text = lambda i: self._seg_text(False, i)
        self.bot.seg_text = lambda i: self._seg_text(True, i)
        self.top.copy_all = lambda: self.copy(False)
        self.bot.copy_all = lambda: self.copy(True)
        # the Original box: click and type (or paste) any time; pasted wrapped lines are joined
        t = self.top.text
        t.bind("<Button-1>", self._top_press, add="+")
        t.bind("<Key>", self._top_key, add="+")
        t.bind("<<Paste>>", self._top_paste)
        bar = app.player_bar(self, self.play_pause, self._seek)
        bar.frame.pack(fill="x", pady=(8, 0))
        row, self._fmts = _format_row(self, app, "tr_formats", TR_FORMATS, app._formats() or ["pdf", "txt"],
                                      self.export)
        row.pack(fill="x", pady=(10, 0))
        self.status = ttk.Label(self, style="Muted.TLabel", wraplength=900, justify="left")
        self.status.pack(fill="x", pady=(8, 0))
        self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=max(e.width - 4, 100)))
        self._empty()

    # ------------------------------------------------------------ helpers
    def _save(self):
        s = self.app.s
        s.pop("tr_src", None)                 # not remembered: every start is Auto-detect
        s["tr_tgt"] = langs.from_choice(self.v_tgt.get()) or "eng_Latn"
        s["tr_engine"] = next((k for k, v in ENGINES.items() if v == self.v_eng.get()), "auto")
        self.app._save()

    def _engine(self):
        return next((k for k, v in ENGINES.items() if v == self.v_eng.get()), "auto")

    def _tgt(self):
        return langs.from_choice(self.v_tgt.get()) or "eng_Latn"

    def recolor(self, p, dark):
        self.top.recolor(p, dark)
        self.bot.recolor(p, dark)
        self.find.recolor(dark)

    def _empty(self):
        self.top.message("Click here and type, or press  Paste text  (or Ctrl/Cmd+V here) to translate any text.\n\n"
                         "Import from Transcribe  brings in the transcript you're looking at on the Transcribe tab "
                         "(with its timestamps and speakers).  Open file…  opens a text file (TXT, SRT, VTT, Word) "
                         "or an audio / video file (it's transcribed first).\n\nThen pick the languages above and "
                         "press  Translate.  🔊 Read aloud  speaks the text with an offline voice.")
        self.top.text.configure(cursor="xterm")
        self.bot.message("The translation appears here, sentence by sentence. Click a sentence in either box to "
                         "see its match (and play it, for a transcript).")
        self.info.configure(text="")

    def _flash(self, msg):
        self.status.configure(text=msg)

    def _src_result(self):
        return self.src["res"] if self.src else None

    def _trans_result(self):
        if not self.src:
            return None
        res = self.src["res"]
        tgt = self._tgt() if not self.result else self.result["_tgt"]
        segs = [dict(s, text=(self.trans[i] if i < len(self.trans) and self.trans[i] is not None else ""),
                     words=[]) for i, s in enumerate(res["segments"])]
        return dict(res, segments=segs, language=langs.NLLB_TO_ISO.get(tgt, "en") if tgt != "yue_Hant" else "yue")

    def render(self, keep=True):
        if not self.src:
            return
        ts = self.app.v_ts.get()
        if self.src["kind"] == "paste" and self.src.get("editing"):
            return
        y1, y2 = self.top.text.yview()[0], self.bot.text.yview()[0]
        self.top.show(self.src["res"], ts)
        if any(t is not None for t in self.trans):
            self.bot.show(self._trans_result(), ts)
        if keep:
            self.top.text.yview_moveto(y1)
            self.bot.text.yview_moveto(y2)
        if self.now is not None:
            self.top.mark(self.now, False)
            self.bot.mark(self.now, False)

    # ------------------------------------------------------------ sources
    def import_transcript(self, item=None):
        app = self.app
        if self.running:
            return
        if app.editing:
            app.end_edit(True)
        item = item or app.preview_item
        r = app.rows.get(item) if item else None
        if not r or not r.get("result"):
            done = [i for i in app.tree.get_children() if app.rows[i].get("result")]
            if not done:
                self._flash("Nothing to import yet: transcribe a file on the Transcribe tab first.")
                return
            item, r = done[0], app.rows[done[0]]
        res = r["result"]
        segs = [{"id": i, "_i": i, "start": s["start"], "end": s["end"], "text": s["text"],
                 **({"speaker": s["speaker"]} if "speaker" in s else {})} for i, s in enumerate(res["segments"])]
        meta = {k: res.get(k) for k in ("file", "duration", "spoken_language", "language", "speakers", "model")}
        hint = langs.from_iso(res.get("spoken_language"))
        if hint == "zho_Hans" and res.get("segments"):
            hint = langs.chinese_variant(" ".join(s["text"] for s in res["segments"][:50]))
        untimed = bool(res.get("no_times"))              # a document added in Chat: no times, no audio
        self.src = {"kind": "import", "item": item, "file": r["file"], "no_times": untimed, "hint": hint,
                    "res": dict(meta, task="transcribe", segments=segs, no_times=untimed)}
        # From = the language the transcript is in (so a Tamil recording isn't translated as Malay)
        self.v_src.set(langs.choice(hint) if hint in langs.NLLB else AUTO)
        self._from_import = True
        self.trans, self.result, self.now = [], None, None
        self.top.text.configure(cursor="hand2")
        self.top.title.configure(text=f"Original — {r['file'].name}")
        self.bot.title.configure(text="Translation")
        self.info.configure(text=f"{r['file'].name} · {lang_name(res.get('spoken_language'))} · {len(segs)} "
                                 f"sentences" + (f" · {len(res['speakers'])} speakers" if res.get('speakers') else ""))
        self.render(keep=False)
        self.bot.message("Pick the languages above and press  Translate.")
        self._flash("Imported. The original keeps its timestamps and speakers; the translation will too.")

    def paste_mode(self, text=None, name=None, from_clip=True):
        """Type / paste / opened-file text into the Original box. text=None: take the clipboard (Paste text)."""
        if self.running:
            return
        if getattr(self, "_from_import", False):           # From was set by an import: new text -> Auto-detect
            self.v_src.set(AUTO)
            self._from_import = False
        self.src = {"kind": "paste", "item": None, "file": None, "no_times": True, "hint": None, "editing": True,
                    "name": name, "res": {"file": name or "Pasted text", "segments": [], "no_times": True,
                                          "task": "transcribe"}}
        self.trans, self.result, self.now = [], None, None
        t = self.top.text
        t.configure(state="normal", cursor="xterm")
        t.delete("1.0", "end")
        clip = ""
        if text is None and from_clip:
            try:
                clip = self.app.clipboard_get()
            except tk.TclError:
                clip = ""
            text = clip
        if text and text.strip():
            t.insert("1.0", engine.join_wrapped(text.strip("\n")))
        t.edit_reset()
        t.focus_set()
        t.mark_set("insert", "end-1c")
        self.top.title.configure(text=f"Original — {name}" if name else
                                 "Original — type or paste here (Ctrl/Cmd+V)")
        self.bot.title.configure(text="Translation")
        self.bot.message("Pick the languages above and press  Translate.")
        self.info.configure(text=name or "Your text")
        if name:
            self._flash(f"Opened {name}. Edit it if needed, then press Translate.")
        elif clip.strip():
            self._flash("Pasted what was on the clipboard. Edit it if needed, then press Translate.")
        else:
            self._flash("Type or paste the text in the top box, then press Translate.")

    # ------------------------------------------------------------ typing in the Original box
    def _top_press(self, e):
        if self.running:
            return
        if self.src is None:
            self.paste_mode(from_clip=False)

    def _top_key(self, e):
        """Typing into the Original box: start a new text, or go back to editing a translated one."""
        if self.running:
            return "break" if self.src and self.src["kind"] == "paste" else None
        typing = (e.char and e.char.isprintable()) or e.keysym in ("BackSpace", "Delete", "Return", "KP_Enter")
        if not typing or (e.state & 0x4 and e.keysym.lower() not in ("v",)):      # Ctrl+C, Ctrl+A …: leave
            return None
        if self.src is None:
            self.paste_mode(from_clip=False)
        elif self.src["kind"] == "paste" and not self.src.get("editing"):
            self._edit_again()
        return None

    def _top_paste(self, e):
        """Paste into the Original box, joining lines that were only wrapped."""
        if self.running:
            return "break"
        if self.src is None:
            self.paste_mode(from_clip=False)
        elif self.src["kind"] != "paste":
            return "break"                       # an imported transcript isn't edited here
        elif not self.src.get("editing"):
            self._edit_again()
        try:
            clip = self.app.clipboard_get()
        except tk.TclError:
            return "break"
        t = self.top.text
        try:
            t.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        t.insert("insert", engine.join_wrapped(clip))
        t.see("insert")
        return "break"

    def _edit_again(self):
        """Back to editing the text after a translation (the translation stays until Translate is pressed)."""
        segs = self.src["res"]["segments"]
        raw = self.src.get("raw") or "\n".join(("\n" if s.get("para") and i else "") + s["text"]
                                              for i, s in enumerate(segs))
        self.src["editing"] = True
        t = self.top.text
        t.configure(state="normal", cursor="xterm")
        t.delete("1.0", "end")
        t.insert("1.0", raw)
        t.mark_set("insert", "end-1c")
        t.focus_set()
        self.top.mark(None)
        self._flash("Editing the text. Press Translate again when you're done.")

    def _take_text(self):
        """Typed text -> sentences. False if there's nothing."""
        if self.src["kind"] == "paste" and self.src.get("editing"):
            text = self.top.text.get("1.0", "end").strip()
            segs = split_text(text)
            if not segs:
                self._flash("The box is empty: type or paste some text first.")
                return False
            for i, sg in enumerate(segs):
                sg["_i"] = i
            self.src["raw"] = text
            self.src["res"]["segments"] = segs
            self.src["res"]["no_times"] = True
            self.src["no_times"] = True
            self.src["item"] = None
            self.src["editing"] = False
            self.top.text.configure(cursor="hand2")
            self.info.configure(text=f"{self.src.get('name') or 'Your text'} · {len(segs)} sentences")
            self.trans, self.result = [], None
        return True

    def _seg_text(self, translated, i):
        if not self.src:
            return ""
        if translated:
            return self.trans[i] if i < len(self.trans) and self.trans[i] else ""
        segs = self.src["res"]["segments"]
        return segs[i]["text"] if i < len(segs) else ""

    # ------------------------------------------------------------ open a file
    TEXT_EXT = (".txt", ".srt", ".vtt", ".md", ".docx", ".pdf", ".csv", ".text")

    def open_file(self):
        if self.running:
            return
        from .common import MEDIA_EXT
        media = " ".join(f"*.{x}" for x in sorted(MEDIA_EXT))
        f = filedialog.askopenfilename(parent=self.app, title="Open a text, audio or video file", filetypes=[
            ("Text, audio or video", " ".join(f"*{x}" for x in self.TEXT_EXT) + " " + media),
            ("Text (TXT, PDF, Word, SRT, VTT)", " ".join(f"*{x}" for x in self.TEXT_EXT)),
            ("Audio & video", media), ("All files", "*.*")])
        if not f:
            return
        p = Path(f)
        if p.suffix.lower() in self.TEXT_EXT:
            try:
                text = read_text_file(p)
            except Exception as e:
                self._flash(f"Couldn't read {p.name}: {e}")
                return
            if not text.strip():
                self._flash(f"{p.name} has no text in it.")
                return
            self.paste_mode(text=text, name=p.name)
            return
        # audio / video: transcribe it on the Transcribe tab, then bring it in here
        app = self.app
        item = next((i for i, r in app.rows.items() if Path(r["file"]) == p), None)
        if item is None:
            app.add([str(p)])
            item = next((i for i, r in app.rows.items() if Path(r["file"]) == p), None)
            if item is None:
                self._flash(f"{p.name} isn't an audio or video file this app can read.")
                return
        r = app.rows[item]
        if r.get("result") and r["status"] == "DONE":
            self.import_transcript(item)
            return
        self._await = item
        app.tree.selection_set(item)
        if r["status"] in ("FAIL", "SKIP"):
            app._set_status(item, "WAIT")
        if not app.running:
            app.start()
        self.top.message(f"Transcribing {p.name} on the Transcribe tab …\n\nIt appears here as soon as it's done.")
        self.bot.message("")
        self.src, self.trans, self.result = None, [], None
        self.info.configure(text=p.name)
        self._flash(f"Transcribing {p.name} first (watch the progress on the Transcribe tab).")

    def on_transcribed(self, item, ok=True):
        """The Transcribe tab finished a file. If it's the one Open file… asked for, bring it in."""
        if item != self._await:
            return
        self._await = None
        if ok:
            self.import_transcript(item)
            self._flash("Transcribed and imported. Pick the languages above and press Translate.")
        else:
            self._empty()
            self._flash("The transcription didn't finish (see the Transcribe tab).")

    # ------------------------------------------------------------ read aloud
    def _voice_menu(self):
        if self.running:
            return
        app = self.app
        p = app.pal
        m = tk.Menu(self, tearoff=0, bg=p["panel"], fg=p["text"], activebackground=p["accent"],
                    activeforeground="#ffffff", disabledforeground=p["faint"])
        has_tr = any(self.trans)
        has_src = bool(self.src) and (bool(self.src["res"]["segments"]) or (
            self.src["kind"] == "paste" and self.top.text.get("1.0", "end").strip()))
        tgt = self.result["_tgt"] if self.result else self._tgt()
        src = self._source_code() if has_src and self.src["res"]["segments"] else None
        tv, sv = self._voice_of(tgt), (self._voice_of(src) if src else None)
        m.add_command(label=f"Read the translation aloud  ·  {langs.name(tgt)}" + (f", {tv['name']}" if tv else ""),
                      command=lambda: self.read_aloud(True), state="normal" if has_tr else "disabled")
        m.add_command(label="Read the original aloud" + (f"  ·  {langs.name(src)}, {sv['name']}" if sv else ""),
                      command=lambda: self.read_aloud(False), state="normal" if has_src else "disabled")
        m.add_separator()
        m.add_command(label=f"Choose the {langs.name(tgt)} voice…", command=lambda: self.choose_voice(tgt))
        if src and tts._base(src) != tts._base(tgt):
            m.add_command(label=f"Choose the {langs.name(src)} voice…", command=lambda: self.choose_voice(src))
        m.add_separator()
        self._g = tk.StringVar(value=app.s.get("tts_gender", "female"))
        for g, lbl in (("female", "Automatic: female voices"), ("male", "Automatic: male voices")):
            m.add_radiobutton(label=lbl, value=g, variable=self._g,
                              command=lambda g=g: (app.s.__setitem__("tts_gender", g), app._save()))
        m.add_separator()
        m.add_command(label="About the voices…", command=self._voice_info)
        b = self.voice_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _voice_of(self, code):
        """The voice that will read this language: the one chosen for it, else the automatic pick."""
        if not code:
            return None
        s = self.app.s
        return tts.pick(code, s.get("tts_gender", "female"), (s.get("voices") or {}).get(tts._base(code)))

    def choose_voice(self, code):
        from .voice_win import VoiceWindow
        if self.src and self.src["res"]["segments"]:
            if tts._base(code) == tts._base(self.result["_tgt"] if self.result else self._tgt()) and any(self.trans):
                sample = next((t for t in self.trans if t and t.strip()), "")
            else:
                sample = next((s["text"] for s in self.src["res"]["segments"] if s["text"].strip()), "")
        else:
            sample = ""
        VoiceWindow(self.app, code, sample)

    def _voice_info(self):
        messagebox.showinfo(APP_NAME, (
            "Read aloud makes a temporary voice recording of the text, offline, so you can hear it and click any "
            "sentence to play it (also handy for text that has no recording).\n\n"
            "The app picks a native voice for the language by itself:\n"
            "•  Mandarin and English: Kokoro voices (Apache-2.0, ~215 MB, 100 speakers).\n"
            "•  Malay: Malaysian voices Yasmin, Osman, Idayu and Husein (mesolitica, ~115 MB each).\n"
            "•  About 40 more languages (German, French, Spanish, Hindi, Indonesian, Vietnamese, Arabic, Russian, "
            "Turkish …): native Piper voices (~60 MB each; licence in each voice's MODEL_CARD).\n"
            "•  Any other language (about 1,100, e.g. Tamil): Meta MMS voices (one voice each, ~115 MB, free for "
            "non-commercial use).\n\n"
            "Choose the … voice… lists every voice for a language: preview it with your own text and pick the one "
            "you like (remembered per language). Each voice downloads the first time it's used. The recording is "
            "added to the Transcribe list, where Save audio as MP3 saves it. It's a computer voice for checking a "
            "script, not a voice-over."), parent=self.app)

    def _source_code(self):
        """The language of the original text (NLLB code)."""
        c = self.v_src.get()
        if c != AUTO:
            return langs.from_choice(c)
        if self.src.get("hint"):
            return self.src["hint"]
        text = " ".join(s["text"] for s in self.src["res"]["segments"][:40])
        return langs.detect(text)

    def read_aloud(self, translated):
        app = self.app
        if self.running or not self.src:
            return
        if not translated and not self._take_text():
            return
        if translated:
            if self.src.get("editing"):
                self._flash("The text was changed: press Translate first, then read the translation aloud.")
                return
            if not any(self.trans):
                self._flash("Translate first, then read the translation aloud.")
                return
            texts = [t or "" for t in self.trans]
            code = self.result["_tgt"] if self.result else self._tgt()
        else:
            texts = [s["text"] for s in self.src["res"]["segments"]]
            code = self._source_code()
        v = self._voice_of(code)
        if v is None:
            self._flash(f"There's no offline voice for {langs.name(code)} yet.")
            return
        if not tts.installed(v):
            if not messagebox.askyesno(APP_NAME, f"Reading {langs.name(code)} aloud with {v['name']} needs a "
                                                 f"one-time download of about {v['mb']} MB (licence: "
                                                 f"{v['license']}).\n\nDownload now?", parent=app):
                self._flash("Voice not downloaded — nothing read. (Choose the voice… lists the others.)")
                return
        if not app.ai.free_for(self):
            self._flash(f"The {app.ai.label()} tab is still using the AI; nothing started.")
            return
        stem = Path(str(self.src["file"])).stem if self.src.get("file") else (
            Path(self.src.get("name")).stem if self.src.get("name") else "Text")
        folder = tts.voices_dir()
        out = folder / f"{stem} - {langs.name(code)} voice.wav"
        k = 2
        while out.exists() and any(Path(r["file"]) == out for r in app.rows.values()):
            out = folder / f"{stem} - {langs.name(code)} voice {k}.wav"
            k += 1
        self._voice_job = {"translated": translated, "code": code, "texts": texts}
        self._busy(True)
        self.progress["value"] = 0
        self._flash(f"Getting the voice ready ({v['name']}) …")
        app.ai.run(self, [{"kind": "tts", "id": "tts", "texts": texts, "lang": code, "voice": v["id"],
                           "out": str(out)}])

    def _voiced(self, info, took):
        app = self.app
        job = self._voice_job
        texts, code = job["texts"], job["code"]
        iso = "yue" if code == "yue_Hant" else langs.NLLB_TO_ISO.get(code, "en")
        segs = [{"id": i, "start": st, "end": en, "text": texts[i]} for i, (st, en) in enumerate(info["times"])]
        res = {"file": info["out"], "model": "read aloud", "device": "cpu", "task": "transcribe",
               "note": "read aloud", "speakers": [], "voices": [], "language": iso, "spoken_language": iso,
               "language_probability": 1.0, "duration": info["duration"], "segments": segs}
        item = app.add_voice(info["out"], res, took)
        # the sentences now have times in that recording: click to play, follow while playing
        for i, s in enumerate(self.src["res"]["segments"]):
            if i < len(segs):
                s["start"], s["end"] = segs[i]["start"], segs[i]["end"]
        self.src["res"]["no_times"] = False
        self.src["no_times"] = False
        self.src["item"] = item
        self.src["voice"] = True
        if self.result:
            self.result = dict(self._trans_result_for(self.result["_tgt"]), model=self.result.get("model"))
        self.now = None
        self.render(keep=True)
        who = "translation" if job["translated"] else "original"
        self._flash(f"Read the {who} aloud ({langs.name(code)}, {info.get('voice_name', '')}, "
                    f"{fmt_hms(info['duration'])}) in {fmt_hms(took)}. "
                    "Click any sentence to hear it. The recording is also in the Transcribe list "
                    "(Save audio as MP3 saves it).")
        self._play(0)

    def clear(self):
        if self.running:
            return
        self.src, self.trans, self.result, self.now = None, [], None, None
        self.v_src.set(AUTO)
        self._from_import = False
        self.top.text.configure(state="normal")
        self.top.title.configure(text="Original")
        self.bot.title.configure(text="Translation")
        self._empty()
        self._flash("")

    # ------------------------------------------------------------ translate
    def translate(self):
        app = self.app
        if self.running:
            self.stop()
            return
        if not self.src:
            self._flash("Import a transcript or paste some text first.")
            return
        if not self._take_text():
            return
        if not app.ai.free_for(self):
            self._flash(f"The {app.ai.label()} tab is still using the AI; nothing started.")
            return
        texts = [s["text"] for s in self.src["res"]["segments"]]
        src_choice = self.v_src.get()
        src = None if src_choice == AUTO else langs.from_choice(src_choice)
        tgt = self._tgt()
        from . import ai
        eng, key, srcs = ai.plan(texts, src, tgt, self._engine(), app.has_gpu, self.src.get("hint"))
        need = ensure_models(app, [key], "Translating" + (" with the AI model" if eng == "ai" else ""))
        if need is None:
            self._flash("Model not downloaded — nothing started.")
            return
        found = sorted({langs.name(s) for s, t in zip(srcs, texts) if t.strip()})
        self.info.configure(text=(self.info.cget("text").split("  ·  From:")[0] +
                                  f"  ·  From: {', '.join(found[:4])}{'…' if len(found) > 4 else ''}"))
        self.trans = [None] * len(texts)
        self.result = None
        self.now = None
        self.render(keep=False)
        self.bot.title.configure(text=f"Translation — {langs.name(tgt)}  ({'AI: Qwen3' if eng == 'ai' else 'NLLB-200'})")
        self.bot.message("Translating …")
        jobs = []
        if need:
            jobs.append({"kind": "download", "id": "ai-dl", "models": need})
        jobs.append({"kind": "translate", "id": "tr", "texts": texts, "src": src, "tgt": tgt,
                     "engine": eng, "hint": self.src.get("hint")})
        self._tgt_now = tgt
        self._busy(True)
        self.progress["value"] = 0
        self._flash("Downloading the model (one time) …" if need else "Starting …")
        app.ai.run(self, jobs)

    def _busy(self, b):
        self.running = b
        self.go.configure(text="■  Stop" if b else "Translate", style="Stop.TButton" if b else "Accent.TButton")

    def stop(self):
        if self.app.ai.owner is self:
            self.app.ai.stop()
        voice = getattr(self, "_voice_job", None) is not None
        self._voice_job = None
        self._busy(False)
        self.progress["value"] = 0
        self._flash("Stopped." if voice else "Stopped. What was translated so far is kept below.")

    def on_ai(self, kind, *a):
        if kind == "prog":
            jid, frac, text = a
            self.progress["value"] = frac or 0
            self._flash(text)
        elif kind == "tseg":
            _jid, i, text = a
            if i < len(self.trans):
                self.trans[i] = text
            if self._redraw is None:
                self._redraw = self.after(250, self._live)
        elif kind == "downloaded":
            self.app.refresh_models()
            self._flash("Model downloaded. Translating …")
        elif kind == "tdone":
            _jid, res, took = a
            self.trans = list(res["texts"])
            self.app.ai.release()
            self._busy(False)
            self.progress["value"] = 1.0
            tgt = self._tgt_now
            self.result = dict(self._trans_result_for(tgt), model=res["model"])
            self.render(keep=True)
            self._flash(f"Translated {sum(1 for t in self.trans if t)} sentences in {fmt_hms(took)} with "
                        f"{'the AI model (Qwen3)' if res['engine'] == 'ai' else 'NLLB-200'}. Click a sentence to "
                        "see its match; Export saves it.")
        elif kind == "voiced":
            _jid, info, took = a
            self.app.ai.release()
            self._busy(False)
            self.progress["value"] = 1.0
            try:
                self._voiced(info, took)
            finally:
                self._voice_job = None
        elif kind in ("fail", "died"):
            voice = getattr(self, "_voice_job", None) is not None
            msg = a[1] if kind == "fail" else ("The voice" if voice else "The translator") + " stopped unexpectedly."
            self.app.ai.stop()
            self._busy(False)
            self._voice_job = None
            self.progress["value"] = 0
            self._flash(("Reading aloud failed: " if voice else "Translation failed: ") + msg)

    def _trans_result_for(self, tgt):
        res = self.src["res"]
        segs = [dict(s, text=self.trans[i] or "", words=[]) for i, s in enumerate(res["segments"])]
        iso = "yue" if tgt == "yue_Hant" else langs.NLLB_TO_ISO.get(tgt, "en")
        return dict(res, segments=segs, task="translate", language=iso, _tgt=tgt, target_name=langs.name(tgt),
                    source_name=lang_name(res.get("spoken_language")) if res.get("spoken_language") else "")

    def _live(self):
        self._redraw = None
        if self.src:
            self.bot.show(self._trans_result(), self.app.v_ts.get())
            self.bot.text.see("end")

    # ------------------------------------------------------------ click, play, follow
    def _click(self, e):
        w = e.widget
        if self.src and self.src.get("editing"):
            return
        from .widgets import blank_click
        if blank_click(w, e):                 # empty space: clear the highlight
            self.now = None
            self.top.mark(None)
            self.bot.mark(None)
            return
        if w.tag_ranges("sel"):
            return
        p = self.top if w is self.top.text else self.bot
        i = p.seg_at(e)
        if i is None:
            return
        self.now = i
        self.top.mark(i)
        self.bot.mark(i)
        if self.src and not self.src["no_times"]:
            self._play(self.src["res"]["segments"][i]["start"])

    def _play(self, t):
        app = self.app
        item = self.src.get("item")
        if item not in app.rows:
            return
        if app.preview_item != item:
            app.tree.selection_set(item)
            app._preview(item)
        app.play_at(t)

    def play_pause(self):
        app = self.app
        if not self.src or self.src["no_times"] or self.src.get("item") not in app.rows:
            self._flash("Text has no recording: press  🔊 Read aloud  to hear it, then click any sentence.")
            return
        if app.preview_item != self.src["item"]:
            seg = self.src["res"]["segments"][self.now or 0]
            self._play(seg["start"])
        else:
            app.toggle_play()

    def _seek(self, t):
        """The slider was let go at t: play the imported recording from there (switching to it if needed)."""
        app = self.app
        if not self.src or self.src["no_times"] or self.src.get("item") not in app.rows:
            return True
        if app.preview_item != self.src["item"]:
            self._play(t)
            return True
        return False                     # the normal seek

    def follow(self, pos):
        """Called while the audio plays: highlight the matching pair."""
        if not self.src or self.src["no_times"] or self.src.get("item") != self.app.audio_item:
            return
        starts = [s["start"] for s in self.src["res"]["segments"]]
        i = bisect.bisect_right(starts, pos + 0.05) - 1
        if i >= 0 and i != self.now:
            self.now = i
            self.top.mark(i)
            self.bot.mark(i)

    # ------------------------------------------------------------ copy / export
    def copy(self, translated):
        if not self.src:
            return
        res = self.result if translated else self.src["res"]
        if translated and not res:
            if not any(self.trans):
                return
            res = self._trans_result()
        text = engine.to_text(res, self.app.v_ts.get())
        self.app.clipboard_clear()
        self.app.clipboard_append(text)
        self._flash(("Translation" if translated else "Original") + " copied to the clipboard.")

    def export(self):
        app = self.app
        if not self.result:
            self._flash("Translate something first, then Export.")
            return
        fmts = self._fmts()
        if not fmts:
            self._flash("Tick at least one format next to “Save as” (below).")
            return
        res = self.result
        skipped = []
        if res.get("no_times"):
            skipped = [f for f in fmts if f in ("srt", "vtt")]
            fmts = [f for f in fmts if f not in ("srt", "vtt")]
            if not fmts:
                self._flash("Pasted text has no timestamps, so it can't be saved as subtitles: tick PDF, TXT, "
                            "JSON or CSV.")
                return
        item = self.src.get("item")
        if item in app.rows and app.rows[item].get("voice"):
            item = None                     # a read-aloud recording lives in the app's folder: ask where
        if item in app.rows:
            folder = app._outdir_for(app.rows[item])
            stem = Path(app.rows[item]["file"]).stem
        else:
            folder = app._custom_outdir()
            if not folder:
                d = filedialog.askdirectory(parent=app, title="Save the translation in …")
                if not d:
                    return
                folder = Path(d)
            stem = Path(self.src.get("name") or "Text").stem
        stem = f"{stem}_{_short_code(res['_tgt'])}"
        out = {k: v for k, v in res.items() if not k.startswith("_")}
        out["segments"] = [{k: v for k, v in sg.items() if not k.startswith("_") and k != "para"}
                           for sg in res["segments"]]
        if not item:
            out["file"] = str(Path(folder) / f"{stem}.txt")
        ts = app.v_ts.get()

        def run():
            try:
                paths = engine.save(out, fmts, folder, timestamps=ts, stem=stem)
                msg = (f"Exported {len(paths)} file{'s' * (len(paths) != 1)} to {folder}" +
                       (f" (no {', '.join(x.upper() for x in skipped)}: pasted text has no timestamps)"
                        if skipped else ""))
                self.after(0, lambda: (self._flash(msg), open_path(paths[0], reveal=True) if paths else None))
            except Exception as e:
                log(f"translation export failed: {e}")
                self.after(0, lambda e=e: self._flash(f"Export problem: {e}"))
        threading.Thread(target=run, daemon=True).start()


# ============================================================================ Summary
class SummaryTab(ttk.Frame):
    ai_label = "Summary"

    def __init__(self, nb, app, tr_tab):
        super().__init__(nb, padding=(12, 10, 12, 12))
        self.app = app
        self.tr = tr_tab
        s = app.s
        self.running = False
        self.text = ""
        self.stem = "summary"
        self.src_item = None            # the Transcribe row the summary came from (for its folder)
        self.subtitle = ""
        self.v_from = tk.StringVar(value="transcript")
        lang = s.get("sum_lang", "eng_Latn")
        self.v_lang = tk.StringVar(value=langs.choice(lang if lang in langs.NLLB else "eng_Latn"))
        self.v_detail = tk.StringVar(value=DETAIL.get(s.get("sum_detail"), "Normal"))
        for v in (self.v_lang, self.v_detail):
            v.trace_add("write", lambda *_: self._save())

        r1 = ttk.Frame(self)
        r1.pack(fill="x")
        ttk.Label(r1, text="Summarise").pack(side="left", padx=(0, 8))
        self.rb_tr = ttk.Radiobutton(r1, text="Transcript", value="transcript", variable=self.v_from,
                                     command=self._source_ui)
        self.rb_tr.pack(side="left")
        self.rb_tl = ttk.Radiobutton(r1, text="Translation", value="translation", variable=self.v_from,
                                     command=self._source_ui)
        self.rb_tl.pack(side="left", padx=(10, 0))
        ttk.Radiobutton(r1, text="Pasted text", value="paste", variable=self.v_from,
                        command=self._source_ui).pack(side="left", padx=(10, 0))
        ttk.Label(r1, text="Write in").pack(side="left", padx=(20, 6))
        lb = ttk.Combobox(r1, textvariable=self.v_lang, values=TGT_CHOICES, width=26)
        lb.pack(side="left")
        searchable(lb, TGT_CHOICES, langs.choice("eng_Latn"))
        ttk.Label(r1, text="Length").pack(side="left", padx=(16, 6))
        db = ttk.Combobox(r1, textvariable=self.v_detail, values=list(DETAIL.values()), state="readonly", width=10)
        db.pack(side="left")
        db.bind("<<ComboboxSelected>>", lambda e: db.selection_clear())

        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=(10, 0))
        # bring the text in from the other tabs in one click (also when the box was edited or emptied)
        ttk.Button(r2, text="Import from Transcribe", command=lambda: self.import_from("transcript")).pack(
            side="left")
        ttk.Button(r2, text="Import from Translate", command=lambda: self.import_from("translation")).pack(
            side="left", padx=(6, 14))
        self.go = ttk.Button(r2, text="Summarise", style="Accent.TButton", command=self.summarize, width=12)
        self.go.pack(side="right")
        self.src_lbl = ttk.Label(r2, style="Muted.TLabel")
        self.src_lbl.pack(side="left", fill="x", expand=True)
        self.src_lbl.bind("<Configure>", lambda e: self.src_lbl.configure(wraplength=max(e.width - 4, 120)))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(10, 0))

        # the AI summary on top, the text it works from below; drag the divider between them
        split = ttk.PanedWindow(self, orient="vertical")
        split.pack(fill="both", expand=True, pady=(4, 0))
        self.out = _Pane(split, app, "Summary")
        self.paste = _Pane(split, app, "Text to summarise (type or paste here)")
        split.add(self.out, weight=3)
        split.add(self.paste, weight=1)
        theme.SashGrip(split)
        self._filled = None
        self.paste.text.configure(state="normal", cursor="xterm", height=8)
        # ask the AI about the text (or the summary): type, then Enter or the > button
        self.qa = []                    # [(question, answer)] asked here, shown under the summary
        self._partial = None
        ask = ttk.Frame(self.out)
        ask.pack(side="bottom", fill="x", pady=(8, 2))
        self.ask_btn = ttk.Button(ask, text="➤", style="Accent.TButton", width=3, command=self.ask)
        self.ask_btn.pack(side="right", padx=(8, 0))
        self.v_ask = tk.StringVar()
        self.ask_ent = ttk.Entry(ask, textvariable=self.v_ask)
        self.ask_ent.pack(side="left", fill="x", expand=True, ipady=3)
        self._ask_ph = False
        self.ask_ent.bind("<Return>", lambda e: (self.ask(), "break")[1])
        self.ask_ent.bind("<KP_Enter>", lambda e: (self.ask(), "break")[1])
        self.ask_ent.bind("<FocusIn>", lambda e: self._ask_hint(False))
        self.ask_ent.bind("<FocusOut>", lambda e: self._ask_hint(True))
        self._ask_hint(True)
        # the summary text box sits above the ask row (packed after it, so the row always stays in view)
        self.out.text.master.pack_forget()
        self.out.text.master.pack(fill="both", expand=True)
        ttk.Button(self.out.hdr, text="Copy", command=self.copy).pack(side="right")
        self.find = FindBar(self, [self.out.text])
        self.find.place_fn = lambda: self.find.pack(fill="x", pady=(8, 0), before=split)
        ttk.Button(self.out.hdr, text="Find", command=self.find.open, width=6).pack(side="right", padx=(0, 6))
        row, self._fmts = _format_row(self, app, "sum_formats", engine.SUMMARY_FORMATS, ["pdf", "txt"], self.export)
        self.fmt_row = row
        ttk.Label(self.out.hdr, text="Made offline by the Qwen3 AI model. Check important details against the "
                                     "transcript.", style="Small.TLabel").pack(side="right", padx=10)
        self.fmt_row.pack(fill="x", pady=(10, 0))
        self.status = ttk.Label(self, style="Muted.TLabel", wraplength=900, justify="left")
        self.status.pack(fill="x", pady=(8, 0))
        self.out.message("Choose what to summarise (it shows in the box below, where you can edit it) and press  "
                         "Summarise.  Drag the line between the two boxes to make either one bigger.\n\nYou get an overview, the key "
                         "points, key moments with their times (for transcripts) and any action items, in the "
                         "language you pick.")
        self._source_ui()

    ASK_HINT = "Ask the AI about this text, e.g. “What should we do next?”  (Enter or ➤ sends)"

    def _ask_hint(self, show):
        if show and not self.v_ask.get().strip() and self.focus_get() is not self.ask_ent:
            self.v_ask.set(self.ASK_HINT)
            self.ask_ent.configure(style="Hint.TEntry")
            self._ask_ph = True
        elif not show and self._ask_ph:
            self.v_ask.set("")
            self.ask_ent.configure(style="TEntry")
            self._ask_ph = False

    def ask(self):
        """Answer a question about the text being summarised (and the summary), under the summary."""
        app = self.app
        if self.running:
            return
        q = "" if self._ask_ph else self.v_ask.get().strip()
        if not q:
            self.ask_ent.focus_set()
            return
        text = self._box().strip()
        r = self._transcript() if self.v_from.get() == "transcript" and (self._filled or "").strip() else None
        if not text and not r and not self.text:
            self._flash("There's no text yet: pick a transcript or paste some text below, then ask.")
            return
        if not app.ai.free_for(self):
            self._flash(f"The {app.ai.label()} tab is still using the AI; nothing asked.")
            return
        from . import ai
        need = ensure_models(app, [ai.AI_KEY], "Asking the AI")
        if need is None:
            self._flash("Model not downloaded — nothing asked.")
            return
        if r is not None and text == (self._filled or "").strip():
            from .chat import build_ctx
            ctx = build_ctx(app, r)
        else:
            ctx = {"title": self.stem if self.stem != "summary" else "Text", "speakers": [], "translation": None,
                   "lines": [[None, "", ln] for ln in text.splitlines() if ln.strip()], "summary": None}
        if self.text:
            ctx["summary"] = engine.summary_plain(self.text)
        self.v_ask.set("")
        self._pending_q = q
        self._partial = ""
        self._render_qa()
        jobs = [{"kind": "download", "id": "ai-dl", "models": need}] if need else []
        jobs.append({"kind": "chat", "id": "sumask", "ctx": ctx, "history": self.qa[-4:], "question": q})
        self._busy(True)
        self.progress["value"] = 0.05
        self._flash("Downloading the AI model (one time) …" if need else "Thinking …")
        app.ai.run(self, jobs)

    def _render_qa(self):
        """The summary, then the questions asked here with their answers."""
        t = self.out.text
        if self.text:
            self._show(self.text)
        else:
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.configure(state="disabled")
        t.configure(state="normal")
        items = list(self.qa)
        if self._partial is not None:
            items.append((self._pending_q, (self._partial or "") + " ▍"))
        for q, a in items:
            if t.get("1.0", "end").strip():
                t.insert("end", "\n")
            t.insert("end", q + "\n", ("h",))
            for ln in (a or "").splitlines():
                s = ln.rstrip()
                if re.match(r"^\s*[-*•]\s+", s):
                    t.insert("end", "  •  " + re.sub(r"^\s*[-*•]\s+", "", s).replace("**", "") + "\n")
                else:
                    t.insert("end", re.sub(r"^#{1,4}\s*", "", s).replace("**", "") + "\n")
        t.configure(state="disabled")
        t.see("end")

    def _save(self):
        self.app.s["sum_lang"] = langs.from_choice(self.v_lang.get()) or "eng_Latn"
        self.app.s["sum_detail"] = next((k for k, v in DETAIL.items() if v == self.v_detail.get()), "normal")
        self.app._save()

    def recolor(self, p, dark):
        self.paste.recolor(p, dark)
        self.out.recolor(p, dark)
        ttk.Style(self).configure("Hint.TEntry", foreground=p["faint"])
        self.find.recolor(dark)

    def _transcript(self):
        app = self.app
        r = app.rows.get(app.preview_item) if app.preview_item else None
        if r and r.get("result"):
            return r
        done = [i for i in app.tree.get_children() if app.rows[i].get("result")]
        return app.rows[done[0]] if done else None

    def _source_text(self, f):
        """The text of the chosen source (transcript / translation), or None if there isn't one yet."""
        if f == "transcript":
            r = self._transcript()
            return engine.to_text(self.app._view(r["result"]), not r["result"].get("no_times")) if r else None
        if f == "translation":
            tr = self.tr.result
            return engine.to_text(tr, not tr.get("no_times")) if tr else None
        return None

    def _box(self):
        return self.paste.text.get("1.0", "end-1c")

    def _set_box(self, text):
        t = self.paste.text
        t.delete("1.0", "end")
        t.insert("1.0", text)
        t.edit_reset()

    def _source_ui(self, switched=True):
        """The text box is always there: it holds the transcript / translation (edit it, or type over it) or your
        own text."""
        f = self.v_from.get()
        prev = getattr(self, "_shown", None)
        if switched and prev == "paste":
            self._paste_text = self._box()                 # keep what was typed for Pasted text
        if f == "paste":
            if switched or prev != "paste":
                self._set_box(getattr(self, "_paste_text", ""))
            self._filled = None
            self.paste.title.configure(text="Text to summarise (type or paste here)")
            self.src_lbl.configure(text="Type or paste the text in the box below.")
        else:
            text = self._source_text(f)
            # refill unless the user changed the text that was put in for them
            if switched or self._box() == (getattr(self, "_filled", None) or ""):
                self._set_box(text or "")
                self._filled = text or ""
            if f == "transcript":
                r = self._transcript()
                self.src_lbl.configure(text=f"Transcript: {r['file'].name}" if r else
                                       "No transcript yet: type the text below, or transcribe a file first.")
                self.paste.title.configure(text="Transcript to summarise (edit it, or type your own text)")
            else:
                tr = self.tr.result
                self.src_lbl.configure(text=f"Translation ({tr.get('target_name')}) of "
                                            f"{Path(str(tr['file'])).name}" if tr else
                                       "No translation yet: type the text below, or translate something first.")
                self.paste.title.configure(text="Translation to summarise (edit it, or type your own text)")
        self._shown = f

    def refresh(self):
        self._source_ui(switched=False)

    def import_from(self, f):
        """Import from Transcribe / Translate: put that text in the box (replacing what's there)."""
        if self.running:
            return
        if self.v_from.get() == "paste":
            self._paste_text = self._box()
        self.v_from.set(f)
        self._shown = f
        text = self._source_text(f)
        self._set_box(text or "")
        self._filled = text or ""
        self._source_ui(switched=False)
        if not text:
            self._flash("Nothing to import yet: transcribe a file first." if f == "transcript" else
                        "Nothing to import yet: translate something on the Translate tab first.")
        else:
            self._flash("Imported. Edit the text below if you like, then press Summarise or ask a question.")

    def summarize(self):
        app = self.app
        if self.running:
            self.stop()
            return
        f = self.v_from.get()
        text = self._box().strip()             # whatever is in the box: the source as it is, edited, or typed
        times, self.stem, self.src_item = False, "Text", None
        if f == "transcript" and (self._filled or "").strip():
            r = self._transcript()
            if r:
                times = not r["result"].get("no_times") and bool(re.search(r"\(\d+:\d\d", text))
                self.stem = Path(r["file"]).stem
                self.src_item = next((i for i, x in app.rows.items() if x is r), None)
        elif f == "translation" and (self._filled or "").strip():
            tr = self.tr.result
            if tr:
                times = not tr.get("no_times") and bool(re.search(r"\(\d+:\d\d", text))
                self.stem = Path(str(tr["file"])).stem + f"_{_short_code(tr['_tgt'])}"
                self.src_item = self.tr.src.get("item") if self.tr.src else None
        if not text.strip():
            self._flash("There's no text to summarise.")
            return
        if not app.ai.free_for(self):
            self._flash(f"The {app.ai.label()} tab is still using the AI; nothing started.")
            return
        from . import ai
        need = ensure_models(app, [ai.AI_KEY], "The AI summary")
        if need is None:
            self._flash("Model not downloaded — nothing started.")
            return
        jobs = [{"kind": "download", "id": "ai-dl", "models": need}] if need else []
        lang = langs.from_choice(self.v_lang.get()) or "eng_Latn"
        detail = next((k for k, v in DETAIL.items() if v == self.v_detail.get()), "normal")
        jobs.append({"kind": "summarize", "id": "sum", "text": text, "lang": lang, "detail": detail, "times": times})
        self.text = ""
        self.subtitle = f"AI summary ({self.v_detail.get().lower()}) in {langs.name(lang)} · made offline by Qwen3"
        self.out.message("Reading …")
        self._busy(True)
        self.progress["value"] = 0
        self._flash("Downloading the AI model (one time) …" if need else "Starting …")
        if not app.has_gpu:
            self._flash("Starting … (no NVIDIA GPU, so this runs on the processor and can take a few minutes for "
                        "a long recording)")
        app.ai.run(self, jobs)

    def _busy(self, b):
        self.running = b
        self.go.configure(text="■  Stop" if b else "Summarise", style="Stop.TButton" if b else "Accent.TButton")
        if hasattr(self, "ask_btn"):
            self.ask_btn.configure(text="■" if b else "➤", style="Stop.TButton" if b else "Accent.TButton",
                                   command=self.stop if b else self.ask)

    def stop(self):
        if self.app.ai.owner is self:
            self.app.ai.stop()
        self._busy(False)
        self.progress["value"] = 0
        if self._partial is not None:
            self.qa.append((self._pending_q, (self._partial or "") + "\n(stopped)"))
            self._partial = None
            self._render_qa()
        self._flash("Stopped.")

    def _flash(self, msg):
        self.status.configure(text=msg)

    def on_ai(self, kind, *a):
        if kind == "prog":
            _jid, frac, text = a
            self.progress["value"] = frac or 0
            self._flash(text)
        elif kind == "downloaded":
            self.app.refresh_models()
            self._flash("AI model downloaded. Summarising …")
        elif kind == "stext":
            self._show(a[1])
        elif kind == "ctext":
            self._partial = a[1]
            self._render_qa()
        elif kind == "cdone":
            _jid, ans, took = a
            self.app.ai.release()
            self._busy(False)
            self.progress["value"] = 1.0
            self.qa.append((self._pending_q, ans))
            self._partial = None
            self._render_qa()
            self._flash(f"Answered in {took:.1f} s. Ask another question, or press Summarise for a summary.")
        elif kind == "sdone":
            _jid, text, took = a
            self.app.ai.release()
            self._busy(False)
            self.progress["value"] = 1.0
            self.text = text
            self.qa = []                       # a new summary starts a fresh set of questions
            self._show(text)
            self._flash(f"Summary ready ({fmt_hms(took)}). Copy it, or tick the formats below and Export.")
        elif kind in ("fail", "died"):
            msg = a[1] if kind == "fail" else "The AI model stopped unexpectedly."
            self.app.ai.stop()
            self._busy(False)
            self.progress["value"] = 0
            if self._partial is not None:
                self._partial = None
                self._render_qa()
                self._flash(f"Couldn't answer: {msg}")
            else:
                self._flash(f"Summary failed: {msg}")

    def _show(self, md):
        """Show the summary's simple markdown nicely: headings, bullets, no ** marks."""
        t = self.out.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for ln in md.splitlines():
            s = ln.rstrip()
            if re.match(r"^#{1,4}\s", s):
                t.insert("end", re.sub(r"^#{1,4}\s*", "", s).replace("**", "") + "\n", ("h",))
            elif re.match(r"^\s*[-*•]\s+", s):
                t.insert("end", "  •  " + re.sub(r"^\s*[-*•]\s+", "", s).replace("**", "") + "\n")
            else:
                t.insert("end", s.replace("**", "") + "\n")
        t.configure(state="disabled")

    def _plain(self):
        return engine.summary_plain(self.text)

    def copy(self):
        if not self.text:
            return
        self.app.clipboard_clear()
        self.app.clipboard_append(self._plain())
        self._flash("Summary copied to the clipboard.")

    def export(self):
        app = self.app
        if not self.text:
            self._flash("Make a summary first.")
            return
        fmts = self._fmts()
        if not fmts:
            self._flash("Tick at least one format next to “Save as” (below).")
            return
        if self.src_item in app.rows:
            folder = app._outdir_for(app.rows[self.src_item])
        else:
            folder = app._custom_outdir()
            if not folder:
                d = filedialog.askdirectory(parent=app, title="Save the summary in …")
                if not d:
                    return
                folder = Path(d)
        md, stem, title, sub = self.text, f"{self.stem}_summary", f"{self.stem} — summary", self.subtitle

        def run():
            try:
                paths = engine.save_summary(md, fmts, folder, stem, title, sub)
                msg = f"Exported {len(paths)} file{'s' * (len(paths) != 1)} to {folder}"
                self.after(0, lambda: (self._flash(msg), open_path(paths[0], reveal=True) if paths else None))
            except Exception as e:
                log(f"summary export failed: {e}")
                self.after(0, lambda e=e: self._flash(f"Export problem: {e}"))
        threading.Thread(target=run, daemon=True).start()
