"""The app's looks ("themes"), each in light and dark.

Legacy: light uses the native Windows / Mac style, dark uses 'clam' recoloured (the look up to 1.7).
Fresh Mint (the default from 1.8): soft mint colours, rounded cards, pill buttons, switches, the Nunito font
and a sidebar menu, light or dark. Its drawn pieces live in mint.py.
The theme is chosen at start-up (it changes the layout), so switching it restarts the app; light / dark
switches straight away."""

import ctypes
import os
import sys
import tkinter as tk
from tkinter import ttk

IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
UI = "Segoe UI" if IS_WIN else "Helvetica Neue" if IS_MAC else "DejaVu Sans"
MONO = "Consolas" if IS_WIN else "Menlo" if IS_MAC else "DejaVu Sans Mono"
# Transcript text: Microsoft YaHei UI = Segoe UI's Latin letters + regular-weight Chinese/Japanese (with plain
# Segoe UI, Windows falls back to a font that draws CJK text looking bold).
TEXT = "Microsoft YaHei UI" if IS_WIN else UI
_S = 0 if IS_WIN else 3   # macOS text needs to be a few points larger to look the same

LIGHT = {
    "dark": False, "bg": "SystemButtonFace" if IS_WIN else "systemWindowBackgroundColor" if IS_MAC else "#ececec", "panel": "#f3f4f6", "field": "#ffffff", "text": "#1d2330",
    "muted": "#555b66", "faint": "#777d88", "line": "#d0d4da", "accent": "#205cd6",
    "tags": {"RUN": "#205cd6", "DONE": "#2e7d32", "FAIL": "#c62828", "WAIT": "#6b7280",
             "SKIP": "#6b7280"},
}
DARK = {
    "dark": True, "bg": "#1c1d22", "panel": "#26282f", "field": "#202127", "text": "#e6e7ea",
    "muted": "#a3a8b3", "faint": "#7f8590", "line": "#3a3d46", "accent": "#3b82f6", "hover": "#30333b",
    "pressed": "#383c46", "select": "#2c4a7c",
    "tags": {"RUN": "#6aa5ff", "DONE": "#5cc46a", "FAIL": "#ff7070", "WAIT": "#9aa0aa",
             "SKIP": "#9aa0aa"},
}


MINT_LIGHT = {
    "dark": False, "bg": "#f1f6f4", "panel": "#e7f0ec", "field": "#ffffff", "card": "#ffffff", "text": "#1d2b28",
    "muted": "#677a75", "faint": "#98a8a3", "line": "#d5e2dd", "accent": "#0f9f7b", "accent2": "#e2f6ef",
    "accent_hi": "#14b88f", "accent_lo": "#0b8667", "accent_fg": "#0b6b53", "hover": "#dcebe5",
    "pressed": "#cfe4dc", "select": "#bfe8da", "thumb": "#c3d3cd", "thumb_hi": "#a8bdb5", "grip": "#7f9690",
    "sash": "#f1f6f4", "coral": "#c4542f", "coral_bg": "#ffe6dc", "coral_bg_hi": "#ffd7c8",
    "fill": "#f1f6f4", "track_off": "#d3dedb", "track": "#dde8e4", "card_line": "#e6efeb",
    "drop": "#f4fbf8", "drop_hot": "#e9f7f1", "drop_line": "#9fd8c5", "wave": "#9fdcc8",
    "tags": {"RUN": "#2f5bd3", "DONE": "#0b8667", "FAIL": "#c62828", "WAIT": "#677a75", "SKIP": "#677a75"},
}
MINT_DARK = {
    "dark": True, "bg": "#131817", "panel": "#1f2725", "field": "#1b2120", "card": "#1b2120", "text": "#e6ecea",
    "muted": "#a0aeaa", "faint": "#7d8c88", "line": "#2f3a37", "accent": "#3fd0a8", "accent2": "#1d3a32",
    "accent_hi": "#5fdcb8", "accent_lo": "#2fb592", "accent_fg": "#7fe6c8", "hover": "#26302e",
    "pressed": "#2d3936", "select": "#24534a", "thumb": "#3a4643", "thumb_hi": "#4c5a56", "grip": "#e6ecea",
    "sash": "#131817", "coral": "#ffb59c", "coral_bg": "#4a2a20", "coral_bg_hi": "#5a3428",
    "fill": "#242c2a", "track_off": "#3a4643", "track": "#2f3a37", "card_line": "#252e2c",
    "drop": "#172220", "drop_hot": "#1b2a27", "drop_line": "#2f6a5a", "wave": "#2f8f74",
    "tags": {"RUN": "#7aa6ff", "DONE": "#5fdcb8", "FAIL": "#ff7b72", "WAIT": "#a0aeaa", "SKIP": "#a0aeaa"},
}

