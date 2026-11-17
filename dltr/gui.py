"""dl_transcribe window: drop audio/video files, press Start, read/play/edit the transcript, Export. Fully offline."""

import bisect
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine, media, models, speakers, theme
from .worker import Worker
from .common import (APP_NAME, IS_MAC, IS_WIN, LOG, FORMATS, LANGUAGES, MODELS, asset, find_media, fmt_hms,
                     lang_name, load_settings, log, open_path, save_settings)
from .theme import MONO, UI

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
except Exception:  # drag and drop unavailable: the window still works with the buttons
    DND_FILES, BaseTk = None, tk.Tk

BADGE = {"WAIT": "Ready — press Start", "RUN": "⏳ Working", "DONE": "✅ Done", "FAIL": "❌ Failed",
         "SKIP": "— Cancelled"}
AUTO = "Auto-detect"
LANG_CHOICES = [AUTO] + [f"{n} ({c})" for c, n in LANGUAGES.items()]
if IS_WIN:
    ICON_FONT = "Segoe MDL2 Assets"   # Windows 10/11 symbol font: crisp play/pause/volume icons
    I_PLAY, I_PAUSE = "\uE768", "\uE769"
    I_MUTE, I_VOL = "\uE74F", ("\uE993", "\uE994", "\uE995")
else:                                 # Mac: plain symbols (\uFE0E = draw as text, not as a colour emoji)
    ICON_FONT = theme.UI
    I_PLAY, I_PAUSE = "\u25B6\uFE0E", "\u23F8\uFE0E"
    I_MUTE, I_VOL = "\U0001F507", ("\U0001F508", "\U0001F509", "\U0001F50A")
MOD = "Command" if IS_MAC else "Control"     # Cmd+F on the Mac, Ctrl+F on Windows
SPK_CHOICES = ["Auto"] + [str(n) for n in range(2, 11)]


def _size(mb):
    return f"{mb:,.0f} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"


def _short(path, n=60):
    s = str(path)
    return s if len(s) <= n else s[:22] + " … " + s[-(n - 25):]


def set_icon(win):
    if not IS_WIN:
        return                        # the Mac app's icon comes from its bundle
    ico = asset("icon.ico")
    try:
        if ico.exists():
            win.iconbitmap(default=str(ico))
    except tk.TclError:
        pass


def open_in_explorer(path):
    open_path(path, reveal=True)


def sound(ok):
    try:
        if IS_WIN:
            import winsound
            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        elif IS_MAC:
            subprocess.Popen(["afplay", f"/System/Library/Sounds/{'Glass' if ok else 'Basso'}.aiff"])
    except Exception:
        pass


