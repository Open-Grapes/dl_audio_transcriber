"""The transcript in a bigger window of its own (the ⤢ button on the transcript card): the same text, Pinyin and
English lines, and the player (play, position, volume). Click a sentence to play it; Space plays / pauses."""

import tkinter as tk
from tkinter import ttk

from . import theme
from .common import IS_MAC


class BigTranscript:
    def __init__(self, app):
        self.app = app
        w = self.win = tk.Toplevel(app)
        w.title("Transcript")
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        W, H = min(1200, sw - 120), min(900, sh - 120)
        w.geometry(f"{W}x{H}+{max(0, app.winfo_rootx() + 60)}+{max(0, app.winfo_rooty() + 30)}")
        w.minsize(560, 380)
        w.configure(bg=app.pal["bg"])
        mint = theme.mint()
        outer = ttk.Frame(w, style="Page.TFrame" if mint else "TFrame", padding=(18, 16, 18, 18) if mint else 10)
        outer.pack(fill="both", expand=True)
        card = ttk.Frame(outer, style="Card.TFrame" if mint else "TFrame", padding=(24, 16, 24, 16) if mint else 4)
        card.pack(fill="both", expand=True)

        hdr = ttk.Frame(card)
        hdr.pack(fill="x")
        self.name = ttk.Label(hdr, text="Transcript", style="Title.TLabel")
        self.name.pack(side="left")
        ttk.Checkbutton(hdr, text="Timestamps", variable=app.v_ts).pack(side="right")   # close it with the window's X (or Esc)
        self.en_cb = ttk.Checkbutton(hdr, text="English", variable=app.v_en)
        self.en_cb.pack(side="right", padx=14)
        self.py_cb = ttk.Checkbutton(hdr, text="Pinyin", variable=app.v_py)
        self.py_cb.pack(side="right")
        if mint:
            ttk.Frame(card, style="Line.TFrame", height=1).pack(fill="x", pady=(12, 6))

        self.bar = app.player_bar(card, app.toggle_play)
        self.bar.frame.pack(side="bottom", fill="x", pady=(10, 0))
        body = ttk.Frame(card)
        body.pack(fill="both", expand=True, pady=(6 if not mint else 0, 0))
        self.text = tk.Text(body, wrap="word", font=(theme.TEXT, 14 + theme._S), relief="flat",
                            padx=8 if mint else 16, pady=8 if mint else 12, borderwidth=0 if mint else 1,
                            highlightthickness=0 if mint else 1, spacing1=5, spacing3=5, cursor="arrow")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        t = self.text
        t.tag_bind("seg", "<ButtonRelease-1>", app._click_seg)
        t.bind("<Button-1>", app._text_press, add="+")
        t.tag_bind("seg", "<Enter>", lambda e: t.configure(cursor="hand2"))
        t.tag_bind("seg", "<Leave>", lambda e: t.configure(cursor="arrow"))
        t.bind("<Control-a>" if not IS_MAC else "<Command-a>", lambda e: (t.tag_add("sel", "1.0", "end"), "break")[1])
        for wd in (w, t):
            wd.bind("<space>", lambda e: (app.toggle_play(), "break")[1])
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.recolor()
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        try:
            from .gui import set_icon
            set_icon(w)
        except Exception:
            pass

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def title(self, name):
        self.name.configure(text=f"Transcript — {name}" if name else "Transcript")
        self.win.title(name or "Transcript")

    def recolor(self):
        app = self.app
        self.win.configure(bg=app.pal["bg"])
        app._style_text(self.text, bigger=2)
        theme.title_bar(self.win, app.dark)

    def close(self):
        app = self.app
        if self.bar in app.bars:
            app.bars.remove(self.bar)
        self.win.destroy()
        app.big = None
