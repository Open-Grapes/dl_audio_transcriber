"""The Theme window: pick how the app looks (Fresh Mint or Legacy), then Apply.

A theme changes the layout (Fresh Mint has a menu on the left), and the window is built once at start-up,
so a new theme takes effect after a restart: the window offers a Restart now button that reopens the app.
Light / dark mode still switches straight away with its own button."""

import tkinter as tk
from tkinter import ttk

from . import theme
from .common import APP_NAME

# what each theme looks like (shown in the window)
INFO = {
    "mint": ("A fresh, modern look: soft mint colours, rounded cards, switches and a menu on the left with the "
             "tabs and tools. The default.", ("#f1f6f4", "#ffffff", "#0f9f7b", "#e2f6ef"), ("#131817", "#1b2120", "#3fd0a8", "#1d3a32")),
    "legacy": ("The original look: the standard Windows / Mac style, with the tabs and buttons across the top.", ("#f0f0f0", "#ffffff", "#205cd6", "#d0d4da"),
               ("#1c1d22", "#26282f", "#3b82f6", "#3a3d46")),
}


class ThemeWindow:
    def __init__(self, app):
        self.app = app
        old = getattr(app, "theme_win", None)
        if old is not None:
            try:
                old.win.lift()
                old.win.focus_force()
                return
            except tk.TclError:
                pass
        app.theme_win = self
        p = app.pal
        w = self.win = tk.Toplevel(app)
        w.title("Theme")
        w.transient(app)
        w.resizable(False, False)
        w.configure(bg=p["bg"])
        self.f = ttk.Frame(w, padding=(22, 18, 22, 18))
        self.f.pack(fill="both", expand=True)
        self.v = tk.StringVar(value=theme.STYLE)
        self._choose()
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        y = app.winfo_rooty() + 120
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        w.grab_set()

    def _clear(self):
        for c in self.f.winfo_children():
            c.destroy()

    def _choose(self):
        self._clear()
        f, p = self.f, self.app.pal
        ttk.Label(f, text="Choose a theme", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Each theme has a light and a dark mode (the Light / Dark mode button switches it).",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        for key, name in theme.THEMES.items():
            desc, light, dark = INFO[key]
            row = ttk.Frame(f, padding=(0, 6))
            row.pack(fill="x")
            ttk.Radiobutton(row, text=name + ("   (in use)" if key == theme.STYLE else ""), value=key,
                            variable=self.v).pack(anchor="w")
            sw = tk.Frame(row, bg=p["bg"])
            sw.pack(anchor="w", padx=(26, 0), pady=(4, 2))
            for colours in (light, dark):            # a little sample of its light and dark colours
                box = tk.Frame(sw, bg=colours[0], highlightthickness=1, highlightbackground=p["line"])
                box.pack(side="left", padx=(0, 8))
                for c in colours[1:]:
                    tk.Frame(box, bg=c, width=26, height=16).pack(side="left", padx=3, pady=4)
            ttk.Label(row, text=desc, style="Muted.TLabel", wraplength=420, justify="left").pack(anchor="w",
                                                                                             padx=(26, 0))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(16, 0))
        ttk.Button(b, text="Apply", style="Accent.TButton", command=self.apply, width=10).pack(side="right")
        ttk.Button(b, text="Cancel", command=self.close).pack(side="right", padx=8)

    def apply(self):
        key = self.v.get()
        if key == theme.STYLE:
            self.close()
            return
        app = self.app
        app.s["ui_style"] = key
        from .common import save_settings
        save_settings(app.s)                     # used the next time the app opens, restart or not
        self._clear()
        f = self.f
        name = theme.THEMES[key]
        ttk.Label(f, text=f"{name} is ready", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=f"{APP_NAME} needs to restart to change its look (it takes a few seconds). "
                          "Your settings are kept. Transcripts in the list that you haven't exported will be lost "
                          "(you'll be asked first).\n\nChoose Later to switch the next time you open the app.",
                  wraplength=440, justify="left").pack(anchor="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(18, 0))
        ttk.Button(b, text="Restart now", style="Accent.TButton", command=self.restart, width=14).pack(side="right")
        ttk.Button(b, text="Later", command=self.close).pack(side="right", padx=8)

    def restart(self):
        self.close()
        self.app.after(50, self.app.restart)

    def close(self):
        self.app.theme_win = None
        try:
            self.win.grab_release()
            self.win.destroy()
        except tk.TclError:
            pass