THEMES = {"mint": "Fresh Mint", "legacy": "Legacy"}     # key -> name shown in the Theme window
DEFAULT_THEME = "mint"


def _saved_style():
    try:
        from .common import load_settings
        ui = load_settings().get("ui_style")
    except Exception:
        ui = None
    return ui if ui in THEMES else DEFAULT_THEME


# The theme is read here, before any window code imports UI, because Fresh Mint uses its own font (Nunito).
STYLE = _saved_style()
if STYLE == "mint":
    try:
        from . import mint as _mint
        if _mint.load_fonts():
            UI = _mint.FONT
    except Exception:
        pass


def mint():
    return STYLE == "mint"


def palette(dark):
    if mint():
        return MINT_DARK if dark else MINT_LIGHT
    return DARK if dark else LIGHT


def apply(root, dark):
    """Restyle every ttk widget under root. Returns the palette used."""
    p = palette(dark)
    style = ttk.Style(root)
    if mint():
        from . import mint as _mint
        _mint.use_theme(style, p, dark)
        _mint.configure(style, p, dark, UI, _S)
        _finish(root, style, p, dark)
        return p
    if dark:
        style.theme_use("clam")
        bg, panel, field, text, line = p["bg"], p["panel"], p["field"], p["text"], p["line"]
        style.configure(".", background=bg, foreground=text, fieldbackground=field, bordercolor=line,
                        darkcolor=bg, lightcolor=bg, troughcolor=panel, focuscolor=p["accent"],
                        selectbackground=p["select"], selectforeground=text, insertcolor=text,
                        font=(UI, (10 if mint() else 9) + _S))
        style.map(".", foreground=[("disabled", p["faint"])])
        style.configure("TButton", background=panel, bordercolor=panel if mint() else line,
                        padding=(12, 6) if mint() else (10, 4), relief="flat")
        style.map("TButton", background=[("pressed", p["pressed"]), ("active", p["hover"]), ("disabled", bg)],
                  bordercolor=[("focus", p["accent"])])
        style.configure("Toolbutton", background=bg, padding=(10, 4), relief="flat")
        style.map("Toolbutton", background=[("selected", p["accent"]), ("active", p["hover"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TRadiobutton", background=bg, indicatorbackground=field, indicatorforeground=text,
                        upperbordercolor=line, lowerbordercolor=line)
        style.map("TRadiobutton", background=[("active", bg)],
                  indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("TCheckbutton", background=bg, indicatorbackground=field, indicatorforeground=text,
                        upperbordercolor=line, lowerbordercolor=line)
        style.map("TCheckbutton", background=[("active", bg)],
                  indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("Treeview", background=field, fieldbackground=field, foreground=text, bordercolor=line)
        style.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", text)])
        style.configure("Treeview.Heading", background=panel, foreground=text, bordercolor=line, relief="flat")
        style.map("Treeview.Heading", background=[("active", p["hover"])])
        # clam's scrollbar arrows look odd in dark mode: just a plain thumb, like modern Windows apps
        style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        thumb, hi = p.get("thumb", "#4a4e58"), p.get("thumb_hi", "#5d626e")
        style.configure("Vertical.TScrollbar", background=thumb, troughcolor=bg, bordercolor=bg,
                        lightcolor=thumb, darkcolor=thumb, gripcount=0, relief="flat", arrowsize=10)
        style.map("Vertical.TScrollbar", background=[("active", hi), ("pressed", hi)],
                  lightcolor=[("active", hi), ("pressed", hi)], darkcolor=[("active", hi), ("pressed", hi)])
        # read-only comboboxes default to a light grey field in 'clam', which made the Model box look washed out
        style.configure("TCombobox", fieldbackground=field, background=panel, foreground=text, arrowcolor=text,
                        bordercolor=line, selectbackground=field, selectforeground=text, padding=(6, 3))
        style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", bg)],
                  foreground=[("readonly", text), ("disabled", p["faint"])],
                  selectbackground=[("readonly", field), ("focus", p["select"])],
                  selectforeground=[("readonly", text)], background=[("active", p["hover"])],
                  bordercolor=[("focus", p["accent"])], arrowcolor=[("disabled", p["faint"])])
        style.configure("TEntry", fieldbackground=field, foreground=text, bordercolor=line, padding=(6, 3))
        style.map("TEntry", bordercolor=[("focus", p["accent"])])
        style.configure("Horizontal.TScale", background=p["accent"], troughcolor=panel, bordercolor=line,
                        lightcolor=panel, darkcolor=panel, gripcount=0, sliderlength=12)
        style.map("Horizontal.TScale", background=[("active", p.get("accent_hi", "#60a5fa"))])
        on_accent = "#0b201b" if (mint() and dark) else "#ffffff"
        style.configure("Accent.TButton", background=p["accent"], foreground=on_accent, bordercolor=p["accent"])
        if mint():
            style.configure("Accent.TButton", font=(UI, 10 + _S, "bold"))
        style.map("Accent.TButton", background=[("pressed", p.get("accent_lo", "#2563eb")),
                                                ("active", p.get("accent_hi", "#4f8ff7")), ("disabled", panel)],
                  foreground=[("disabled", p["faint"])])
        style.configure("TProgressbar", background=p["accent"], troughcolor=panel, bordercolor=line)
        style.configure("TNotebook", background=bg, bordercolor=line, lightcolor=bg, darkcolor=bg,
                        tabmargins=(4, 4, 4, 0))
        style.configure("TNotebook.Tab", background=panel, foreground=p["muted"], bordercolor=line,
                        lightcolor=panel, darkcolor=panel, padding=(14, 6), font=(UI, 10 + _S))
        style.map("TNotebook.Tab", background=[("selected", bg), ("active", p["hover"])],
                  foreground=[("selected", text)], lightcolor=[("selected", bg)],
                  expand=[("selected", (0, 2, 0, 0))])
        style.configure("Drop.TLabel", background=panel, bordercolor=line)
        if mint():
            _mint_extras(style, p, dark)
    else:
        # ttk keeps settings per theme, so the dark colours above don't leak into the native look.
        names = style.theme_names()
        style.theme_use(next(t for t in ("vista", "aqua", "winnative", "clam") if t in names))
    style.configure("Drop.TLabel", font=(UI, 13 + _S), foreground=p["muted"], anchor="center")
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Small.TLabel", foreground=p["faint"], font=(UI, 8 + _S))
    style.configure("Title.TLabel", font=(UI, 11 + _S, "bold"))
    style.configure("Treeview", rowheight=30 if mint() else 26)
    style.configure("Time.TLabel", font=(MONO, 9 + _S), foreground=p["muted"])
    icon_font = "Segoe MDL2 Assets" if IS_WIN else UI
    style.configure("Play.TButton", font=(icon_font, 12 + _S), padding=(10, 4), width=3)
    if mint():
        style.configure("Stop.TButton", background=p["coral_bg"], foreground=p["coral"], bordercolor=p["coral_bg"],
                        font=(UI, 10 + _S, "bold"))
        style.map("Stop.TButton", background=[("pressed", p["coral_bg"]), ("active", p["coral_bg"])])
    elif dark:
        style.configure("Stop.TButton", background="#dc2626", foreground="#ffffff", bordercolor="#dc2626")
        style.map("Stop.TButton", background=[("pressed", "#b91c1c"), ("active", "#ef4444")])
    else:
        style.configure("Stop.TButton", foreground="#c62828", font=(UI, 9 + _S, "bold"))
    _finish(root, style, p, dark)
    return p


def _finish(root, style, p, dark):
    _sash(style, p, dark)
    # the drop-down list of a combobox is a plain Tk listbox
    for k, v in (("background", p["field"] if dark else "#ffffff"), ("foreground", p["text"]),
                 ("selectBackground", p.get("select", "#cce0ff") if dark else "#cce0ff"),
                 ("selectForeground", p["text"])):
        root.option_add(f"*TCombobox*Listbox.{k}", v)
    root.configure(bg=p["bg"])
    for w in _toplevels(root):
        try:
            w.configure(bg=p["bg"])
        except tk.TclError:
            pass
        title_bar(w, dark)


def _sash(style, p, dark):
    """The divider you drag between two boxes: a clear grip (light in dark mode) so it's easy to find."""
    grip = p.get("grip") or ("#e6e7ea" if dark else "#6b7280")
    bar = p.get("sash") or (p["panel"] if dark else "#e3e6ea")
    try:
        style.configure("Sash", sashthickness=10, gripcount=24, background=bar, lightcolor=grip,
                        darkcolor=grip, bordercolor=grip, sashrelief="flat")
    except tk.TclError:
        pass
    # the native Windows / Mac look ('vista', 'aqua') draws no grip whatever the style says: draw our own
    native = style.theme_use() not in ("clam", "mint_light", "mint_dark")
    for g in list(_GRIPS):
        g.show(native, grip)


_GRIPS = []


class SashGrip:
    """A small handle drawn on the divider of a vertical ttk.PanedWindow (first divider) when the native look
    wouldn't show one. It can be dragged like the divider itself."""

    def __init__(self, pw):
        self.pw = pw
        self.on = False
        self.f = tk.Frame(pw, width=64, height=4, cursor="sb_v_double_arrow", bg="#6b7280")
        self.f.bind("<B1-Motion>", self._drag)
        for ev in ("<Configure>", "<ButtonRelease-1>", "<B1-Motion>"):
            pw.bind(ev, lambda e: pw.after_idle(self.place), add="+")
        _GRIPS.append(self)
        pw.bind("<Destroy>", lambda e: self in _GRIPS and e.widget is pw and _GRIPS.remove(self), add="+")
        st = ttk.Style(pw)
        self.show(st.theme_use() not in ("clam", "mint_light", "mint_dark"), st.lookup("Sash", "lightcolor") or "#6b7280")

    def show(self, on, colour):
        self.on = on
        try:
            self.f.configure(bg=colour)
            self.place()
        except tk.TclError:
            pass

    def place(self):
        try:
            if not self.on or len(self.pw.panes()) < 2:
                self.f.place_forget()
                return
            y = self.pw.sashpos(0)
            self.f.place(relx=0.5, y=y + 5, anchor="center")
            self.f.lift()
        except tk.TclError:
            pass

    def _drag(self, e):
        try:
            self.pw.sashpos(0, max(40, e.y_root - self.pw.winfo_rooty()))
            self.place()
        except tk.TclError:
            pass


def _mint_extras(style, p, dark):
    """Fresh Mint: the sidebar menu, cards and soft accent chips."""
    bg, card, text = p["bg"], p["card"], p["text"]
    # the tabs are in the sidebar menu, so the notebook's own tab row is hidden
    style.layout("Side.TNotebook.Tab", [])
    style.configure("Side.TNotebook", background=bg, borderwidth=0, bordercolor=bg, lightcolor=bg, darkcolor=bg,
                    tabmargins=0)
    style.configure("Side.TFrame", background=card)
    style.configure("SideLine.TFrame", background=p["line"])
    style.configure("Side.TLabel", background=card, foreground=p["muted"])
    style.configure("Brand.TLabel", background=card, foreground=text, font=(UI, 14 + _S, "bold"))
    style.configure("Logo.TLabel", background=p["accent"], foreground="#ffffff", font=(UI, 13 + _S, "bold"),
                    padding=(8, 3), anchor="center")
    style.configure("SideHead.TLabel", background=card, foreground=p["faint"], font=(UI, 8 + _S, "bold"))
    for name, fg, bgc, font in (("Nav", p["muted"], card, (UI, 11 + _S, "bold")),
                                ("NavOn", p["accent_fg"], p["accent2"], (UI, 11 + _S, "bold")),
                                ("Tool", p["muted"], card, (UI, 10 + _S))):
        style.configure(f"{name}.TButton", background=bgc, foreground=fg, bordercolor=bgc, lightcolor=bgc,
                        darkcolor=bgc, anchor="w", padding=(14, 7) if name == "Tool" else (14, 9), font=font)
        style.map(f"{name}.TButton",
                  background=[("pressed", p["pressed"]), ("active", p["accent2"] if name == "NavOn" else p["hover"])],
                  bordercolor=[("focus", bgc)], foreground=[("active", p["accent_fg"] if name == "NavOn" else text)])
    style.configure("ToolAccent.TButton", background=p["accent"], foreground="#0b201b" if dark else "#ffffff",
                    bordercolor=p["accent"], anchor="w", padding=(14, 7), font=(UI, 10 + _S, "bold"))
    style.map("ToolAccent.TButton", background=[("active", p["accent_hi"]), ("pressed", p["accent_lo"])])
    style.configure("Pill.TLabel", background=p["accent2"], foreground=p["accent_fg"], padding=(12, 4),
                    font=(UI, 9 + _S, "bold"))
    style.configure("Hero.TLabel", font=(UI, 17 + _S, "bold"), foreground=text)
    style.configure("Drop.TLabel", background=p["accent2"], bordercolor=p["accent"], lightcolor=p["accent2"],
                    darkcolor=p["accent2"])
    style.configure("Treeview.Heading", font=(UI, 9 + _S, "bold"), foreground=p["faint"], padding=(6, 6))
    style.configure("TProgressbar", background=p["accent"], troughcolor=p["accent2"], bordercolor=p["accent2"],
                    lightcolor=p["accent"], darkcolor=p["accent"], thickness=10)


def _toplevels(root):
    out = [root]
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel):
            out.append(w)
    return out


def title_bar(win, dark):
    """Ask Windows 10/11 to draw this window's title bar dark (or light). (macOS: follows the system.)"""
    if not IS_WIN:
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # Redraw the frame so the change shows immediately.
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
    except Exception:
        pass