class App(BaseTk):
    def __init__(self, files=()):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x880")
        self.minsize(860, 640)
        set_icon(self)
        self.s = load_settings()
        self.events = queue.Queue()
        self.rows = {}           # tree item -> {"file", "status", "result", "outputs", "duration", "dirty"}
        self.running = False
        self.ok = self.fail = 0
        self.stage_text, self.t_start = "", None
        self.current = None      # tree item being transcribed
        self.preview_item = None
        self.editing = False
        self.flash_until = 0
        self.has_gpu, self.device_name = False, "checking…"
        self.models_win = None
        self.update_win = None
        self.find_hits, self.find_i = [], -1
        # player
        self.player = media.Player()
        self.player.volume = float(self.s.get("volume", 0.8))
        self.audio_item = None       # row whose audio is open in the player
        self.audio_state = None      # None | "preparing" | "ready" | "failed"
        self.pending_seek = None     # play from here once the audio is ready
        self.dragging = False
        self.seg_starts = []
        self.now_seg = None
        self.dark = self.s.get("theme") == "dark"
        self._vars()
        self.pal = theme.apply(self, self.dark)
        self._build()
        self._recolor()
        self._refresh_outdir()
        self.after(100, self._pump)
        self.after(60, lambda: theme.title_bar(self, self.dark))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.wk = Worker(self._from_worker)
        self.wk.ensure()           # starts in the background and reports which GPU it found
        self.after(1000, self._tick)
        self.after(200, self._poll_player)
        self.after(4000, self._quiet_update_check)
        threading.Thread(target=media.clean_cache, daemon=True).start()
        if files:
            self.after(400, lambda: self.add(files))

    # ------------------------------------------------------------ settings
    def _vars(self):
        s = self.s
        self.v_model = tk.StringVar(value=self._label(s.get("model") if s.get("model") in MODELS else "large-v3-turbo"))
        lang = s.get("language")
        self.v_lang = tk.StringVar(value=f"{LANGUAGES[lang]} ({lang})" if lang in LANGUAGES else AUTO)
        self.v_task = tk.StringVar(value="transcribe")      # translating moved to the Translate tab
        self.v_mixed = tk.BooleanVar(value=s.get("multilingual", False))
        self.v_hints = tk.StringVar(value=s.get("hints", ""))
        self.v_ts = tk.BooleanVar(value=s.get("timestamps", True))
        self.v_spk = tk.BooleanVar(value=s.get("speakers", False))
        self.v_showspk = tk.BooleanVar(value=s.get("show_speakers", True))
        self.v_find = tk.StringVar()
        self.v_nspk = tk.StringVar(value=str(s.get("num_speakers") or "Auto"))
        fm = s.get("formats", ["pdf", "txt", "srt"])
        self.v_fmt = {f: tk.BooleanVar(value=f in fm) for f in FORMATS}
        self.v_pos = tk.DoubleVar(value=0)
        self.v_vol = tk.DoubleVar(value=self.player.volume)
        for v in (self.v_model, self.v_lang, self.v_task, self.v_mixed, self.v_hints, self.v_ts, self.v_spk, self.v_nspk,
                  *self.v_fmt.values()):
            v.trace_add("write", lambda *_: self._save())
        self.v_ts.trace_add("write", lambda *_: (self._render(keep_view=True),
                                                 hasattr(self, "trans_tab") and self.trans_tab.render()))
        self.v_showspk.trace_add("write", lambda *_: (self._save(), self._render(keep_view=True)))

    @staticmethod
    def _label(key):
        """Drop-list text for a model, marked when it's already on this PC."""
        return MODELS[key][0] + ("   ✓ downloaded" if models.installed(key) else "")

    def _model_name(self):
        k = self.v_model.get().split(" — ")[0].strip()
        return k if k in MODELS else "large-v3-turbo"

    def set_model(self, key):
        self.v_model.set(self._label(key))

    def refresh_models(self):
        """Re-mark which models are downloaded (after the Models window or a download changed them)."""
        cur = self._model_name()
        self.model_box.configure(values=[self._label(k) for k in MODELS])
        if self.v_model.get() != self._label(cur):
            self.v_model.set(self._label(cur))

    def open_models(self):
        from .models_win import ModelsWindow
        if self.models_win is not None and self.models_win.alive():
            self.models_win.win.deiconify()
            self.models_win.win.lift()
            self.models_win.win.focus_force()
            return
        self.models_win = ModelsWindow(self)

    def _lang_code(self):
        v = self.v_lang.get()
        return v[v.rfind("(") + 1:-1] if v != AUTO and v.endswith(")") else None

    def _formats(self):
        return [f for f, v in self.v_fmt.items() if v.get()]

    def _save(self):
        self.s.update(model=self._model_name(), language=self._lang_code(), task=self.v_task.get(),
                      multilingual=self.v_mixed.get(), hints=self.v_hints.get(), timestamps=self.v_ts.get(),
                      formats=self._formats(), theme="dark" if self.dark else "light",
                      speakers=self.v_spk.get(), num_speakers=self._num_speakers(),
                      show_speakers=self.v_showspk.get(),
                      volume=round(self.player.volume, 2))
        save_settings(self.s)

    def _num_speakers(self):
        v = self.v_nspk.get()
        return int(v) if v.isdigit() else None

    def _opts(self):
        return {"model": self._model_name(), "language": self._lang_code(), "task": self.v_task.get(),
                "multilingual": self.v_mixed.get(), "hints": self.v_hints.get(),
                "speakers": self.v_spk.get() and speakers.available(), "num_speakers": self._num_speakers()}

    # ------------------------------------------------------------ layout
    def _build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        self.theme_btn = ttk.Button(top, command=self.toggle_theme, width=11)
        self.theme_btn.pack(side="left", padx=(0, 10))
        ttk.Label(top, text="Offline transcription — your files never leave this PC", style="Title.TLabel").pack(
            side="left")
        self.device_lbl = ttk.Label(top, text="Checking GPU …", style="Muted.TLabel")
        self.update_btn = ttk.Button(top, text="Check for updates", command=self.open_updates)
        self.update_btn.pack(side="right", padx=(10, 0))
        self.device_lbl.pack(side="right")
        self.warn_lbl = ttk.Label(self, style="Warn.TLabel", wraplength=900, justify="left")

        # three tabs: Transcribe (the original window), Translate, Summary
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="   Transcribe   ")
        self.tab_tr = tab
        self.drop = ttk.Label(tab, style="Drop.TLabel", relief="groove", padding=12,
                              text="Drop audio or video files / folders here (or click to browse)\n"
                                   "mp3 · wav · m4a · mp4 · mov · mkv · webm · and more — no length limit")
        self.drop.pack(fill="x", padx=12, pady=10)
        self.drop.bind("<Button-1>", lambda e: self.choose_files())
        if DND_FILES:
            for w in (self, self.drop):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # options
        opt = ttk.Frame(tab, padding=(12, 0))
        opt.pack(fill="x")
        opt.columnconfigure(1, weight=1)
        opt.columnconfigure(3, weight=1)
        ttk.Label(opt, text="Model").grid(row=0, column=0, sticky="w", padx=(0, 6))
        mrow = ttk.Frame(opt)
        mrow.grid(row=0, column=1, sticky="we")
        self.model_box = ttk.Combobox(mrow, textvariable=self.v_model, values=[self._label(k) for k in MODELS],
                                      state="readonly", width=52)
        self.model_box.pack(side="left", fill="x", expand=True)
        ttk.Button(mrow, text="Models…", command=self.open_models).pack(side="left", padx=(6, 0))
        self.model_box.bind("<<ComboboxSelected>>", lambda e: self.model_box.selection_clear())
        ttk.Label(opt, text="Language").grid(row=0, column=2, sticky="w", padx=(14, 6))
        lang = ttk.Combobox(opt, textvariable=self.v_lang, values=LANG_CHOICES, width=26)
        lang.grid(row=0, column=3, sticky="we")
        lang.bind("<KeyRelease>", lambda e: self._filter_lang(lang))
        lang.bind("<FocusOut>", lambda e: self._fix_lang(lang))
        lang.bind("<<ComboboxSelected>>", lambda e: (lang.configure(values=LANG_CHOICES), lang.selection_clear()))
        self.lang_box = lang

        r2 = ttk.Frame(opt)
        r2.grid(row=1, column=0, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Checkbutton(r2, text="Mixed languages (e.g. English + Mandarin in one recording)",
                        variable=self.v_mixed).pack(side="left")
        spk_ok = speakers.available()
        cb = ttk.Checkbutton(r2, text="Speakers (who said what)", variable=self.v_spk,
                             command=self._spk_ui)
        cb.pack(side="left", padx=(18, 0))
        if not spk_ok:
            cb.state(["disabled"])
        self.nspk_lbl = ttk.Label(r2, text="How many")
        self.nspk_lbl.pack(side="left", padx=(10, 4))
        self.nspk_box = ttk.Combobox(r2, textvariable=self.v_nspk, values=SPK_CHOICES, state="readonly", width=6)
        self.nspk_box.pack(side="left")
        self.nspk_box.bind("<<ComboboxSelected>>", lambda e: self.nspk_box.selection_clear())
        self._spk_ui()

        r4 = ttk.Frame(opt)
        r4.grid(row=2, column=0, columnspan=4, sticky="we", pady=(8, 0))
        ttk.Label(r4, text="Vocabulary").pack(side="left", padx=(0, 6))
        ttk.Entry(r4, textvariable=self.v_hints).pack(side="left", fill="x", expand=True)
        ttk.Label(r4, text="names, jargon, spellings — optional", style="Small.TLabel").pack(side="left", padx=6)

        # save row: formats · where · Open folder · Export
        r3 = ttk.Frame(opt)
        r3.grid(row=3, column=0, columnspan=4, sticky="we", pady=(10, 0))
        ttk.Label(r3, text="Save as").pack(side="left", padx=(0, 6))
        for f in FORMATS:
            ttk.Checkbutton(r3, text=f.upper(), variable=self.v_fmt[f]).pack(side="left", padx=(0, 6))
        self.export_btn = ttk.Button(r3, text="Export", command=self.export, style="Accent.TButton", width=10)
        self.export_btn.pack(side="right")
        ttk.Button(r3, text="Open folder", command=self.open_folder).pack(side="right", padx=6)
        ttk.Button(r3, text="Save to…", command=self.choose_outdir).pack(side="right")
        self.outdir_lbl = ttk.Label(r3, style="Muted.TLabel")
        self.outdir_lbl.pack(side="right", padx=(12, 6))

        # status + buttons
        bot = ttk.Frame(tab, padding=(12, 8, 12, 12))
        bot.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bot, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")
        row = ttk.Frame(bot)
        row.pack(fill="x", pady=(6, 0))
        self.start_btn = ttk.Button(row, text="Start", command=self.start, style="Accent.TButton", width=10)
        self.start_btn.pack(side="right")
        ttk.Button(row, text="Clear list", command=self.clear).pack(side="right", padx=6)
        self.reset_btn = ttk.Button(row, text="Reset save folder", command=self.reset_outdir)
        self.reset_btn.pack(side="right", padx=6)
        self.status = ttk.Label(row, text="Drop files, then press Start.", style="Muted.TLabel")
        self.status.pack(side="left", fill="x", expand=True)
        self.status.bind("<Configure>", lambda e: self.status.configure(wraplength=max(e.width - 4, 100)))

        # queue + transcript
        pane = ttk.PanedWindow(tab, orient="vertical")
        pane.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        tf = ttk.Frame(pane)
        cols = ("file", "length", "lang", "status", "time")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=5, selectmode="extended")
        for c, text, w, anchor in (("file", "File", 420, "w"), ("length", "Length", 70, "e"),
                                   ("lang", "Language", 130, "w"), ("status", "Status", 170, "w"),
                                   ("time", "Took", 70, "e")):
            self.tree.heading(c, text=text, anchor=anchor)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "file"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected())
        self.tree.bind("<Double-1>", lambda e: self.toggle_play())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        pane.add(tf, weight=1)

        pf = ttk.Frame(pane)
        hdr = ttk.Frame(pf)
        hdr.pack(fill="x", pady=(8, 4))
        self.preview_lbl = ttk.Label(hdr, text="Transcript", style="Muted.TLabel")
        self.preview_lbl.pack(side="left")
        self.mp3_btn = ttk.Button(hdr, text="Save audio as MP3", command=self.save_mp3)
        self.mp3_btn.pack(side="right")
        ttk.Button(hdr, text="Copy text", command=self.copy_text).pack(side="right", padx=6)
        self.cancel_edit_btn = ttk.Button(hdr, text="Discard edits", command=lambda: self.end_edit(False))
        self.edit_btn = ttk.Button(hdr, text="Edit transcript", command=self.toggle_edit, width=14)
        self.edit_btn.pack(side="right")
        ttk.Checkbutton(hdr, text="Show timestamps", variable=self.v_ts).pack(side="right", padx=(10, 12))
        self.showspk_cb = ttk.Checkbutton(hdr, text="Show speakers", variable=self.v_showspk)
        self.showspk_cb.pack(side="right")
        self.find_btn = ttk.Button(hdr, text="Find", command=self.open_find, width=6)
        self.find_btn.pack(side="right", padx=(0, 12))

        # find bar (Ctrl+F): shown under the header when needed
        fb = self.find_bar = ttk.Frame(pf)
        ttk.Label(fb, text="Find").pack(side="left", padx=(0, 6))
        self.find_ent = ttk.Entry(fb, textvariable=self.v_find, width=34)
        self.find_ent.pack(side="left")
        ttk.Button(fb, text="Previous", command=lambda: self._find_step(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(fb, text="Next", command=lambda: self._find_step(1)).pack(side="left", padx=6)
        self.find_lbl = ttk.Label(fb, style="Muted.TLabel")
        self.find_lbl.pack(side="left", padx=6)
        ttk.Button(fb, text="Close", command=self.close_find).pack(side="right")
        self.find_ent.bind("<KeyRelease>", lambda e: None if e.keysym in ("Return", "Escape", "Shift_L", "Shift_R")
                           else self._find_all())
        self.find_ent.bind("<Return>", lambda e: self._find_step(1))
        self.find_ent.bind("<Shift-Return>", lambda e: self._find_step(-1))
        self.find_ent.bind("<Escape>", lambda e: self.close_find())
        self.find_ent.bind("<F3>", lambda e: self._find_step(1))
        self.bind_all(f"<{MOD}-f>", lambda e: (self.open_find(), "break")[1])
        self.bind_all("<F3>", lambda e: self._find_step(1))
        self._hdr = hdr

        # player bar (like a media player: play/pause, time, seek, volume)
        pl = ttk.Frame(pf)
        pl.pack(side="bottom", fill="x", pady=(6, 0))
        self.play_btn = ttk.Button(pl, text=I_PLAY, command=self.toggle_play, style="Play.TButton", takefocus=False)
        self.play_btn.pack(side="left")
        self.time_lbl = ttk.Label(pl, text="0:00 / 0:00", style="Time.TLabel", width=17, anchor="center")
        self.time_lbl.pack(side="left", padx=6)
        self.vol = ttk.Scale(pl, from_=0, to=1, variable=self.v_vol, length=120, takefocus=False)
        self.vol.pack(side="right")
        self.vol_pct = ttk.Label(pl, style="Time.TLabel", width=4, anchor="e")
        self.vol_pct.pack(side="right", padx=(0, 4))
        self.vol_lbl = ttk.Label(pl, text=I_VOL[2], cursor="hand2", font=(ICON_FONT, 12))
        self.vol_lbl.pack(side="right", padx=(12, 2))
        self.vol_lbl.bind("<Button-1>", lambda e: self._mute())
        # Tk's sliders jump a whole step (here: 0 % or 100 %) when the bar is clicked;
        # make a click put the knob right where you click, and dragging follow the mouse.
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.vol.bind(ev, self._vol_drag)
        self.vol.bind("<MouseWheel>", lambda e: self._vol_set(self.v_vol.get() + (0.05 if e.delta > 0 else -0.05)))
        self.seek = ttk.Scale(pl, from_=0, to=1, variable=self.v_pos, takefocus=False)
        self.seek.pack(side="left", fill="x", expand=True, padx=(4, 0))
        for ev in ("<Button-1>", "<B1-Motion>"):
            self.seek.bind(ev, self._seek_drag)
        self.seek.bind("<ButtonRelease-1>", self._seek_release)

        body = ttk.Frame(pf)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", height=10, font=(theme.TEXT, 11 + theme._S), relief="flat", padx=14, pady=10,
                            borderwidth=1, highlightthickness=1, spacing1=1, spacing3=1, undo=True,
                            cursor="arrow")
        tsb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tsb.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        pane.add(pf, weight=3)

        self.text.bind(f"<{MOD}-a>", lambda e: (self.text.tag_add("sel", "1.0", "end"), "break")[1])
        self.text.bind("<Key>", self._edit_guard)
        for ev in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
            self.text.bind(ev, lambda e: self._guard_virtual(e))
        self.text.tag_bind("spk", "<Button-1>", self._spk_menu)
        self.text.tag_bind("spk", "<Button-3>", self._spk_menu)
        self.text.tag_bind("spk", "<Button-2>", self._spk_menu)      # right-click on the Mac
        self.text.tag_bind("spk", "<Enter>", lambda e: (not self.editing) and self.text.configure(cursor="hand2"))
        self.text.tag_bind("seg", "<ButtonRelease-1>", self._click_seg)
        self.text.tag_bind("seg", "<Enter>", lambda e: (not self.editing) and self.text.configure(cursor="hand2"))
        self.text.tag_bind("seg", "<Leave>", lambda e: self.text.configure(cursor="xterm" if self.editing
                                                                             else "arrow"))
        for w in (self.tree, self.text):
            w.bind("<space>", lambda e: None if self.editing else (self.toggle_play(), "break")[1])
        self._player_ui()
        self.vol_lbl.configure(text=I_VOL[2])
        self.vol_pct.configure(text=f"{self.player.volume:.0%}")

        # Translate + Summary tabs (dltr/tabs.py)
        from .tabs import AiRunner, SummaryTab, TranslateTab
        self.ai = AiRunner(self)
        self.trans_tab = TranslateTab(self.nb, self)
        self.nb.add(self.trans_tab, text="   Translate   ")
        self.sum_tab = SummaryTab(self.nb, self, self.trans_tab)
        self.nb.add(self.sum_tab, text="   Summary   ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.sum_tab.refresh())

    def _spk_ui(self):
        on = self.v_spk.get() and speakers.available()
        self.nspk_box.configure(state="readonly" if on else "disabled")
        self.nspk_lbl.configure(style="TLabel" if on else "Muted.TLabel")

    def _filter_lang(self, box):
        q = box.get().strip().lower()
        box.configure(values=[c for c in LANG_CHOICES if q in c.lower()] if q else LANG_CHOICES)

    def _fix_lang(self, box):
        if box.get() not in LANG_CHOICES:
            q = box.get().strip().lower()
            match = [c for c in LANG_CHOICES if q and q in c.lower()]
            box.set(match[0] if match else AUTO)
        box.configure(values=LANG_CHOICES)

    def _recolor(self):
        p = self.pal
        self.theme_btn.configure(text="Light mode" if self.dark else "Dark mode")
        sel = p.get("select", "#cce0ff")
        self.text.configure(bg=p["field"], fg=p["text"], insertbackground=p["text"], highlightbackground=p["line"],
                            highlightcolor=p["accent"], selectbackground=sel, inactiveselectbackground=sel)
        self.text.tag_configure("ts", foreground=p["faint"], font=(UI, 9 + theme._S))
        self.text.tag_configure("ets", foreground=p["accent"], font=(MONO, 10 + theme._S))
        self.text.tag_configure("now", background="#2f3b52" if self.dark else "#fff3b0")
        self.text.tag_configure("err", foreground=p["tags"]["FAIL"])
        self.text.tag_configure("spk", foreground=p["accent"], font=(UI, 10 + theme._S, "bold"), spacing1=4)
        self.text.tag_configure("find", background="#5c4a12" if self.dark else "#ffe08a")
        self.text.tag_configure("findcur", background="#d97706" if self.dark else "#ff9f43",
                                foreground="#ffffff" if self.dark else "#000000")
        if hasattr(self, "trans_tab"):
            self.trans_tab.recolor(p, self.dark)
            self.sum_tab.recolor(p, self.dark)
        self.text.tag_raise("find")
        self.text.tag_raise("findcur")
        self.text.tag_raise("sel")
        ttk.Style(self).configure("Warn.TLabel", foreground="#f5a524" if self.dark else "#b45309")
        for k, color in p["tags"].items():
            self.tree.tag_configure(k, foreground=color)
        # an already-opened combobox list keeps its colours, so recolour it too
        for box in (self.model_box, self.lang_box):
            try:
                pd = self.tk.call("ttk::combobox::PopdownWindow", str(box))
                self.tk.call(f"{pd}.f.l", "configure", "-background", p["field"] if self.dark else "#ffffff",
                             "-foreground", p["text"], "-selectbackground", sel, "-selectforeground", p["text"])
            except tk.TclError:
                pass

    def toggle_theme(self):
        self.dark = not self.dark
        self.pal = theme.apply(self, self.dark)
        self._recolor()
        if self.models_win is not None and self.models_win.alive():
            self.models_win.refresh()
        self._save()

    def _flash(self, msg, secs=6):
        """Show a message in the status line (kept for a few seconds even while transcribing)."""
        self.status.configure(text=msg)
        self.flash_until = time.time() + secs

    # ------------------------------------------------------------ output folder
    def _custom_outdir(self):
        d = self.s.get("outdir")
        return Path(d) if d else None

    def _outdir_for(self, r):
        return self._custom_outdir() or engine.default_outdir(r["file"])

    def _refresh_outdir(self):
        d = self._custom_outdir()
        self.outdir_lbl.configure(text=f"→ {_short(d, 46)}" if d else "→ “Transcripts” folder beside each file")
        self.reset_btn.configure(state="normal" if d else "disabled")

    def choose_outdir(self):
        r = self._sel_row() or next(iter(self.rows.values()), None)
        start = self._custom_outdir() or (r["file"].parent if r else Path.home())
        d = filedialog.askdirectory(title="Save transcripts to", initialdir=str(start), mustexist=False)
        if d:
            self.s["outdir"] = d
            self._save()
            self._refresh_outdir()

    def reset_outdir(self):
        self.s.pop("outdir", None)
        self._save()
        self._refresh_outdir()
        self._flash("Saving to a “Transcripts” folder beside each file again.")

    # ------------------------------------------------------------ queue
    def _on_drop(self, e):
        self.add(self.tk.splitlist(e.data))

    def choose_files(self):
        files = filedialog.askopenfilenames(title="Choose audio or video files", filetypes=[
            ("Audio & video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mov *.mkv *.avi *.webm "
                              "*.wmv *.m4v *.mts *.ts *.3gp *.mpg *.mpeg"), ("All files", "*.*")])
        if files:
            self.add(files)

    def add(self, paths):
        """Queue files. Nothing runs until Start is pressed."""
        known = {r["file"] for r in self.rows.values()}
        found = find_media(paths)
        new = [f for f in found if f not in known]
        if not new:
            self._flash("Those files are already in the list." if found
                        else "No audio or video files found in what was dropped.")
            return
        first = None
        for f in new:
            item = self.tree.insert("", "end", values=(f.name, "…", "", BADGE["WAIT"], ""), tags=("WAIT",))
            self.rows[item] = {"file": f, "status": "WAIT", "result": None, "outputs": [], "duration": None,
                               "dirty": False}
            first = first or item
            threading.Thread(target=self._probe, args=(item, f), daemon=True).start()
        if self.running:
            self._flash(f"Added {len(new)} file{'s' * (len(new) != 1)} — they'll run after the current one.")
        else:
            self._flash(f"Added {len(new)} file{'s' * (len(new) != 1)}. Press Start to transcribe.", 30)
        if not self.tree.selection():
            self.tree.selection_set(first)

    def _probe(self, item, f):
        self.events.put(("dur", item, engine.probe_duration(f)))

    def _set(self, item, **vals):
        if not self.tree.exists(item):
            return
        cur = dict(zip(("file", "length", "lang", "status", "time"), self.tree.item(item, "values")))
        cur.update(vals)
        self.tree.item(item, values=tuple(cur.values()))

    def _set_status(self, item, st, text=None):
        self.rows[item]["status"] = st
        self._set(item, status=text or BADGE[st])
        self.tree.item(item, tags=(st,))

    def _sel_row(self):
        sel = self.tree.selection()
        return self.rows.get(sel[0]) if sel else None

    def remove_selected(self):
        for item in self.tree.selection():
            if item != self.current:
                self._drop_row(item)

    def clear(self):
        for item in list(self.rows):
            if item != self.current:
                self._drop_row(item)

    def _drop_row(self, item):
        if item == self.audio_item:
            self._close_audio()
        if item == self.preview_item:
            if self.editing:
                self.end_edit(False)
            self._preview(None)
        self.tree.delete(item)
        self.rows.pop(item, None)

    # ------------------------------------------------------------ run
    def _needed_models(self):
        opts = self._opts()
        names = [opts["model"]]
        if opts["task"] == "translate" and "turbo" in opts["model"]:
            names = ["large-v3"]          # turbo can't translate; the engine switches to large-v3
        return [n for n in names if not engine.is_downloaded(n)]

    def start(self):
        if self.running:
            return
        if not any(r["status"] == "WAIT" for r in self.rows.values()):
            # nothing new: re-run the selected rows (e.g. with another model)
            again = [i for i in self.tree.selection() if self.rows[i]["status"] in ("DONE", "FAIL", "SKIP")]
            if not again:
                self._flash("Drop some files first." if not self.rows else
                            "Everything in the list is done. Select a file and press Start to redo it.")
                return
            if any(self.rows[i]["dirty"] and self.rows[i].get("edited") for i in again) and not messagebox.askyesno(
                    APP_NAME, "Redo the transcription? Your edits to it will be replaced."):
                return
            for i in again:
                self._set_status(i, "WAIT")
        if self.editing:
            self.end_edit(True)
        missing = self._needed_models()
        if missing:
            choice = self._ask_download(missing[0])
            if not choice:
                self._flash("Model not downloaded — nothing started. Press Start when ready.")
                return
            self._begin_run()
            self.t_start = time.time()
            self._stage("Preparing download …", 0.0)
            self.wk.submit({"kind": "download", "id": "dl", "models": choice})
            return
        self._begin_run()
        self._run_next()

    def _ask_download(self, name):
        """One-time model download prompt. Returns the list of models to fetch, or None."""
        others = [m for m in MODELS if not engine.is_downloaded(m)]
        total = sum(MODELS[m][1] for m in others)
        dlg = tk.Toplevel(self)
        dlg.title("Download a model")
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.configure(bg=self.pal["bg"])
        f = ttk.Frame(dlg, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"The {name} model isn't on this PC yet.", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, justify="left", wraplength=460, text=(
            f"It's a one-time download of about {_size(MODELS[name][1])}. "
            "After that, transcription runs completely offline.\n\n"
            f"“Download all” fetches every model ({len(others)} models, about {_size(total)}) "
            "so you can switch models later without internet.")).pack(anchor="w", pady=(8, 14))
        res = {"v": None}
        row = ttk.Frame(f)
        row.pack(fill="x")

        def pick(v):
            res["v"] = v
            dlg.destroy()
        ttk.Button(row, text="Cancel", command=lambda: pick(None)).pack(side="right")
        if len(others) > 1:
            ttk.Button(row, text=f"Download all ({_size(total)})",
                       command=lambda: pick([name] + [m for m in others if m != name])).pack(side="right", padx=6)
        b = ttk.Button(row, text=f"Download {name} ({_size(MODELS[name][1])})", command=lambda: pick([name]))
        b.pack(side="right")
        b.focus_set()
        dlg.bind("<Return>", lambda e: pick([name]))
        dlg.bind("<Escape>", lambda e: pick(None))
        dlg.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        dlg.update_idletasks()
        theme.title_bar(dlg, self.dark)
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.grab_set()
        self.wait_window(dlg)
        return res["v"]

    def _begin_run(self):
        self.running = True
        self.flash_until = 0
        self.ok = self.fail = 0
        self.start_btn.configure(text="■  Stop", command=self.stop, style="Stop.TButton")

    def stop(self):
        if not self.running:
            return
        self.wk.kill()           # really stops the GPU work, mid-file
        if self.current in self.rows:
            self._set_status(self.current, "SKIP")
            if self.current == self.preview_item:
                self._preview(self.current)
        self._finish(cancelled=True)

    def _next(self):
        for item in self.tree.get_children():
            if self.rows.get(item, {}).get("status") == "WAIT":
                return item
        return None

    def _run_next(self):
        item = self._next()
        if item is None:
            self._finish()
            return
        r = self.rows[item]
        r.update(result=None, error=None, dirty=False, edited=False)
        self.current = item
        self.t_start = time.time()
        self._set_status(item, "RUN")
        self.tree.see(item)
        if self.preview_item in (None, item) or not (self.rows.get(self.preview_item) or {}).get("result"):
            self.tree.selection_set(item)
        if item == self.preview_item:
            self._preview(item)
        self._stage("Starting …", 0.0)
        self.wk.submit({"kind": "transcribe", "id": item, "file": str(r["file"]), "opts": self._opts()})

    def _finish(self, cancelled=False):
        self.running = False
        self.current = None
        self.t_start = None
        self.start_btn.configure(text="Start", command=self.start, style="Accent.TButton")
        self._player_ui()
        ok, fail = self.ok, self.fail
        if cancelled:
            self.progress["value"] = 0
            self._flash("Stopped. Press Start to continue the remaining files.", 30)
        elif ok or fail:
            self.progress["value"] = 1.0 if ok else 0
            msg = (f"Finished: {ok} transcribed" + (f", {fail} failed" if fail else "") +
                   " — click any line to play it, then press Export to save.")
            if fail and not ok:
                msg = self.status.cget("text")
            self._flash(msg, 60)
            sound(not fail)

    def _stage(self, text, frac=None):
        """Update the status line and (when frac is given) the progress bar + row percentage."""
        self.stage_text = text
        if frac is not None:
            self.progress["value"] = frac
            if self.current in self.rows:
                self._set(self.current, status=f"⏳ {frac:.0%}")
        self._tick(reschedule=False)

    def _tick(self, reschedule=True):
        if self.running and time.time() > self.flash_until:
            name = self.rows[self.current]["file"].name + ": " if self.current in self.rows else ""
            pct = f"{self.progress['value']:.0%} · "
            el = f" · {fmt_hms(time.time() - self.t_start)} elapsed" if self.t_start else ""
            self.status.configure(text=f"{name}{pct}{self.stage_text}{el}")
        if reschedule:
            self.after(1000, self._tick)

    def _from_worker(self, kind, *a):
        self.events.put((kind, *a))     # reader thread -> UI thread

    # ------------------------------------------------------------ UI pump
    def _pump(self):
        try:
            while True:
                self._handle(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _handle(self, kind, *a):
        if kind == "call":
            a[0]()
            return
        if kind == "ai":                       # translation / summary worker
            self.ai.handle(*a)
            return
        if kind == "device":
            self.has_gpu, self.device_name, problem = a
            self.device_lbl.configure(text=f"Running on: {self.device_name}" + ("" if self.has_gpu else " (slower)"))
            self.warn_lbl.configure(text=problem)
            if problem:
                self.warn_lbl.pack(fill="x", padx=12, pady=(6, 0), before=self.nb)
            else:
                self.warn_lbl.pack_forget()
            if not self.has_gpu and "model" not in self.s:
                self.set_model("small")
        elif kind == "dur":
            item, d = a
            if item in self.rows:
                self.rows[item]["duration"] = d
                self._set(item, length=fmt_hms(d) if d else "?")
                if item == self.preview_item:
                    self._player_ui()
        elif kind == "prog":
            jid, frac, text = a
            if jid == "dl":
                self.progress["value"] = frac or 0
                self.stage_text = text
                self._tick(reschedule=False)
            elif jid == self.current:
                if frac is None:
                    self._stage(text)
                elif text.startswith("Downloading"):
                    self._stage(text, frac * 0.05)
                else:
                    # transcription fills 5–99 %; 100 % once it's finished
                    self._stage(text, 0.05 + 0.94 * frac)
        elif kind == "downloaded":
            self.refresh_models()
            if self.models_win is not None and self.models_win.alive():
                self.models_win.refresh()
            self._run_next()
        elif kind == "seg":
            item, seg = a
            r = self.rows.get(item)
            if r is not None:
                r.setdefault("live", []).append(seg)
            if item == self.preview_item and not self.editing:
                self._append(seg, len(r["live"]) - 1 if r else 0)
        elif kind == "done":
            item, res, took = a
            r = self.rows.get(item)
            self.ok += 1
            if r:
                r.update(result=res, dirty=True, live=None)
                self._set_status(item, "DONE")
                lang = lang_name(res["spoken_language"]) + (" → English" if res["task"] == "translate" else "")
                self._set(item, lang=lang, time=fmt_hms(took))
                note = "; ".join(x for x in (res.get("note"), "on CPU" if r.get("retry") else "") if x)
                if note:
                    self._set(item, status=f"✅ Done ({note})")
                if item == self.preview_item:
                    self._preview(item)
            self.progress["value"] = 1.0
            self._run_next()
        elif kind == "fail":
            jid, msg = a
            if jid == "dl":
                self._finish()
                self._flash(f"Download failed: {msg}", 60)
                return
            self.fail += 1
            if jid in self.rows:
                self._set_status(jid, "FAIL")
                self.rows[jid]["error"] = msg
                if jid == self.preview_item:
                    self._preview(jid)
            self._flash(f"{self.rows[jid]['file'].name}: {msg}" if jid in self.rows else msg, 10)
            self._run_next()
        elif kind == "died":
            jid, code = a
            log(f"worker died (exit {code}) during {jid}")
            r = self.rows.get(jid)
            if jid is None:
                return                   # died while idle: a new one starts with the next job
            if jid == "dl":
                self._finish()
                self._flash("The download stopped unexpectedly. Press Start to try again.", 60)
            elif r is not None and not r.get("retry") and not self.wk.force_cpu:
                # GPU / driver crash: retry this file on the CPU in a fresh worker
                r["retry"] = True
                self.wk = Worker(self._from_worker, force_cpu=True)
                self.wk.ensure()
                self._set_status(jid, "WAIT")
                self._run_next()
            else:
                self.fail += 1
                if r is not None:
                    self._set_status(jid, "FAIL")
                    r["error"] = f"The transcriber stopped unexpectedly (code {code}). Log: {LOG}"
                self._run_next()
        elif kind == "exported":
            items, folders, n, err = a
            self.export_btn.configure(state="normal")
            for i in items:
                if i in self.rows:
                    self.rows[i]["dirty"] = False
                    if self.rows[i]["status"] == "DONE":
                        self._set(i, status="✅ Exported")
            where = folders[0] if len(folders) == 1 else f"{len(folders)} folders"
            if err:
                self._flash(f"Export problem: {err}", 30)
                sound(False)
            else:
                self._flash(f"Exported {n} file{'s' * (n != 1)} to {_short(where, 70)} — Open folder shows them.", 30)
                sound(True)
        elif kind == "audio":
            item, path, err = a
            if item != self.audio_item:
                return
            if err:
                self.audio_state = "failed"
                self._flash(f"Can't play this file: {err}", 10)
            else:
                try:
                    self.player.open(path)
                    self.audio_state = "ready"
                    if self.pending_seek is not None:
                        self.player.play(self.pending_seek)
                except RuntimeError as e:
                    self.audio_state = "failed"
                    self._flash(f"Can't play this file: {e}", 10)
            self.pending_seek = None
            self._player_ui()
        elif kind == "audio_prog":
            item, frac = a
            if item == self.audio_item and self.audio_state == "preparing":
                self.time_lbl.configure(text=f"preparing {frac:.0%}")
        elif kind == "mp3_prog":
            self._flash(f"Saving MP3 … {a[0]:.0%}", 120)
        elif kind == "mp3":
            path, err = a
            self.mp3_btn.configure(state="normal")
            if err:
                self._flash(f"Couldn't save the MP3: {err}", 15)
            else:
                self._flash(f"Saved {Path(path).name} to {Path(path).parent}", 20)

    # ------------------------------------------------------------ transcript view
    def _show_selected(self):
        sel = self.tree.selection()
        if sel and sel[0] != self.preview_item:
            if self.editing:
                self.end_edit(True)
            self._preview(sel[0])

    def _segments(self, r):
        if not r:
            return []
        if r.get("result"):
            return r["result"]["segments"]
        return r.get("live") or []

    def _preview(self, item):
        if item != self.audio_item:
            self._close_audio()
            self.audio_item = item
        self.preview_item = item
        self._render()
        self._player_ui()

    def _top_seg(self):
        """(sentence number, how far into it) of the sentence at the top of the view, to come back to it."""
        t = self.text
        idx = t.index("@0,0")
        for tg in t.tag_names(idx):
            if tg[0] == "s" and tg[1:].isdigit():
                return int(tg[1:]), len(t.get(t.tag_prevrange(tg, f"{idx}+1c")[0], idx))
        nxt = t.tag_nextrange("seg", idx)
        if nxt:
            for tg in t.tag_names(nxt[0]):
                if tg[0] == "s" and tg[1:].isdigit():
                    return int(tg[1:]), 0
        return None

    def _scroll_to_seg(self, anchor):
        t = self.text
        t.update_idletasks()
        if anchor is None:
            t.yview_moveto(0)
            return
        rng = t.tag_ranges(f"s{anchor[0]}")
        if not rng:
            t.yview_moveto(0)
            return
        t.yview(t.index(f"{rng[0]}+{anchor[1]}c"))

    def _view(self, res):
        """The result as shown/exported: without speaker names when 'Show speakers' is off."""
        if res and res.get("speakers") and not self.v_showspk.get():
            return dict(res, speakers=[])
        return res

    def _render(self, keep_view=False):
        """Draw the transcript like a document: paragraphs, optional (m:ss) stamps, each sentence clickable.
        keep_view: stay at the same place in the text (toggles, edit mode) instead of going to the top."""
        if self.editing:
            return
        anchor = self._top_seg() if keep_view else None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.now_seg = None
        r = self.rows.get(self.preview_item) if self.preview_item else None
        segs = self._segments(r)
        self.seg_starts = [s["start"] for s in segs]
        if not r:
            self.preview_lbl.configure(text="Transcript")
        else:
            live = r["status"] == "RUN"
            self.preview_lbl.configure(text=f"Transcript — {r['file'].name}" + (" (live)" if live else ""))
            if segs:
                res = self._view(r.get("result") or {"segments": segs, "language": None})
                j = " " if self.v_ts.get() else ("" if res.get("language") in engine.CJK else " ")
                idx = 0
                for pi, (name, para) in enumerate(engine.labelled(res)):
                    if pi:
                        self.text.insert("end", "\n\n")
                    if name:
                        k = para[0].get("speaker") or 0
                        self.text.insert("end", name, ("spk", f"k{k}", f"p{idx}", "prot"))
                        self.text.insert("end", "\n", ("prot",))
                    for k, s in enumerate(para):
                        self._insert_seg(s, idx, sep=(j if k else ""))
                        idx += 1
            elif r.get("error"):
                self.text.insert("end", f"Failed: {r['error']}", "err")
            elif r["status"] == "WAIT":
                self.text.insert("end", "Press Start to transcribe this file. You can already play it below.", "ts")
            elif r["status"] == "SKIP":
                self.text.insert("end", "Cancelled. Press Start to transcribe it.", "ts")
            elif live:
                self.text.insert("end", "Working … text appears here as it's recognised.", "ts")
        self.text.configure(state="disabled")
        self._scroll_to_seg(anchor)
        if self.find_bar.winfo_ismapped():
            self._find_all(scroll=False)

    def _insert_seg(self, s, i, sep=""):
        tag = f"s{i}"
        if sep:
            self.text.insert("end", sep)
        if self.v_ts.get():
            self.text.insert("end", f"({fmt_hms(s['start'])}) ", ("ts", "seg", tag, "prot"))
        self.text.insert("end", s["text"], ("seg", tag))

    def _append(self, seg, i):
        """A live segment while transcribing (new paragraph after a pause)."""
        r = self.rows.get(self.preview_item)
        segs = self._segments(r)
        self.seg_starts = [s["start"] for s in segs]
        self.text.configure(state="normal")
        if i == 0:
            self.text.delete("1.0", "end")
            sep = ""
        else:
            prev = segs[i - 1] if i - 1 < len(segs) else None
            sep = "\n\n" if prev and seg["start"] - prev["end"] > 2.0 else " "
        at_end = self.text.yview()[1] > 0.98
        self._insert_seg(seg, i, sep)
        if at_end:
            self.text.see("end")
        self.text.configure(state="disabled")

    def _click_seg(self, e):
        if self.editing or self.text.tag_ranges("sel"):
            return                       # editing, or selecting text to copy
        idx = self.text.index(f"@{e.x},{e.y}")
        for t in self.text.tag_names(idx):
            if t[0] == "s" and t[1:].isdigit():
                i = int(t[1:])
                if i < len(self.seg_starts):
                    self.play_at(self.seg_starts[i])
                return

    def _highlight(self, pos):
        if self.editing or not self.seg_starts:
            return
        i = bisect.bisect_right(self.seg_starts, pos + 0.05) - 1
        if i == self.now_seg:
            return
        self.text.tag_remove("now", "1.0", "end")
        self.now_seg = i
        if i >= 0:
            rng = self.text.tag_ranges(f"s{i}")
            if rng:
                self.text.tag_add("now", rng[0], rng[1])
                self.text.see(rng[1])
                self.text.see(rng[0])

    # ------------------------------------------------------------ find
    def open_find(self):
        if not self.find_bar.winfo_ismapped():
            self.find_bar.pack(fill="x", pady=(0, 6), after=self._hdr)
        self.find_ent.focus_set()
        self.find_ent.select_range(0, "end")
        if self.v_find.get():
            self._find_all()

    def close_find(self):
        self.find_bar.pack_forget()
        self.text.tag_remove("find", "1.0", "end")
        self.text.tag_remove("findcur", "1.0", "end")
        self.find_hits, self.find_i = [], -1
        self.text.focus_set()

    def _find_all(self, scroll=True):
        """Highlight every match (not case-sensitive) and jump to the first one from where you're reading."""
        t, q = self.text, self.v_find.get()
        t.tag_remove("find", "1.0", "end")
        t.tag_remove("findcur", "1.0", "end")
        self.find_hits, self.find_i = [], -1
        if not q.strip():
            self.find_lbl.configure(text="")
            return
        cnt = tk.IntVar()
        idx = "1.0"
        while True:
            pos = t.search(q, idx, "end", nocase=True, count=cnt)
            if not pos or not cnt.get():
                break
            end = f"{pos}+{cnt.get()}c"
            self.find_hits.append((pos, end))
            t.tag_add("find", pos, end)
            idx = end
        if not self.find_hits:
            self.find_lbl.configure(text="No matches")
            return
        here = t.index("@0,0")
        self.find_i = next((k for k, (a, _b) in enumerate(self.find_hits) if t.compare(a, ">=", here)), 0)
        self._find_show(scroll)

    def _find_step(self, d):
        if not self.find_bar.winfo_ismapped():
            self.open_find()
            return "break"
        if not self.find_hits:
            self._find_all()
            if not self.find_hits:
                return "break"
        else:
            self.find_i = (self.find_i + d) % len(self.find_hits)
        self._find_show(True)
        return "break"

    def _find_show(self, scroll=True):
        a, b = self.find_hits[self.find_i]
        self.text.tag_remove("findcur", "1.0", "end")
        self.text.tag_add("findcur", a, b)
        if scroll:
            self.text.see(a)
        self.find_lbl.configure(text=f"{self.find_i + 1} of {len(self.find_hits)}")

    # ------------------------------------------------------------ updates
    def open_updates(self):
        from .update_win import UpdateWindow
        if self.update_win is not None and self.update_win.alive():
            self.update_win.win.lift()
            self.update_win.win.focus_force()
            return
        self.update_win = UpdateWindow(self)

    def set_update_badge(self, version):
        if version:
            self.update_btn.configure(text=f"Update available: {version}", style="Accent.TButton")
        else:
            self.update_btn.configure(text="Check for updates", style="TButton")

    def _quiet_update_check(self):
        """Once per start: if GitHub has a newer version, say so on the button (no pop-up)."""
        from . import updater

        def work():
            try:
                info = updater.latest()
            except Exception:
                return
            if updater.is_newer(info["version"]):
                self.events.put(("call", lambda: self.set_update_badge(info["version"])))
        threading.Thread(target=work, daemon=True).start()

    def copy_text(self):
        r = self.rows.get(self.preview_item)
        if self.editing:
            text = self.text.get("1.0", "end")
        elif r and r.get("result"):
            text = engine.to_text(self._view(r["result"]), self.v_ts.get())
        else:
            text = self.text.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._flash("Transcript copied to the clipboard.")

    # ------------------------------------------------------------ editing (in place, in the same view)
    def toggle_edit(self):
        if self.editing:
            self.end_edit(True)
            return
        r = self.rows.get(self.preview_item)
        if not r or not r.get("result"):
            self._flash("Transcribe the file first, then you can edit the text.")
            return
        self._render(keep_view=True)   # start from a clean copy of the transcript, at the same place
        self.editing = True
        self.text.tag_remove("now", "1.0", "end")
        self.now_seg = None
        self.text.configure(state="normal", cursor="xterm")
        self.text.edit_reset()
        self.text.mark_set("insert", self.text.index("@0,0"))     # cursor where you were reading
        self.text.focus_set()
        self.edit_btn.configure(text="Save edits", style="Accent.TButton")
        self.cancel_edit_btn.pack(side="right", padx=(0, 6), before=self.edit_btn)
        self.preview_lbl.configure(text=f"Editing — {r['file'].name}")
        self._flash("Editing: click into the text and type, like a document. Timestamps and speaker names stay "
                    "as they are. Press Save edits when done.", 20)

    _NAV = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Shift_L", "Shift_R", "Control_L",
            "Control_R", "Alt_L", "Alt_R", "Escape", "Caps_Lock", "Insert", "F1", "F2", "F3", "F4", "F5", "F6",
            "F7", "F8", "F9", "F10", "F11", "F12", "Win_L", "Win_R", "App"}

    def _edit_guard(self, e):
        """While editing: allow typing anywhere in the text, but not inside a timestamp or speaker name."""
        if not self.editing:
            return None
        ks = e.keysym
        ctrl = e.state & (0x4 | (0x8 if IS_MAC else 0))      # Ctrl, or Cmd on the Mac
        if ks in self._NAV or (ctrl and ks.lower() in ("a", "c", "z", "y", "home", "end", "left", "right")):
            return None
        if not e.char and ks not in ("BackSpace", "Delete"):
            return None
        t = self.text
        if t.tag_ranges("sel"):
            a, b = t.index("sel.first"), t.index("sel.last")
        elif ks == "BackSpace":
            a, b = t.index("insert-1c"), t.index("insert")
        elif ks == "Delete":
            a, b = t.index("insert"), t.index("insert+1c")
        else:                                       # typing / pasting at the cursor
            inside = "prot" in t.tag_names("insert") and "prot" in t.tag_names("insert-1c")
            if inside:
                self._flash("Timestamps and speaker names can't be typed into — click in the sentence text.")
                return "break"
            return None
        if "prot" in t.tag_names(a) or t.tag_nextrange("prot", a, b):
            self._flash("Timestamps and speaker names can't be changed here — only the sentence text.")
            return "break"
        return None

    def end_edit(self, keep):
        if not self.editing:
            return
        r = self.rows.get(self.preview_item)
        if keep and r and r.get("result"):
            new = self._read_edits(r["result"]["segments"])
            if new != r["result"]["segments"]:
                r["result"]["segments"] = new
                r["dirty"] = r["edited"] = True
                self._set(self.preview_item, status="✏️ Edited")
                self._flash("Edits saved. Press Export to write them to files.")
        self.editing = False
        self.text.configure(cursor="arrow")
        self.edit_btn.configure(text="Edit transcript", style="TButton")
        self.cancel_edit_btn.pack_forget()
        if not keep:
            self._flash("Edits discarded.")
        self._render(keep_view=True)

    def _read_edits(self, old):
        """Collect the edited text sentence by sentence (each sentence is tagged s<n> in the view).
        Text typed between sentences belongs to the sentence before it. Timings stay as they were."""
        buckets = {}
        active, cur = set(), None
        for key, val, _idx in self.text.dump("1.0", "end-1c", text=True, tag=True):
            if key == "tagon":
                active.add(val)
                if val[0] == "s" and val[1:].isdigit():
                    cur = int(val[1:])
            elif key == "tagoff":
                active.discard(val)
            elif key == "text" and "prot" not in active:
                k = cur if cur is not None else 0
                buckets[k] = buckets.get(k, "") + val
        out = []
        for i, seg in enumerate(old):
            text = re.sub(r"\s+", " ", buckets.get(i, "")).strip()
            if not text:
                continue                      # sentence deleted
            if text == seg["text"]:
                out.append(seg)
                continue
            new = dict(seg, text=text)
            words = seg.get("words") or []
            parts = text.split(" ")
            if words and " " in seg["text"] and len(parts) == len(words):
                # same number of words: keep each word's timing (subtitles stay precise)
                new["words"] = [dict(w, word=(" " if w["word"].startswith(" ") else "") + p)
                                for w, p in zip(words, parts)]
            else:
                new["words"] = []
            out.append(new)
        for i, seg in enumerate(out):
            if seg.get("id") != i:
                out[i] = dict(seg, id=i)
        return out

    # ------------------------------------------------------------ speakers: rename / reassign
    def _guard_virtual(self, e):
        """Paste / cut from the right-click menu or Shift+Insert go through the same check."""
        if not self.editing:
            return None
        t = self.text
        if t.tag_ranges("sel"):
            a, b = t.index("sel.first"), t.index("sel.last")
            if "prot" in t.tag_names(a) or t.tag_nextrange("prot", a, b):
                self._flash("Timestamps and speaker names can't be changed here — only the sentence text.")
                return "break"
        elif "prot" in t.tag_names("insert") and "prot" in t.tag_names("insert-1c"):
            return "break"
        return None

    def _spk_menu(self, e):
        r = self.rows.get(self.preview_item)
        if self.editing or not r or not r.get("result"):
            return "break"
        idx = self.text.index(f"@{e.x},{e.y}")
        names = self.text.tag_names(idx)
        k = next((int(t[1:]) for t in names if t[0] == "k" and t[1:].isdigit()), None)
        p = next((int(t[1:]) for t in names if t[0] == "p" and t[1:].isdigit()), None)
        if k is None or p is None:
            return "break"
        res = r["result"]
        spk = res.get("speakers") or []
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=f"Rename “{spk[k] if k < len(spk) else k + 1}” everywhere …",
                      command=lambda: self._rename_speaker(k))
        m.add_separator()
        for j, n in enumerate(spk):
            if j != k:
                m.add_command(label=f"This part was said by {n}", command=lambda j=j: self._reassign(p, j))
        m.add_command(label="This part was said by a new speaker", command=lambda: self._reassign(p, len(spk)))
        m.tk_popup(e.x_root, e.y_root)
        return "break"

    def _para_of(self, res, first_idx):
        """Segment indexes of the paragraph that starts at segment first_idx."""
        idx = 0
        for _name, para in engine.labelled(res):
            if idx == first_idx:
                return list(range(idx, idx + len(para)))
            idx += len(para)
        return []

    def _reassign(self, first_idx, j):
        r = self.rows.get(self.preview_item)
        res = r["result"]
        spk = res.setdefault("speakers", [])
        while len(spk) <= j:
            spk.append(f"Speaker {len(spk) + 1}")
        segs = res["segments"]
        # the whole turn (until the voice changes) moves to the other speaker
        k = segs[first_idx].get("speaker")
        i = first_idx
        while i < len(segs) and segs[i].get("speaker") == k:
            segs[i]["speaker"] = j
            i += 1
        r["dirty"] = r["edited"] = True
        self._set(self.preview_item, status="✏️ Edited")
        self._render()

    def _rename_speaker(self, k):
        r = self.rows.get(self.preview_item)
        res = r["result"]
        spk = res.get("speakers") or []
        if k >= len(spk):
            return
        new = self._ask_text("Rename speaker", f"New name for {spk[k]}:", spk[k])
        if new and new.strip() and new.strip() != spk[k]:
            spk[k] = new.strip()
            r["dirty"] = r["edited"] = True
            self._set(self.preview_item, status="✏️ Edited")
            self._render()

    def _ask_text(self, title, prompt, value):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.configure(bg=self.pal["bg"])
        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=prompt).pack(anchor="w")
        v = tk.StringVar(value=value)
        ent = ttk.Entry(f, textvariable=v, width=36)
        ent.pack(fill="x", pady=(6, 12))
        res = {"v": None}

        def done(ok):
            res["v"] = v.get() if ok else None
            dlg.destroy()
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="Cancel", command=lambda: done(False)).pack(side="right")
        ttk.Button(row, text="OK", style="Accent.TButton", command=lambda: done(True)).pack(side="right", padx=6)
        dlg.bind("<Return>", lambda e: done(True))
        dlg.bind("<Escape>", lambda e: done(False))
        dlg.update_idletasks()
        theme.title_bar(dlg, self.dark)
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        ent.focus_set()
        ent.select_range(0, "end")
        dlg.grab_set()
        self.wait_window(dlg)
        return res["v"]

    # ------------------------------------------------------------ export
    def export(self):
        if self.editing:
            self.end_edit(True)
        fmts = self._formats()
        if not fmts:
            messagebox.showinfo(APP_NAME, "Tick at least one format under “Save as”.")
            return
        done = [i for i in self.tree.get_children() if (self.rows[i].get("result"))]
        sel = [i for i in self.tree.selection() if i in done]
        items = sel or done
        if not items:
            self._flash("Nothing to export yet — press Start to transcribe first.")
            return
        jobs, used = [], set()
        for i in items:
            r = self.rows[i]
            folder = self._outdir_for(r)
            stem = r["file"].stem + ("_en" if r["result"]["task"] == "translate" else "")
            base, n = stem, 2
            while (str(folder).lower(), stem.lower()) in used:     # two inputs with the same name
                stem, n = f"{base} ({n})", n + 1
            used.add((str(folder).lower(), stem.lower()))
            r["outputs"] = []
            jobs.append((i, r, folder, stem))
        self.export_btn.configure(state="disabled")
        ts = self.v_ts.get()
        self._flash(f"Exporting {len(items)} transcript{'s' * (len(items) != 1)} …")

        def run():
            n, err, folders = 0, None, []
            for i, r, folder, stem in jobs:
                try:
                    paths = engine.save(self._view(r["result"]), fmts, folder, timestamps=ts, stem=stem)
                    r["outputs"] = paths
                    n += len(paths)
                    if str(folder) not in folders:
                        folders.append(str(folder))
                except Exception as e:
                    log(f"export failed for {r['file']}: {e}")
                    err = str(e)
            self.events.put(("exported", [j[0] for j in jobs], folders, n, err))
        threading.Thread(target=run, daemon=True).start()

    def open_folder(self):
        r = self._sel_row() or next(iter(self.rows.values()), None)
        if r and r.get("outputs"):
            open_in_explorer(r["outputs"][0])
            return
        d = self._custom_outdir()
        if r and not d:
            d = engine.default_outdir(r["file"])
            if not d.exists():
                open_in_explorer(r["file"])
                self._flash("Nothing exported for this file yet — showing the original. Press Export to save.")
                return
        if d:
            d.mkdir(parents=True, exist_ok=True)
            open_in_explorer(d)
        else:
            self._flash("Drop a file first (exports go to a “Transcripts” folder beside it), or pick Save to….")

    def save_mp3(self):
        r = self.rows.get(self.preview_item)
        if not r:
            self._flash("Select a file in the list first.")
            return
        folder = self._outdir_for(r)
        dst = folder / f"{r['file'].stem}.mp3"
        if dst.resolve() == r["file"].resolve():
            self._flash("That file is already an MP3 in that folder.")
            return
        self.mp3_btn.configure(state="disabled")
        self._flash(f"Saving {dst.name} …", 120)
        src = r["file"]

        def run():
            try:
                folder.mkdir(parents=True, exist_ok=True)
                cached = media.cached_mp3(src)
                if src.suffix.lower() == ".mp3":
                    shutil.copy2(src, dst)
                elif cached.exists():             # already converted for playback
                    shutil.copyfile(cached, dst)
                else:
                    media.to_mp3(src, dst, lambda f: self.events.put(("mp3_prog", f)))
                self.events.put(("mp3", str(dst), None))
            except Exception as e:
                log(f"mp3 failed for {src}: {e}")
                self.events.put(("mp3", str(dst), str(e)))
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ player
    def _close_audio(self):
        self.player.close()
        self.audio_state = None
        self.pending_seek = None
        self.v_pos.set(0)

    def _prepare_audio(self, then_play_at=None):
        r = self.rows.get(self.preview_item)
        if not r:
            return
        self.audio_item = self.preview_item
        self.pending_seek = then_play_at
        if self.audio_state == "preparing":
            return
        self.audio_state = "preparing"
        item, src = self.preview_item, r["file"]
        self.time_lbl.configure(text="preparing …")
        self.play_btn.configure(state="disabled")

        def run():
            try:
                path = media.playable(src, lambda f: self.events.put(("audio_prog", item, f)))
                self.events.put(("audio", item, str(path), None))
            except Exception as e:
                log(f"playback prep failed for {src}: {e}")
                self.events.put(("audio", item, None, str(e)))
        threading.Thread(target=run, daemon=True).start()

    def play_at(self, t):
        if not self.rows.get(self.preview_item):
            return
        if self.audio_state != "ready":
            if self.audio_state != "failed":
                self._prepare_audio(then_play_at=t)
            return
        try:
            self.player.play(t)
        except RuntimeError as e:
            self._flash(f"Playback error: {e}")
        self._player_ui()

    def toggle_play(self):
        if not self.rows.get(self.preview_item):
            return
        if self.audio_state != "ready":
            if self.audio_state != "failed":
                self._prepare_audio(then_play_at=self.v_pos.get())
            return
        try:
            if self.player.playing():
                self.player.pause()
            else:
                pos = self.player.position()
                self.player.play(0 if self.player.length and pos >= self.player.length - 0.3 else pos)
        except RuntimeError as e:
            self._flash(f"Playback error: {e}")
        self._player_ui()

    def _seek_release(self, e):
        self.dragging = False
        t = self.v_pos.get()
        if self.audio_state == "ready":
            try:
                self.player.seek(t)
            except RuntimeError:
                pass
        self._highlight(t)
        self._player_ui()

    def _vol_drag(self, e):
        self._vol_set(self.vol.get(e.x, e.y))
        return "break"

    def _vol_set(self, v):
        v = max(0.0, min(1.0, float(v)))
        self.v_vol.set(v)
        self._volume(v)

    def _seek_drag(self, e):
        self.dragging = True
        t = self.seek.get(e.x, e.y)
        self.v_pos.set(t)
        self.time_lbl.configure(text=f"{fmt_hms(t)} / {fmt_hms(self._length())}")
        return "break"

    def _volume(self, v):
        self.player.set_volume(v)
        self.vol_lbl.configure(text=I_MUTE if v < 0.01 else I_VOL[0] if v < 0.34 else I_VOL[1] if v < 0.67
                               else I_VOL[2])
        self.vol_pct.configure(text=f"{v:.0%}")
        if not getattr(self, "_vol_save", None):
            self._vol_save = self.after(800, lambda: (self._save(), setattr(self, "_vol_save", None)))

    def _mute(self):
        v = self.v_vol.get()
        if v > 0.01:
            self._unmute = v
            self.v_vol.set(0)
            self._volume(0)
        else:
            self.v_vol.set(getattr(self, "_unmute", 0.8))
            self._volume(self.v_vol.get())

    def _length(self):
        if self.audio_state == "ready" and self.player.length:
            return self.player.length
        r = self.rows.get(self.preview_item)
        return (r or {}).get("duration") or 0

    def _player_ui(self):
        r = self.rows.get(self.preview_item)
        has = r is not None
        playing = self.audio_state == "ready" and self.player.playing()
        self.play_btn.configure(text=I_PAUSE if playing else I_PLAY,
                                state="normal" if has and self.audio_state != "preparing" else "disabled")
        self.seek.state(["!disabled"] if has else ["disabled"])
        self.mp3_btn.configure(state="normal" if has else "disabled")
        can_edit = has and r.get("result") is not None and r["status"] != "RUN"
        self.edit_btn.configure(state="normal" if can_edit or self.editing else "disabled")
        has_spk = has and bool((r.get("result") or {}).get("speakers"))
        self.showspk_cb.state(["!disabled"] if has_spk else ["disabled"])
        length = self._length()
        self.seek.configure(to=max(length, 1))
        if self.audio_state != "preparing":
            self.time_lbl.configure(text=f"{fmt_hms(self.v_pos.get())} / {fmt_hms(length)}")

    def _poll_player(self):
        try:
            if self.audio_state == "ready" and not self.dragging:
                playing = self.player.playing()
                pos = self.player.position()
                self.v_pos.set(pos)
                self.time_lbl.configure(text=f"{fmt_hms(pos)} / {fmt_hms(self._length())}")
                if playing:
                    self._highlight(pos)
                    self.trans_tab.follow(pos)
                want = I_PAUSE if playing else I_PLAY
                if self.play_btn.cget("text") != want:
                    self.play_btn.configure(text=want)
        except Exception as e:
            log(f"player poll: {e}")
        self.after(200, self._poll_player)

    # ------------------------------------------------------------ close
    def _close(self):
        if self.editing:
            self.end_edit(True)
        if self.running:
            if not messagebox.askyesno(APP_NAME, "A transcription is running. Stop it and quit?"):
                return
        else:
            unsaved = [r for r in self.rows.values() if r.get("result") and r.get("dirty")]
            if unsaved and not messagebox.askyesno(
                    APP_NAME, f"{len(unsaved)} transcript{'s have' if len(unsaved) != 1 else ' has'}n't been "
                              "exported yet and will be lost.\n\nQuit anyway?"):
                return
        mw = self.models_win
        if mw is not None and mw.wk is not None:
            mw.wk.kill()
        uw = self.update_win
        if uw is not None and uw.mwk is not None:
            uw.mwk.kill()
        self.ai.close()              # a download in the Models window (resumes next time)
        self.player.close()
        self.wk.close()
        self.destroy()
