"""The Chat tab (ask the AI anything about a transcribed recording, with clickable times, three suggested
follow-up questions and a microphone button), the Speakers window (rename, male / female voice) and the
automatic speaker naming after a transcription (only names the recording actually says)."""

import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import engine, models, theme
from .common import fmt_hms, log
from .tabs import _Pane, ensure_models
from .theme import UI

STARTERS = ["Summarise the main points in a few bullets", "What decisions or tasks were agreed?",
            "Who speaks the most, and about what?"]
TIME = re.compile(r"\[(\d{1,2}(?::\d{2}){1,2})\](?:\s*[-–]\s*\[(\d{1,2}(?::\d{2}){1,2})\])?")


def _secs(s):
    t = 0
    for part in s.split(":"):
        t = t * 60 + int(part)
    return t


def ai_ready():
    from . import ai
    return models.installed(ai.AI_KEY)


def item_of(app, r):
    return next((i for i, x in app.rows.items() if x is r), None)


def build_ctx(app, r):
    """What the AI gets: the transcript lines (time, speaker, text), the speakers with their voices, and the
    summary / translation of this same recording if they were made."""
    res = r["result"]
    names = res.get("speakers") or []
    item = item_of(app, r)
    ctx = {"title": r["file"].name,
           "lines": [[None if res.get("no_times") else s["start"], engine.speaker_name(res, s) or "", s["text"]]
                     for s in res["segments"]],
           "speakers": [{"name": n, "voice": engine.voice_of(res, i)} for i, n in enumerate(names)],
           "summary": None, "translation": None}
    st = getattr(app, "sum_tab", None)
    if st is not None and st.text and st.src_item == item:
        ctx["summary"] = engine.summary_plain(st.text)
    tt = getattr(app, "trans_tab", None)
    if tt is not None and tt.result and tt.src and tt.src.get("item") == item:
        ctx["translation"] = engine.to_text(tt.result, True)
    return ctx


# ============================================================================ automatic speaker names
class NameFinder:
    """After a transcription with speakers: if the AI model is on this PC, ask it for the speakers' real names
    and use them, but only names the recording says ("I'm Sarah"). Runs when the AI worker is free."""
    ai_label = "speaker naming"

    def ai_cancelled(self):
        """Something the user started needed the AI: try this recording again once it's free."""
        if self.cur is not None and self.cur not in self.todo:
            self.todo.insert(0, self.cur)
        self.cur = None
        self.app.after(3000, self._next)

    def __init__(self, app):
        self.app = app
        self.todo = []
        self.cur = None
        self.on_result = None          # set by the Speakers window while it waits for a manual search

    def queue(self, item, manual=False):
        if item not in self.todo and item != self.cur:
            self.todo.append(item)
        self._next(manual)

    def _next(self, manual=False):
        app = self.app
        if self.cur is not None or not self.todo:
            return
        if app.ai.busy():
            app.after(3000, self._next)
            return
        item = self.todo.pop(0)
        r = app.rows.get(item)
        if not r or not r.get("result") or not r["result"].get("speakers") or not ai_ready():
            return self._next()
        self.cur = item
        app.ai.run(self, [{"kind": "names", "id": "names", "ctx": build_ctx(app, r)}])

    def on_ai(self, kind, *a):
        app = self.app
        item, self.cur = self.cur, None
        if kind == "names":
            app.ai.release()
            found = a[1]
            r = app.rows.get(item)
            done = []
            if r and r.get("result"):
                spk = r["result"].get("speakers") or []
                for k, name in found.items():
                    k = int(k)
                    if k < len(spk) and re.fullmatch(r"Speaker \d+", spk[k]) and name not in spk:
                        spk[k] = name
                        done.append(name)
                if done:
                    r["dirty"] = True
                    if item == app.preview_item:
                        app._render(keep_view=True)
                    app._flash(f"Speakers named from the recording: {', '.join(done)}  "
                               "(right-click a name in the transcript, or use Speakers…, to change it)", 12)
            if self.on_result:
                self.on_result(item, found)
            app.after(200, self._next)
        elif kind in ("fail", "died"):
            app.ai.stop()
            if self.on_result:
                self.on_result(item, None)
            app.after(200, self._next)


