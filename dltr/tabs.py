"""The Translate and Summary tabs. Both run their work in a background worker process (AiRunner), like the
transcriber, so the window never freezes and Stop works at once."""

import bisect
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine, langs, models, theme
from .common import APP_NAME, FORMATS, fmt_hms, lang_name, log, open_path
from .theme import UI
from .worker import Worker

AUTO = "Auto-detect"
SRC_CHOICES = [AUTO] + [langs.choice(c) for c in langs.NLLB]
TGT_CHOICES = [langs.choice(c) for c in langs.NLLB]
ENGINES = {"auto": "Auto (best for these languages)", "ai": "AI: Qwen3 (most natural)",
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
        ttk.Checkbutton(row, text=f.upper(), variable=vars_[f]).pack(side="left", padx=(0, 6))
    btn = ttk.Button(row, text="Export", style="Accent.TButton", command=export_cmd, width=10)
    btn.pack(side="right")
    ttk.Button(row, text="Save to…", command=app.choose_outdir).pack(side="right", padx=6)
    lbl = ttk.Label(row, style="Small.TLabel", text="Saves beside the recording (Transcripts folder), or in the "
                                                     "folder picked with Save to…")
    lbl.pack(side="right", padx=6)
    return row, lambda: [f for f, v in vars_.items() if v.get()]
NBSP = " "


def _searchable(box, choices):
    """Type in a language list to narrow it down; an unknown entry snaps back to the closest match."""
    def filt(e):
        if e.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        q = box.get().strip().lower()
        box.configure(values=[c for c in choices if q in c.lower()] if q else choices)

    def fix(_e=None):
        if box.get() not in choices:
            q = box.get().strip().lower()
            m = [c for c in choices if q and q in c.lower()]
            box.set(m[0] if m else choices[0])
        box.configure(values=choices)
        box.selection_clear()
    box.bind("<KeyRelease>", filt)
    box.bind("<FocusOut>", fix)
    box.bind("<<ComboboxSelected>>", fix)


def _short_code(code):
    return {"zho_Hans": "zh-Hans", "zho_Hant": "zh-Hant", "yue_Hant": "yue"}.get(code) or \
        langs.NLLB_TO_ISO.get(code) or code


split_text = engine.split_text


class AiRunner:
    """One background worker for translation, summaries and their model downloads (one job at a time)."""

    def __init__(self, app):
        self.app = app
        self.wk = None
        self.owner = None

    def busy(self):
        return self.owner is not None

    def run(self, owner, jobs):
        if self.wk is None or self.wk.proc is None or not self.wk.proc.is_alive():
            self.wk = Worker(lambda *a: self.app.events.put(("ai",) + a))
        self.owner = owner
        for j in jobs:
            self.wk.submit(j)

    def handle(self, kind, *a):
        if kind == "device" or self.owner is None:
            return
        self.owner.on_ai(kind, *a)

    def release(self):
        self.owner = None

    def stop(self):
        owner, self.owner = self.owner, None
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
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", height=6, font=(theme.TEXT, 11 + theme._S), relief="flat", padx=14,
                            pady=10, borderwidth=1, highlightthickness=1, spacing1=1, spacing3=1, cursor="arrow",
                            undo=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

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
        t.tag_raise("sel")

    def show(self, res, timestamps):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        ts = timestamps and not res.get("no_times")
        j = " " if ts else ("" if res.get("language") in engine.CJK else " ")
        idx = 0
        for pi, (name, para) in enumerate(engine.labelled(res)):
            if pi:
                t.insert("end", "\n\n")
            if name:
                t.insert("end", name + "\n", ("spk",))
            for k, s in enumerate(para):
                if k:
                    t.insert("end", j)
                tag = f"t{s.get('_i', idx)}"
                if ts:
                    t.insert("end", f"({fmt_hms(s['start'])}){NBSP}", ("ts", tag))
                txt = s["text"]
                t.insert("end", txt if txt else "…", (tag,) + (() if txt else ("hint",)))
                idx += 1
        t.configure(state="disabled")

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
        self.v_src = tk.StringVar(value=s.get("tr_src") if s.get("tr_src") in SRC_CHOICES else AUTO)
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
        _searchable(self.src_box, SRC_CHOICES)
        ttk.Label(r1, text="→  To").pack(side="left", padx=(12, 6))
        self.tgt_box = ttk.Combobox(r1, textvariable=self.v_tgt, values=TGT_CHOICES, width=30)
        self.tgt_box.pack(side="left")
        _searchable(self.tgt_box, TGT_CHOICES)
        ttk.Label(r1, text="Translator").pack(side="left", padx=(16, 6))
        eng = ttk.Combobox(r1, textvariable=self.v_eng, values=list(ENGINES.values()), state="readonly", width=32)
        eng.pack(side="left")
        eng.bind("<<ComboboxSelected>>", lambda e: eng.selection_clear())

        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=(10, 0))
        ttk.Button(r2, text="Import from Transcribe", command=self.import_transcript).pack(side="left")
        ttk.Button(r2, text="Paste text", command=self.paste_mode).pack(side="left", padx=6)
        ttk.Button(r2, text="Clear", command=self.clear).pack(side="left")
        self.go = ttk.Button(r2, text="Translate", style="Accent.TButton", command=self.translate, width=12)
        self.go.pack(side="right")
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
        ttk.Button(self.top.hdr, text="Copy", command=lambda: self.copy(False)).pack(side="right")
        ttk.Button(self.bot.hdr, text="Copy", command=lambda: self.copy(True)).pack(side="right")
        ttk.Checkbutton(self.bot.hdr, text="Show timestamps", variable=app.v_ts).pack(side="right", padx=10)
        for p in (self.top, self.bot):
            p.text.bind("<ButtonRelease-1>", self._click)
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
        s["tr_src"] = self.v_src.get() if self.v_src.get() in SRC_CHOICES else AUTO
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

    def _empty(self):
        self.top.message("Press  Import from Transcribe  to bring in the transcript you're looking at on the "
                         "Transcribe tab (with its timestamps and speakers),\nor  Paste text  to translate any "
                         "text.\n\nThen pick the languages above and press  Translate.")
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
    def import_transcript(self):
        app = self.app
        if self.running:
            return
        if app.editing:
            app.end_edit(True)
        item = app.preview_item
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
        self.src = {"kind": "import", "item": item, "file": r["file"], "no_times": False, "hint": hint,
                    "res": dict(meta, task="transcribe", segments=segs)}
        self.trans, self.result, self.now = [], None, None
        self.top.text.configure(cursor="hand2")
        self.top.title.configure(text=f"Original — {r['file'].name}")
        self.bot.title.configure(text="Translation")
        self.info.configure(text=f"{r['file'].name} · {lang_name(res.get('spoken_language'))} · {len(segs)} "
                                 f"sentences" + (f" · {len(res['speakers'])} speakers" if res.get('speakers') else ""))
        self.render(keep=False)
        self.bot.message("Pick the languages above and press  Translate.")
        self._flash("Imported. The original keeps its timestamps and speakers; the translation will too.")

    def paste_mode(self):
        if self.running:
            return
        self.src = {"kind": "paste", "item": None, "file": None, "no_times": True, "hint": None, "editing": True,
                    "res": {"file": "Pasted text", "segments": [], "no_times": True, "task": "transcribe"}}
        self.trans, self.result, self.now = [], None, None
        t = self.top.text
        t.configure(state="normal", cursor="xterm")
        t.delete("1.0", "end")
        try:
            clip = self.app.clipboard_get()
        except tk.TclError:
            clip = ""
        if clip.strip():
            t.insert("1.0", clip)
        t.focus_set()
        self.top.title.configure(text="Original — pasted text (type or paste here, Ctrl/Cmd+V)")
        self.bot.title.configure(text="Translation")
        self.bot.message("Pick the languages above and press  Translate.")
        self.info.configure(text="Pasted text")
        self._flash("Paste or type the text in the top box" + (" (I pasted what was on the clipboard)" if
                                                                 clip.strip() else "") + ", then press Translate.")

    def clear(self):
        if self.running:
            return
        self.src, self.trans, self.result, self.now = None, [], None, None
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
        if self.src["kind"] == "paste" and self.src.get("editing"):
            text = self.top.text.get("1.0", "end").strip()
            segs = split_text(text)
            if not segs:
                self._flash("The box is empty: paste or type some text first.")
                return
            for i, sg in enumerate(segs):
                sg["_i"] = i
            self.src["res"]["segments"] = segs
            self.src["editing"] = False
            self.top.text.configure(cursor="hand2")
            self.info.configure(text=f"Pasted text · {len(segs)} sentences")
        if app.ai.busy():
            self._flash("The Summary tab is busy; wait for it to finish (or stop it).")
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
        self._busy(False)
        self.progress["value"] = 0
        self._flash("Stopped. What was translated so far is kept below.")

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
        elif kind in ("fail", "died"):
            msg = a[1] if kind == "fail" else "The translator stopped unexpectedly."
            self.app.ai.stop()
            self._busy(False)
            self.progress["value"] = 0
            self._flash(f"Translation failed: {msg}")

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
        if w.tag_ranges("sel") or (self.src and self.src.get("editing")):
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
            self._flash("Playing works for an imported transcript (pasted text has no audio).")
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
            stem = "Pasted text"
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
                self.after(0, lambda: self._flash(f"Export problem: {e}"))
        threading.Thread(target=run, daemon=True).start()


