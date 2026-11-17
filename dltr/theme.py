"""Light and dark looks for the window (ttk). Light uses the native Windows style; dark uses 'clam' recoloured."""

import ctypes
import os
import sys
import tkinter as tk
from tkinter import ttk

IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
UI = "Segoe UI" if IS_WIN else "Helvetica Neue" if IS_MAC else "DejaVu Sans"
MONO = "Consolas" if IS_WIN else "Menlo" if IS_MAC else "DejaVu Sans Mono"
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


def palette(dark):
    return DARK if dark else LIGHT


def apply(root, dark):
    """Restyle every ttk widget under root. Returns the palette used."""
    p = palette(dark)
    style = ttk.Style(root)
    if dark:
        style.theme_use("clam")
        bg, panel, field, text, line = p["bg"], p["panel"], p["field"], p["text"], p["line"]
        style.configure(".", background=bg, foreground=text, fieldbackground=field, bordercolor=line,
                        darkcolor=bg, lightcolor=bg, troughcolor=panel, focuscolor=p["accent"],
                        selectbackground=p["select"], selectforeground=text, insertcolor=text,
                        font=(UI, 9 + _S))
        style.map(".", foreground=[("disabled", p["faint"])])
        style.configure("TButton", background=panel, bordercolor=line, padding=(10, 4), relief="flat")
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
        thumb = "#4a4e58"
        style.configure("Vertical.TScrollbar", background=thumb, troughcolor=bg, bordercolor=bg,
                        lightcolor=thumb, darkcolor=thumb, gripcount=0, relief="flat", arrowsize=10)
        style.map("Vertical.TScrollbar", background=[("active", "#5d626e"), ("pressed", "#6b7180")],
                  lightcolor=[("active", "#5d626e"), ("pressed", "#6b7180")],
                  darkcolor=[("active", "#5d626e"), ("pressed", "#6b7180")])
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
        style.map("Horizontal.TScale", background=[("active", "#60a5fa")])
        style.configure("Accent.TButton", background=p["accent"], foreground="#ffffff", bordercolor=p["accent"])
        style.map("Accent.TButton", background=[("pressed", "#2563eb"), ("active", "#4f8ff7"),
                                                ("disabled", panel)],
                  foreground=[("disabled", p["faint"])])
        style.configure("TProgressbar", background=p["accent"], troughcolor=panel, bordercolor=line)
        style.configure("Drop.TLabel", background=panel, bordercolor=line)
    else:
        # ttk keeps settings per theme, so the dark colours above don't leak into the native look.
        names = style.theme_names()
        style.theme_use(next(t for t in ("vista", "aqua", "winnative", "clam") if t in names))
    style.configure("Drop.TLabel", font=(UI, 13 + _S), foreground=p["muted"], anchor="center")
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Small.TLabel", foreground=p["faint"], font=(UI, 8 + _S))
    style.configure("Title.TLabel", font=(UI, 11 + _S, "bold"))
    style.configure("Treeview", rowheight=26)
    style.configure("Time.TLabel", font=(MONO, 9 + _S), foreground=p["muted"])
    icon_font = "Segoe MDL2 Assets" if IS_WIN else UI
    style.configure("Play.TButton", font=(icon_font, 12 + _S), padding=(10, 4), width=3)
    if dark:
        style.configure("Stop.TButton", background="#dc2626", foreground="#ffffff", bordercolor="#dc2626")
        style.map("Stop.TButton", background=[("pressed", "#b91c1c"), ("active", "#ef4444")])
    else:
        style.configure("Stop.TButton", foreground="#c62828", font=(UI, 9 + _S, "bold"))
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
    return p


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