# ============================================================================ Speakers window
class SpeakersWindow:
    def __init__(self, app, item):
        self.app, self.item = app, item
        r = app.rows[item]
        res = r["result"]
        self.names = res.setdefault("speakers", [])
        w = self.win = tk.Toplevel(app)
        w.title("Speakers")
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        w.resizable(False, False)
        f = ttk.Frame(w, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Speakers in {r['file'].name}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left", text=(
            "Rename a speaker and the new name is used everywhere: the transcript, exports and the Chat tab. "
            "Male / female is recognised from the voice (about 9 in 10 right on clear audio).")).pack(
            anchor="w", pady=(4, 10))
        grid = ttk.Frame(f)
        grid.pack(fill="x")
        self.vars = []
        for k, n in enumerate(self.names):
            v = engine.voice_of(res, k)
            ttk.Label(grid, text=f"Speaker {k + 1}", style="Muted.TLabel").grid(row=k, column=0, sticky="w",
                                                                              padx=(0, 10), pady=3)
            var = tk.StringVar(value=n)
            ttk.Entry(grid, textvariable=var, width=28).grid(row=k, column=1, sticky="w", pady=3)
            ttk.Label(grid, text={"female": "♀  Female voice", "male": "♂  Male voice"}.get(v, "Voice not sure"),
                      style="TLabel" if v else "Muted.TLabel").grid(row=k, column=2, sticky="w", padx=(12, 0))
            self.vars.append(var)
        if not self.names:
            ttk.Label(grid, text="No speakers were detected. Tick “Speakers (who said what)” and transcribe again.",
                      style="Muted.TLabel").grid(row=0, column=0)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(14, 0))
        self.find_btn = ttk.Button(row, text="Find names in the recording (AI)", command=self.find)
        self.find_btn.pack(side="left")
        ttk.Button(row, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(row, text="Save", style="Accent.TButton", command=self.save).pack(side="right", padx=6)
        self.status = ttk.Label(f, style="Muted.TLabel", wraplength=520, justify="left")
        self.status.pack(anchor="w", pady=(8, 0))
        if not self.names:
            self.find_btn.configure(state="disabled")
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<Return>", lambda e: self.save())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        w.geometry(f"+{max(x, 0)}+{max(app.winfo_rooty() + 120, 0)}")

    def find(self):
        app = self.app
        if not ai_ready():
            self.status.configure(text="This needs the Qwen3 AI model (4 GB). Download it in Models (top bar), or "
                                       "ask something on the Chat tab, then try again.")
            return
        self.find_btn.configure(state="disabled")
        self.status.configure(text="Reading the transcript for names …")
        app.names.on_result = self._found
        app.names.queue(self.item, manual=True)

    def _found(self, item, found):
        self.app.names.on_result = None
        if item != self.item or not self.win.winfo_exists():
            return
        self.find_btn.configure(state="normal")
        if found is None:
            self.status.configure(text="Couldn't read the transcript (AI model problem).")
            return
        got = []
        for k, name in found.items():
            k = int(k)
            if k < len(self.vars):
                self.vars[k].set(name)
                got.append(f"Speaker {k + 1} → {name}")
        self.status.configure(text=("Found: " + ", ".join(got) + ". Press Save to keep them.") if got else
                              "No names are said in this recording, so the speakers keep their labels.")

    def save(self):
        changed = False
        for k, var in enumerate(self.vars):
            v = var.get().strip() or f"Speaker {k + 1}"
            if v != self.names[k]:
                self.names[k] = v
                changed = True
        if changed:
            r = self.app.rows.get(self.item)
            if r:
                r["dirty"] = r["edited"] = True
                self.app._set(self.item, status="✏️ Edited")
            if self.item == self.app.preview_item:
                self.app._render(keep_view=True)
        self.close()

    def close(self):
        if self.app.names.on_result == self._found:
            self.app.names.on_result = None
        self.win.destroy()


# ============================================================================ Chat tab
class ChatTab(ttk.Frame):
    ai_label = "Chat"

    def ai_cancelled(self):
        self._sugg_for = None            # the suggestions were dropped to make room; fine

    def __init__(self, nb, app):
        super().__init__(nb, padding=(12, 10, 12, 12))
        self.app = app
        self.item = None             # the Transcribe row being chatted about
        self.running = False
        self.pending_q = None
        self.partial = ""
        self.times = {}              # tag -> seconds (clickable times in answers)
        self.rec = None              # microphone recording in progress
        self._sugg_for = None

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.src_lbl = ttk.Label(top, style="Muted.TLabel")
        self.src_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="New chat", command=self.new_chat).pack(side="right")
        ttk.Button(top, text="Speakers…", command=self.speakers).pack(side="right", padx=6)
        ttk.Button(top, text="📎  Add file…", command=self.add_file).pack(side="right")
        self._await = None             # a recording added here that's being transcribed

        self.pane = _Pane(self, app, "Chat")
        self.pane.pack(fill="both", expand=True)
        ttk.Label(self.pane.hdr, style="Small.TLabel", text="Answers come from the offline Qwen3 AI. Click a "
                  "[time] to play that moment. Check important details in the transcript.").pack(side="right")
        t = self.pane.text
        t.bind("<Button-1>", self._click)
        t.tag_bind("time", "<Enter>", lambda e: t.configure(cursor="hand2"))
        t.tag_bind("time", "<Leave>", lambda e: t.configure(cursor="arrow"))

        self.sugg = ttk.Frame(self)
        self.sugg.pack(fill="x", pady=(8, 0))

        box = ttk.Frame(self)
        box.pack(fill="x", pady=(8, 0))
        self.ask = tk.Text(box, height=3, wrap="word", font=(theme.TEXT, 11 + theme._S), relief="flat", padx=10,
                           pady=8, borderwidth=1, highlightthickness=1, undo=True)
        self.ask.pack(side="left", fill="x", expand=True)
        btns = ttk.Frame(box)
        btns.pack(side="right", fill="y", padx=(8, 0))
        self.send_btn = ttk.Button(btns, text="Send", style="Accent.TButton", command=self.send, width=10)
        self.send_btn.pack(fill="x")
        self.mic_btn = ttk.Button(btns, text="🎤  Speak", command=self.mic, width=10)
        self.mic_btn.pack(fill="x", pady=(6, 0))
        self.mic_pick = ttk.Button(btns, text="Mic ▾", command=self._mic_menu, width=10)
        self.mic_pick.pack(fill="x", pady=(4, 0))
        self._ph = False
        self.ask.bind("<FocusIn>", lambda e: self._ph_hide())
        self.ask.bind("<FocusOut>", lambda e: self._ph_show())
        self.ask.bind("<Return>", lambda e: (self.send(), "break")[1])
        self.ask.bind("<Shift-Return>", lambda e: None)
        self.ask.bind("<KP_Enter>", lambda e: (self.send(), "break")[1])

        bar = app.player_bar(self, self.play_pause, self._seek)
        bar.frame.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(8, 0))
        self.status = ttk.Label(self, style="Muted.TLabel", wraplength=900, justify="left")
        self.status.pack(fill="x", pady=(6, 0))
        self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=max(e.width - 4, 100)))
        self._ph_show()
        self.refresh()

    # ------------------------------------------------------------ the hint in the empty question box
    PH = "Ask anything about the recording or document …   (Enter sends · Shift+Enter for a new line · 🎤 Speak to say it)"

    def _ph_show(self):
        if not self._ph and not self.ask.get("1.0", "end").strip() and self.focus_get() is not self.ask:
            self.ask.insert("1.0", self.PH, ("ph",))
            self._ph = True

    def _ph_hide(self):
        if self._ph:
            self.ask.delete("1.0", "end")
            self._ph = False

    def _question(self):
        return "" if self._ph else self.ask.get("1.0", "end")

    # ------------------------------------------------------------ add a file to chat about
    def add_file(self):
        from tkinter import filedialog
        from .common import MEDIA_EXT
        from .tabs import TranslateTab, read_text_file
        app = self.app
        docs = " ".join(f"*{x}" for x in TranslateTab.TEXT_EXT)
        media = " ".join(f"*.{x}" for x in sorted(MEDIA_EXT))
        f = filedialog.askopenfilename(parent=app, title="Add a document, audio or video file to chat about",
                                       filetypes=[("Documents, audio or video", docs + " " + media),
                                                  ("Documents (TXT, PDF, Word, SRT, VTT)", docs),
                                                  ("Audio & video", media), ("All files", "*.*")])
        if not f:
            return
        p = Path(f)
        if p.suffix.lower() in TranslateTab.TEXT_EXT:
            try:
                text = read_text_file(p)
            except Exception as e:
                self._flash(f"Couldn't read {p.name}: {e}")
                return
            if not text.strip():
                self._flash(f"{p.name} has no text in it.")
                return
            item = app.add_doc(p, text)
            self._switch_to(item)
            self._flash(f"Added {p.name}. Ask anything about it.")
            return
        item = next((i for i, r in app.rows.items() if Path(r["file"]) == p), None)
        if item is None:
            app.add([str(p)])
            item = next((i for i, r in app.rows.items() if Path(r["file"]) == p), None)
            if item is None:
                self._flash(f"{p.name} isn't a file this app can read.")
                return
        r = app.rows[item]
        if r.get("result") and r["status"] == "DONE":
            self._switch_to(item)
            return
        self._await = item
        app.tree.selection_set(item)
        if r["status"] in ("FAIL", "SKIP"):
            app._set_status(item, "WAIT")
        if not app.running:
            app.start()
        self.src_lbl.configure(text=f"Transcribing {p.name} … (you can ask about it as soon as it's done)")
        self._flash(f"Transcribing {p.name} first, then it's ready to chat about.")

    def _switch_to(self, item):
        app = self.app
        if app.editing:
            app.end_edit(True)
        app.tree.selection_set(item)
        app.tree.see(item)
        app._preview(item)
        self.refresh()
        self.ask.focus_set()

    def on_transcribed(self, item, ok=True):
        if item != self._await:
            return
        self._await = None
        if ok:
            self._switch_to(item)
            self._flash(f"{self.app.rows[item]['file'].name} is transcribed. Ask anything about it.")
        else:
            self._flash("The transcription didn't finish (see the Transcribe tab).")
            self.refresh()

    # ------------------------------------------------------------ look
    def recolor(self, p, dark):
        self.pane.recolor(p, dark)
        t = self.pane.text
        t.tag_configure("you", foreground=p["accent"], font=(UI, 10 + theme._S, "bold"), spacing1=12)
        t.tag_configure("bot", foreground=p["muted"], font=(UI, 10 + theme._S, "bold"), spacing1=12)
        t.tag_configure("q", foreground=p["text"], lmargin1=4, lmargin2=4)
        t.tag_configure("a", lmargin1=4, lmargin2=4)
        t.tag_configure("li", lmargin1=12, lmargin2=28)
        t.tag_configure("b", font=(theme.TEXT, 11 + theme._S, "bold"))
        t.tag_configure("time", foreground=p["accent"], underline=True)
        t.tag_configure("think", foreground=p["faint"])
        self.ask.tag_configure("ph", foreground=p["faint"])
        sel = p.get("select", "#cce0ff")
        self.ask.configure(bg=p["field"], fg=p["text"], insertbackground=p["text"], highlightbackground=p["line"],
                           highlightcolor=p["accent"], selectbackground=sel, inactiveselectbackground=sel)
        ttk.Style(self).configure("Sugg.TLabel", foreground=p["faint"], font=(UI, 10 + theme._S))
        ttk.Style(self).configure("SuggHot.TLabel", foreground=p["accent"], font=(UI, 10 + theme._S))

    def _flash(self, msg):
        self.status.configure(text=msg)

    # ------------------------------------------------------------ which recording
    def _row(self):
        app = self.app
        r = app.rows.get(app.preview_item) if app.preview_item else None
        if r and r.get("result"):
            return app.preview_item, r
        done = [i for i in app.tree.get_children() if app.rows[i].get("result")]
        return (done[0], app.rows[done[0]]) if done else (None, None)

    def refresh(self):
        """Called when the tab is shown: follow the recording open on the Transcribe tab."""
        if self.running:
            return
        item, r = self._row()
        if item != self.item:
            self.item = item
            self.redraw()
        if not r:
            self.src_lbl.configure(text="No transcript yet: transcribe a file on the Transcribe tab, then ask "
                                        "anything about it here.")
            self._show_sugg([])
            return
        res = r["result"]
        n = len(res.get("speakers") or [])
        self.src_lbl.configure(text=f"Chatting about: {r['file'].name}  ·  " + (
            "document" if res.get("no_times") else fmt_hms(res.get('duration') or 0))
                                    + (f"  ·  {n} speaker{'s' * (n != 1)}" if n else ""))
        st = r.setdefault("chat", {"hist": [], "sugg": None})
        if st["sugg"] is None:
            self._show_sugg(STARTERS)
            if ai_ready() and not self.app.ai.busy():
                self._ask_suggestions()
        else:
            self._show_sugg(st["sugg"])

    def _state(self):
        r = self.app.rows.get(self.item)
        return r.setdefault("chat", {"hist": [], "sugg": None}) if r else None

    # ------------------------------------------------------------ drawing
    def redraw(self):
        t = self.pane.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        self.times = {}
        st = self._state()
        if not st or not st["hist"]:
            t.insert("end", "Ask anything about the recording, for example:\n\n"
                            "  •  At which point does a woman speak?\n"
                            "  •  When do they talk about the budget?\n"
                            "  •  What did Sarah promise to do, and by when?\n"
                            "  •  Explain the part about the launch date in simple words.\n\n"
                            "Type below, press 🎤 Speak to say it, or click a suggestion.", ("hint",))
        else:
            for q, a in st["hist"]:
                self._put_q(q)
                self._put_a(a)
        t.configure(state="disabled")
        t.see("end")

    def _put_q(self, q):
        t = self.pane.text
        if t.get("1.0", "end").strip():
            t.insert("end", "\n")
        t.insert("end", "You\n", ("you",))
        t.insert("end", q + "\n", ("q",))

    def _put_a(self, a, live=False):
        t = self.pane.text
        t.insert("end", "AI\n", ("bot",))
        for ln in (a or "").strip().splitlines():
            s = ln.rstrip()
            tags = ("a",)
            m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", s)
            if m:
                bullet = "•  " if not re.match(r"^\s*\d", s) else s[m.start():m.end()].strip() + "  "
                t.insert("end", bullet, ("a", "li"))
                s, tags = s[m.end():], ("a", "li")
            s = re.sub(r"^#{1,4}\s*", "", s)
            self._inline(s, tags)
            t.insert("end", "\n", tags)
        if live:
            t.insert("end", "▍", ("think",))

    def _inline(self, s, tags):
        """**bold** and [m:ss] links."""
        for part in re.split(r"(\*\*[^*]+\*\*)", s):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                self._times(part[2:-2], tags + ("b",))
            else:
                self._times(part, tags)

    def _times(self, s, tags):
        t = self.pane.text
        pos = 0
        for m in TIME.finditer(s):
            if m.start() > pos:
                t.insert("end", s[pos:m.start()], tags)
            tag = f"tm{len(self.times)}"
            self.times[tag] = _secs(m.group(1))
            t.insert("end", m.group(0), tags + ("time", tag))
            pos = m.end()
        if pos < len(s):
            t.insert("end", s[pos:], tags)

    def _live(self, text):
        """Redraw the answer being written (replace everything after the last question)."""
        t = self.pane.text
        t.configure(state="normal")
        try:
            t.delete("live.first", "end-1c")     # not "end": Tk would also eat the newline before it
        except tk.TclError:
            pass
        start = t.index("end-1c")
        self._put_a(text, live=True)
        t.tag_add("live", start, "end")
        t.configure(state="disabled")
        t.see("end")

    # ------------------------------------------------------------ suggestions
    def _show_sugg(self, qs):
        for c in self.sugg.winfo_children():
            c.destroy()
        if not qs:
            return
        ttk.Label(self.sugg, text="Suggestions", style="Small.TLabel").pack(anchor="w")
        for q in qs:
            lb = ttk.Label(self.sugg, text="›  " + q, style="Sugg.TLabel", cursor="hand2")
            lb.pack(anchor="w", pady=(2, 0))
            lb.bind("<Button-1>", lambda e, q=q: self.send(q))
            lb.bind("<Enter>", lambda e, w=lb: w.configure(style="SuggHot.TLabel"))
            lb.bind("<Leave>", lambda e, w=lb: w.configure(style="Sugg.TLabel"))

    def _ask_suggestions(self):
        r = self.app.rows.get(self.item)
        st = self._state()
        if not r or st is None or self.app.ai.busy():
            return
        self._sugg_for = self.item
        self.app.ai.run(self, [{"kind": "suggest", "id": "sugg", "ctx": build_ctx(self.app, r),
                                "history": st["hist"][-3:]}])

    # ------------------------------------------------------------ asking
    def send(self, q=None):
        app = self.app
        if self.running:
            return
        if self.rec is not None:
            self._mic_stop()
            return
        q = (q if q is not None else self._question()).strip()
        if not q:
            return
        item, r = self._row()
        if not r:
            self._flash("Transcribe a file on the Transcribe tab first.")
            return
        if item != self.item:
            self.item = item
            self.redraw()
        if not app.ai.free_for(self):
            self._flash(f"The {app.ai.label()} tab is still using the AI; nothing asked.")
            return
        from . import ai
        need = ensure_models(app, [ai.AI_KEY], "Chat")
        if need is None:
            self._flash("Model not downloaded — nothing asked.")
            return
        if app.ai.owner is self:           # cancel a suggestion run still going
            app.ai.stop()
        st = self._state()
        if not st["hist"]:
            t = self.pane.text
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.configure(state="disabled")
        self.ask.delete("1.0", "end")
        t = self.pane.text
        t.configure(state="normal")
        self._put_q(q)
        t.configure(state="disabled")
        self._live("")
        self._show_sugg([])
        self.pending_q = q
        self.partial = ""
        self.running = True
        self.send_btn.configure(text="■  Stop", style="Stop.TButton", command=self.stop)
        self.progress["value"] = 0.05
        jobs = [{"kind": "download", "id": "ai-dl", "models": need}] if need else []
        jobs.append({"kind": "chat", "id": "chat", "ctx": build_ctx(app, r), "history": st["hist"][-6:],
                     "question": q})
        self._flash("Downloading the AI model (one time) …" if need else "Thinking …")
        app.ai.run(self, jobs)

    def stop(self):
        if self.app.ai.owner is self:
            self.app.ai.stop()
        self._done(self.partial + ("\n\n(stopped)" if self.partial else "(stopped)"))
        self._flash("Stopped.")

    def _done(self, answer):
        st = self._state()
        if st is not None and self.pending_q:
            st["hist"].append((self.pending_q, answer))
        self.pending_q = None
        self.running = False
        self.send_btn.configure(text="Send", style="Accent.TButton", command=self.send)
        self.progress["value"] = 1.0 if answer else 0
        self.redraw()

    def new_chat(self):
        if self.running:
            self.stop()
        st = self._state()
        if st is not None:
            st["hist"] = []
            st["sugg"] = None
        self.redraw()
        self.refresh()

    def speakers(self):
        item, r = self._row()
        if not r:
            self._flash("Transcribe a file first.")
            return
        SpeakersWindow(self.app, item)

    # ------------------------------------------------------------ AI events
    def on_ai(self, kind, *a):
        app = self.app
        if kind == "prog":
            _jid, frac, text = a
            if self.running:
                self.progress["value"] = max(0.05, frac or 0)
            self._flash(text)
        elif kind == "downloaded":
            app.refresh_models()
            self._flash("AI model downloaded. Thinking …")
        elif kind == "ctext":
            self.partial = a[1]
            self._live(self.partial)
        elif kind == "cdone":
            _jid, ans, took = a
            app.ai.release()
            self._done(ans)
            self._flash(f"Answered in {took:.1f} s.")
            self._ask_suggestions()
        elif kind == "sugg":
            app.ai.release()
            qs = a[1] or []
            r = app.rows.get(self._sugg_for)
            if r is not None and qs:
                r.setdefault("chat", {"hist": [], "sugg": None})["sugg"] = qs
            if self._sugg_for == self.item and qs:
                self._show_sugg(qs)
        elif kind == "dictated":
            app.ai.release()
            text = a[1]
            self.mic_btn.configure(text="🎤  Speak", state="normal")
            if text:
                self._ph_hide()
                cur = self.ask.get("1.0", "end").strip()
                self.ask.insert("end", (" " if cur else "") + text)
                self.ask.focus_set()
                self._flash("Check the text, edit it if needed, then press Send (or Enter).")
            else:
                self._flash("Didn't catch any speech. Press 🎤 Speak and try again.")
            self.progress["value"] = 0
        elif kind in ("fail", "died"):
            msg = a[1] if kind == "fail" else "The AI model stopped unexpectedly."
            app.ai.stop()
            self.mic_btn.configure(text="🎤  Speak", state="normal")
            if self.running:
                self._done(self.partial or f"(Couldn't answer: {msg})")
            self._flash(f"Problem: {msg}")

    # ------------------------------------------------------------ microphone
    @staticmethod
    def _mics():
        """(index, name) of the input devices, once each (only the system's main sound driver; the others list
        the same microphones again), plus the index of the system default."""
        import sounddevice as sd
        devs = sd.query_devices()
        try:
            dflt = sd.default.device[0]
        except Exception:
            dflt = -1
        host = devs[dflt]["hostapi"] if isinstance(dflt, int) and 0 <= dflt < len(devs) else 0
        out, seen = [], set()
        for i, d in enumerate(devs):
            n = d["name"].strip()
            if (d["hostapi"] == host and d.get("max_input_channels", 0) > 0 and n not in seen
                    and "sound mapper" not in n.lower() and "primary sound capture" not in n.lower()):
                seen.add(n)
                out.append((i, n))
        return out, dflt

    def _mic_device(self):
        """Index of the chosen microphone, or None for the system default (also when it's unplugged)."""
        want = self.app.s.get("mic") or ""
        if not want:
            return None
        try:
            mics, _ = self._mics()
        except Exception:
            return None
        return next((i for i, n in mics if n == want), None)

    def _mic_menu(self):
        try:
            mics, dflt = self._mics()
        except Exception as e:
            self._flash(f"The microphone isn't available: {e}")
            return
        cur = self.app.s.get("mic") or ""
        if cur and cur not in [n for _i, n in mics]:
            cur = ""                                       # chosen one isn't plugged in now
        p = self.app.pal
        m = tk.Menu(self, tearoff=0, bg=p["panel"], fg=p["text"], activebackground=p["accent"],
                    activeforeground="#ffffff")
        self._mic_var = tk.StringVar(value=cur)
        dname = next((n for i, n in mics if i == dflt), "")
        m.add_radiobutton(label="System default" + (f"  ({dname})" if dname else ""), value="",
                          variable=self._mic_var, command=lambda: self._set_mic(""))
        m.add_separator()
        for _i, n in mics:
            m.add_radiobutton(label=n, value=n, variable=self._mic_var, command=lambda n=n: self._set_mic(n))
        if not mics:
            m.add_command(label="No microphone found", state="disabled")
        b = self.mic_pick
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _set_mic(self, name):
        self.app.s["mic"] = name
        self.app._save()
        self._flash(f"Microphone: {name or 'system default'}")

    def mic(self):
        if self.rec is not None:
            self._mic_stop()
            return
        try:
            import numpy as np  # noqa: F401
            import sounddevice as sd
        except Exception as e:
            self._flash(f"The microphone isn't available: {e}")
            return
        frames = []

        def cb(indata, _n, _t, _status):
            frames.append(indata[:, 0].copy())
        dev = self._mic_device()
        rate = 16000
        try:
            try:
                stream = sd.InputStream(device=dev, samplerate=16000, channels=1, dtype="float32", callback=cb)
            except Exception:                              # mic can't do 16 kHz itself: record at its own rate
                rate = int(sd.query_devices(dev, "input")["default_samplerate"])
                stream = sd.InputStream(device=dev, samplerate=rate, channels=1, dtype="float32", callback=cb)
            stream.start()
        except Exception as e:
            log(f"mic: {e!r}")
            self._flash(f"Couldn't open the microphone: {e}. Check it's plugged in and allowed in the system's "
                        "privacy settings.")
            return
        self.rec = (stream, frames, rate)
        self.mic_btn.configure(text="■  Done")
        self._flash("Listening … speak now, then press ■ Done (or Enter).")
        self._mic_level()

    def _mic_level(self):
        if self.rec is None:
            return
        import numpy as np
        frames = self.rec[1]
        if frames:
            lvl = float(np.abs(frames[-1]).max()) if len(frames[-1]) else 0.0
            secs = sum(len(f) for f in frames) / self.rec[2]
            self.progress["value"] = min(1.0, lvl * 3)
            self._flash(f"Listening … {secs:.0f} s  (press ■ Done when you've finished)")
        self.after(150, self._mic_level)

    def _mic_stop(self):
        import numpy as np
        stream, frames, rate = self.rec
        self.rec = None
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        self.progress["value"] = 0
        audio = np.concatenate(frames) if frames else np.zeros(0, np.float32)
        if rate != 16000 and len(audio):
            n = int(len(audio) * 16000 / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        if len(audio) < 8000:
            self.mic_btn.configure(text="🎤  Speak")
            self._flash("That was too short. Press 🎤 Speak and talk for a moment.")
            return
        app = self.app
        if not app.ai.free_for(self):
            self.mic_btn.configure(text="🎤  Speak")
            self._flash(f"The {app.ai.label()} tab is still using the AI; try again in a moment.")
            return
        if app.ai.owner is self:
            app.ai.stop()
        self.mic_btn.configure(text="…", state="disabled")
        model = app._model_name()
        if not engine.is_downloaded(model):
            model = next((m for m in ("large-v3-turbo", "small", "base", "medium", "large-v3", "tiny")
                          if engine.is_downloaded(m)), model)
        lang = app._lang_code() if hasattr(app, "_lang_code") else None
        self._flash("Turning your voice into text …")
        app.ai.run(self, [{"kind": "dictate", "id": "dict", "audio": audio, "model": model, "lang": lang}])

    # ------------------------------------------------------------ playing the clicked time
    def _click(self, e):
        t = self.pane.text
        if t.tag_ranges("sel"):
            return
        for tg in t.tag_names(t.index(f"@{e.x},{e.y}")):
            if tg in self.times:
                self._play(self.times[tg])
                return

    def _play(self, secs):
        app = self.app
        if self.item not in app.rows:
            return
        if app.preview_item != self.item:
            app.tree.selection_set(self.item)
            app._preview(self.item)
        app.play_at(max(0, secs - 0.3))

    def play_pause(self):
        app = self.app
        if self.item not in app.rows:
            return
        if app.preview_item != self.item:
            self._play(0)
        else:
            app.toggle_play()

    def _seek(self, t):
        app = self.app
        if self.item in app.rows and app.preview_item != self.item:
            self._play(t)
            return True
        return False