# ============================================================================ Summary
class SummaryTab(ttk.Frame):
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
        _searchable(lb, TGT_CHOICES)
        ttk.Label(r1, text="Length").pack(side="left", padx=(16, 6))
        db = ttk.Combobox(r1, textvariable=self.v_detail, values=list(DETAIL.values()), state="readonly", width=10)
        db.pack(side="left")
        db.bind("<<ComboboxSelected>>", lambda e: db.selection_clear())

        r2 = ttk.Frame(self)
        r2.pack(fill="x", pady=(10, 0))
        self.src_lbl = ttk.Label(r2, style="Muted.TLabel")
        self.src_lbl.pack(side="left", fill="x", expand=True)
        self.go = ttk.Button(r2, text="Summarise", style="Accent.TButton", command=self.summarize, width=12)
        self.go.pack(side="right")
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(10, 0))

        self.paste = _Pane(self, app, "Text to summarise (paste or type here)")
        self.paste.text.configure(state="normal", cursor="xterm", height=8)
        self.out = _Pane(self, app, "Summary")
        self.out.pack(fill="both", expand=True)
        ttk.Button(self.out.hdr, text="Copy", command=self.copy).pack(side="right")
        row, self._fmts = _format_row(self, app, "sum_formats", engine.SUMMARY_FORMATS, ["pdf", "txt"], self.export)
        self.fmt_row = row
        ttk.Label(self.out.hdr, text="Made offline by the Qwen3 AI model. Check important details against the "
                                     "transcript.", style="Small.TLabel").pack(side="right", padx=10)
        self.fmt_row.pack(fill="x", pady=(10, 0))
        self.status = ttk.Label(self, style="Muted.TLabel", wraplength=900, justify="left")
        self.status.pack(fill="x", pady=(8, 0))
        self.out.message("Choose what to summarise above and press  Summarise.\n\nYou get an overview, the key "
                         "points, key moments with their times (for transcripts) and any action items, in the "
                         "language you pick.")
        self._source_ui()

    def _save(self):
        self.app.s["sum_lang"] = langs.from_choice(self.v_lang.get()) or "eng_Latn"
        self.app.s["sum_detail"] = next((k for k, v in DETAIL.items() if v == self.v_detail.get()), "normal")
        self.app._save()

    def recolor(self, p, dark):
        self.paste.recolor(p, dark)
        self.out.recolor(p, dark)

    def _transcript(self):
        app = self.app
        r = app.rows.get(app.preview_item) if app.preview_item else None
        if r and r.get("result"):
            return r
        done = [i for i in app.tree.get_children() if app.rows[i].get("result")]
        return app.rows[done[0]] if done else None

    def _source_ui(self):
        f = self.v_from.get()
        if f == "paste":
            self.paste.pack(fill="both", expand=False, before=self.out)
            self.src_lbl.configure(text="Paste or type the text in the box below.")
        else:
            self.paste.pack_forget()
            if f == "transcript":
                r = self._transcript()
                self.src_lbl.configure(text=f"Transcript: {r['file'].name}" if r else
                                       "No transcript yet: transcribe a file on the Transcribe tab first.")
            else:
                tr = self.tr.result
                self.src_lbl.configure(text=f"Translation ({tr.get('target_name')}) of "
                                            f"{Path(str(tr['file'])).name}" if tr else
                                       "No translation yet: translate something on the Translate tab first.")

    def refresh(self):
        self._source_ui()

    def summarize(self):
        app = self.app
        if self.running:
            self.stop()
            return
        f = self.v_from.get()
        if f == "paste":
            text = self.paste.text.get("1.0", "end").strip()
            times, self.stem, self.src_item = False, "Pasted text", None
        elif f == "transcript":
            r = self._transcript()
            if not r:
                self._flash("No transcript yet: transcribe a file on the Transcribe tab first.")
                return
            res = app._view(r["result"])
            text, times, self.stem = engine.to_text(res, True), True, Path(r["file"]).stem
            self.src_item = next((i for i, x in app.rows.items() if x is r), None)
        else:
            tr = self.tr.result
            if not tr:
                self._flash("No translation yet: translate something on the Translate tab first.")
                return
            text = engine.to_text(tr, not tr.get("no_times"))
            times = not tr.get("no_times")
            self.stem = Path(str(tr["file"])).stem + f"_{_short_code(tr['_tgt'])}"
            self.src_item = self.tr.src.get("item") if self.tr.src else None
        if not text.strip():
            self._flash("There's no text to summarise.")
            return
        if app.ai.busy():
            self._flash("The Translate tab is busy; wait for it to finish (or stop it).")
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

    def stop(self):
        if self.app.ai.owner is self:
            self.app.ai.stop()
        self._busy(False)
        self.progress["value"] = 0
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
        elif kind == "sdone":
            _jid, text, took = a
            self.app.ai.release()
            self._busy(False)
            self.progress["value"] = 1.0
            self.text = text
            self._show(text)
            self._flash(f"Summary ready ({fmt_hms(took)}). Copy it, or tick the formats below and Export.")
        elif kind in ("fail", "died"):
            msg = a[1] if kind == "fail" else "The AI model stopped unexpectedly."
            self.app.ai.stop()
            self._busy(False)
            self.progress["value"] = 0
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
                self.after(0, lambda: self._flash(f"Export problem: {e}"))
        threading.Thread(target=run, daemon=True).start()
