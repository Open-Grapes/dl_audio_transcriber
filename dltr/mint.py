"""Fresh Mint look kit.

Tk has no rounded corners, soft shadows or on/off switches, so Fresh Mint draws them: every rounded piece
(cards, pill buttons, input fields, switches, format chips, progress bar, slider knob) is a small picture made
here with Pillow and handed to ttk as an "image element". Each light / dark look is its own ttk theme
(mint_light / mint_dark, based on 'clam'), so switching light / dark just swaps themes.

Also here: the Nunito font (bundled in assets/fonts, SIL Open Font License), line icons (Windows' own Segoe
icon font as a fallback; normally the bundled Lucide icons) and the drop area of the Transcribe tab (a canvas with a dashed rounded border)."""

import base64
import io
import os
import sys
import tkinter as tk

IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
FONT, FONT_X = "Nunito", "Nunito ExtraBold"


def heading(size, ui=None):
    """Font for headings: Nunito ExtraBold on Windows; on the Mac that family name isn't found, so Nunito Bold."""
    if not _fonts_ok:
        return (ui or "Segoe UI", size, "bold")
    return (FONT, size, "bold") if IS_MAC else (FONT_X, size)
_fonts_ok = None


def _font_dir():
    from .common import asset
    p = asset("fonts", "Nunito-Regular.ttf")
    return p.parent if p else None


def load_fonts():
    """Make the bundled Nunito usable by this app only (nothing is installed on the computer). True if it worked."""
    global _fonts_ok
    if _fonts_ok is not None:
        return _fonts_ok
    _fonts_ok = False
    d = _font_dir()
    if not d:
        return False
    files = [str(d / f"Nunito-{w}.ttf") for w in ("Regular", "Bold", "ExtraBold")]
    try:
        if IS_WIN:
            import ctypes
            n = sum(ctypes.windll.gdi32.AddFontResourceExW(f, 0x10, 0) > 0 for f in files)   # FR_PRIVATE
            _fonts_ok = n == len(files)
        elif IS_MAC:
            import ctypes
            import ctypes.util
            ct = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
            cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                                   ctypes.c_bool]
            ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            ok = 0
            for f in files:
                b = f.encode()
                url = cf.CFURLCreateFromFileSystemRepresentation(None, b, len(b), False)
                ok += bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))     # kCTFontManagerScopeProcess
            _fonts_ok = ok == len(files)
    except Exception:
        _fonts_ok = False
    return _fonts_ok


# ---------------------------------------------------------------- icons (Windows' Segoe icon font)
ICONS = {"mic": "\ue720", "translate": "\ue8c1", "summary": "\ue8a5", "chat": "\ue8bd", "models": "\ue7b8",
         "wifi": "\ue701", "update": "\ue895", "moon": "\ue708", "sun": "\ue706", "theme": "\ue790",
         "upload": "\ue898", "shield": "\uea18", "find": "\ue721", "people": "\ue716", "edit": "\ue70f",
         "copy": "\ue8c8", "music": "\uec4f", "video": "\ue714", "folder": "\ue8b7", "download": "\ue896",
         "check": "\ue73e", "globe": "\ue774", "book": "\ue82d", "options": "\ue713", "play": "\ue768",
         "stop": "\ue71a", "file": "\ue8a5", "save": "\ue74e"}


# the icons in Lucide (bundled, ISC licence: assets/fonts/LUCIDE-LICENSE.txt)
LUCIDE = {"mic": 0xe118, "translate": 0xe0fe, "summary": 0xe0cc, "chat": 0xe117, "models": 0xe061, "wifi": 0xe1ae,
          "update": 0xe145, "moon": 0xe11e, "sun": 0xe178, "theme": 0xe1dd, "upload": 0xe19e, "shield": 0xe1ff,
          "find": 0xe151, "people": 0xe1a4, "edit": 0xe1f9, "copy": 0xe09e, "music": 0xe122, "video": 0xe1a5,
          "folder": 0xe0d7, "download": 0xe0b2, "check": 0xe06c, "globe": 0xe0e8, "book": 0xe05e, "options": 0xe154,
          "play": 0xe13c, "stop": 0xe167, "file": 0xe0cc, "save": 0xe14d, "chev": 0xe06d}


def _icon_font():
    """(font file, {name: character}) for the line icons, or (None, {}). The bundled Lucide icons (the style of
    the Fresh Mint design) on every computer; Windows' Segoe icons only if Lucide is missing."""
    from .common import asset
    p = asset("fonts", "lucide.ttf")
    if p and os.environ.get("DLTR_ICONS") != "segoe":
        return str(p), {k: chr(v) for k, v in LUCIDE.items()}
    if IS_WIN:
        root = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for f in ("SegoeIcons.ttf", "segmdl2.ttf"):
            q = os.path.join(root, f)
            if os.path.exists(q):
                return q, ICONS
    return None, {}


_IMGS = {}          # keeps every PhotoImage alive (Tk forgets an image when Python drops it)


def _photo(img, key=None):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    ph = tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))
    _IMGS[key or id(ph)] = ph
    return ph


def _scale():
    """Pixels per point-sized pixel: pictures are drawn bigger when Windows display scaling is on."""
    try:
        root = tk._default_root
        k = float(root.tk.call("tk", "scaling")) * 72 / 96
        return max(1.0, min(3.0, k))
    except Exception:
        return 1.0


def icon(name, size, color):
    """A line icon as a picture with a see-through background (None if this computer has no icon font)."""
    key = ("icon", name, size, color)
    if key in _IMGS:
        return _IMGS[key]
    f, table = _icon_font()
    if not f or name not in table:
        return None
    from PIL import Image, ImageDraw, ImageFont
    k = _scale()
    px = max(8, round(size * k))
    ss = 4
    font = ImageFont.truetype(f, px * ss)
    img = Image.new("RGBA", (px * ss, px * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((px * ss / 2, px * ss / 2), table[name], font=font, fill=color, anchor="mm")
    img = img.resize((px, px), Image.LANCZOS)
    return _photo(img, key)


def badge(name, size, fg, bg, out, radius):
    """A rounded square of colour `bg` with an icon on it (the logo, the shield next to the title)."""
    key = ("badge", name, size, fg, bg, out, radius)
    if key in _IMGS:
        return _IMGS[key]
    f, table = _icon_font()
    if not f or name not in table:
        return None
    from PIL import Image, ImageDraw, ImageFont
    k = _scale()
    px, r = round(size * k), round(radius * k)
    img = _rounded(px, px, r, bg, out).convert("RGBA")
    ss = 4
    lay = Image.new("RGBA", (px * ss, px * ss), (0, 0, 0, 0))
    ImageDraw.Draw(lay).text((px * ss / 2, px * ss / 2), table[name], fill=fg, anchor="mm",
                             font=ImageFont.truetype(f, round(px * 0.5) * ss))
    img = Image.alpha_composite(img, lay.resize((px, px), Image.LANCZOS))
    return _photo(img, key)


def dot(color, ring):
    """The little 'ready' dot in the GPU badge."""
    key = ("dot", color, ring)
    if key in _IMGS:
        return _IMGS[key]
    from PIL import Image, ImageDraw
    k = _scale()
    s, ss = round(14 * k), 4
    img = Image.new("RGBA", (s * ss, s * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, s * ss - 1, s * ss - 1], fill=_rgb(color) + (60,))
    b = round(3 * k) * ss
    d.ellipse([b, b, s * ss - 1 - b, s * ss - 1 - b], fill=_rgb(color) + (255,))
    return _photo(img.resize((s, s), Image.LANCZOS), key)


# ---------------------------------------------------------------- drawn pieces
def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _rounded(w, h, r, fill, out, line=None, lw=1, shadow=0, shadow_rgba=(15, 70, 55, 40), inset=0):
    """An opaque picture: a rounded box on the colour `out` (what's around it), with an optional soft shadow."""
    from PIL import Image, ImageDraw, ImageFilter
    ss = 4
    W, H = w * ss, h * ss
    img = Image.new("RGBA", (W, H), _rgb(out) + (255,))
    box = [inset * ss, inset * ss, W - 1 - inset * ss, H - 1 - inset * ss - shadow * ss]
    if shadow:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + shadow * ss // 2, box[2], box[3] + shadow * ss // 2],
                                             r * ss, fill=shadow_rgba)
        sh = sh.filter(ImageFilter.GaussianBlur(shadow * ss / 2))
        img = Image.alpha_composite(img, sh)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, r * ss, fill=_rgb(fill) + (255,),
                        outline=_rgb(line) + (255,) if line else None, width=lw * ss if line else 0)
    return img.resize((w, h), Image.LANCZOS).convert("RGB")


def _switch(on, p, disabled=False):
    from PIL import Image, ImageDraw
    k = _scale()
    w, h, gap = round(36 * k), round(20 * k), round(10 * k)
    ss = 4
    img = Image.new("RGBA", ((w + gap) * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    track = p["accent"] if on else p["track_off"]
    if disabled:
        track = p["track_off"]
    d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], h * ss // 2, fill=_rgb(track) + (255 if not disabled else 140,))
    kr = (h - round(6 * k)) * ss
    x = (w - h + round(3 * k)) * ss if on else round(3 * k) * ss
    y = round(3 * k) * ss
    d.ellipse([x, y, x + kr, y + kr], fill=(255, 255, 255, 255 if not disabled else 200))
    return img.resize((w + gap, h), Image.LANCZOS)


def _knob(p):
    from PIL import Image, ImageDraw
    k = _scale()
    s = round(16 * k)
    ss = 4
    img = Image.new("RGBA", (s * ss, s * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, s * ss - 1, s * ss - 1], fill=_rgb(p["accent"]) + (255,))
    b = round(3 * k) * ss
    d.ellipse([b, b, s * ss - 1 - b, s * ss - 1 - b], fill=(255, 255, 255, 255) if not p["dark"] else _rgb(p["card"]) + (255,))
    return img.resize((s, s), Image.LANCZOS)


def _line(w, h, thick, color, out):
    """A thin rounded line across the middle (the slider's track)."""
    from PIL import Image, ImageDraw
    ss = 4
    img = Image.new("RGB", (w * ss, h * ss), _rgb(out))
    d = ImageDraw.Draw(img)
    y0 = (h - thick) * ss // 2
    d.rounded_rectangle([0, y0, w * ss - 1, y0 + thick * ss - 1], thick * ss // 2, fill=_rgb(color))
    return img.resize((w, h), Image.LANCZOS)


def _check(color, bg):
    from PIL import Image, ImageDraw
    k = _scale()
    s = round(12 * k)
    ss = 4
    img = Image.new("RGBA", ((s + round(5 * k)) * ss, s * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.line([(1.5 * ss * k, 6.5 * ss * k), (4.7 * ss * k, 9.5 * ss * k), (10.5 * ss * k, 3 * ss * k)],
           fill=_rgb(color) + (255,), width=round(2.2 * ss * k), joint="curve")
    return img.resize(((s + round(5 * k)), s), Image.LANCZOS)


def _blank(w, h):
    from PIL import Image
    return Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))


def _el(style, name, imgs, border, sticky="nsew", padding=None):
    """Create an image element once per theme. imgs: normal image, then (state, image) pairs."""
    first, *rest = imgs
    args = [first]
    for st, im in rest:
        args.append((st, im))
    # padding 0: the widget's own padding decides the size (by default ttk would add the border on top)
    kw = {"border": border, "sticky": sticky, "padding": 0 if padding is None else padding}
    try:
        style.element_create(name, "image", *args, **kw)
    except tk.TclError:
        pass


def use_theme(style, p, dark):
    """Switch to mint_light / mint_dark (made the first time), with all its drawn pieces."""
    name = "mint_dark" if dark else "mint_light"
    if name not in style.theme_names():
        style.theme_create(name, parent="clam")
        style.theme_use(name)
        _make_elements(style, p)
    else:
        style.theme_use(name)


def _make_elements(style, p):
    k = _scale()
    R = lambda v: round(v * k)            # noqa: E731
    page, card, fill = p["bg"], p["card"], p["fill"]
    ph = lambda img: _photo(img)          # noqa: E731

    # cards: white rounded boxes with a soft shadow, on the page colour
    c = ph(_rounded(R(48), R(48), R(16), card, page, line=p["card_line"], shadow=R(4)))
    _el(style, "Card.bg", [c], border=R(20))
    style.layout("Card.TFrame", [("Card.bg", {"sticky": "nswe"})])

    # pill buttons (corners match the card they sit on)
    def pill(fill_c, out=card, line=None):
        return ph(_rounded(R(32), R(28), R(13), fill_c, out, line=line))
    btn = [("Btn.bg", fill, p["hover"], p["pressed"], fill),
           ("Acc.bg", p["accent"], p["accent_hi"], p["accent_lo"], p["track_off"]),
           ("Stop.bg", p["coral_bg"], p["coral_bg_hi"], p["coral_bg_hi"], p["coral_bg"]),
           ("NavOn.bg", p["accent2"], p["accent2"], p["accent2"], p["accent2"]),
           ("Ghost.bg", card, p["hover"], p["pressed"], card)]
    for name, n, a, pr, dis in btn:
        _el(style, name, [pill(n), ("disabled", pill(dis)), ("pressed", pill(pr)), ("active", pill(a))],
            border=(R(14), R(13), R(14), R(13)))
    for st, el in (("TButton", "Btn.bg"), ("Accent.TButton", "Acc.bg"), ("Stop.TButton", "Stop.bg"),
                   ("NavOn.TButton", "NavOn.bg"), ("Nav.TButton", "Ghost.bg"), ("Tool.TButton", "Ghost.bg"),
                   ("Ghost.TButton", "Ghost.bg"), ("ToolAccent.TButton", "Acc.bg")):
        style.layout(st, [(el, {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [
            ("Button.label", {"sticky": "nswe"})]})]})])
    # round play button
    circ = lambda f: ph(_rounded(R(34), R(34), R(17), f, card))      # noqa: E731
    _el(style, "Play.bg", [circ(p["accent"]), ("disabled", circ(p["track_off"])), ("pressed", circ(p["accent_lo"])),
                           ("active", circ(p["accent_hi"]))], border=R(16))
    style.layout("Play.TButton", [("Play.bg", {"sticky": "nswe", "children": [("Button.padding", {
        "sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])

    # rounded input fields (Entry, Combobox) with a chevron
    fld = lambda line=None: ph(_rounded(R(32), R(30), R(10), fill, card, line=line))   # noqa: E731
    _el(style, "Field.bg", [fld(), ("focus", fld(p["accent"])), ("disabled", fld())], border=R(12))
    style.layout("TEntry", [("Field.bg", {"sticky": "nswe", "children": [("Entry.padding", {"sticky": "nswe",
                 "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    chev = icon("chev", 12, p["muted"])
    if chev is None:
        from PIL import Image, ImageDraw
        s = R(12) * 4
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(im).line([(s * .2, s * .38), (s * .5, s * .66), (s * .8, s * .38)],
                                fill=_rgb(p["muted"]) + (255,), width=max(4, s // 9), joint="curve")
        chev = ph(im.resize((R(12), R(12)), Image.LANCZOS))
    _el(style, "Chev.img", [chev], border=0, sticky="", padding=(R(4), 0, R(10), 0))
    style.layout("TCombobox", [("Field.bg", {"sticky": "nswe", "children": [
        ("Chev.img", {"side": "right", "sticky": ""}),
        ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
            ("Combobox.textarea", {"sticky": "nswe"})]})]})])

    # on/off switches for check boxes
    _el(style, "Switch.ind", [ph(_switch(False, p)), ("disabled selected", ph(_switch(True, p, True))),
                              ("disabled", ph(_switch(False, p, True))), ("selected", ph(_switch(True, p)))],
        border=0, sticky="")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Switch.ind", {"side": "left", "sticky": ""}),
        ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})])

    # format chips (PDF, TXT …): a soft pill, mint with a tick when on
    chip = lambda f: ph(_rounded(R(32), R(26), R(12), f, card))        # noqa: E731
    _el(style, "Chip.bg", [chip(fill), ("selected", chip(p["accent2"])), ("active", chip(p["hover"]))],
        border=(R(13), R(12), R(13), R(12)))
    _el(style, "Chip.chk", [ph(_blank(1, R(12))), ("selected", ph(_check(p["accent_fg"], p["accent2"])))],
        border=0, sticky="")
    style.layout("Chip.TCheckbutton", [("Chip.bg", {"sticky": "nswe", "children": [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Chip.chk", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})]})])

    # pill label (the GPU badge), on the page colour
    _el(style, "Pill.bg", [ph(_rounded(R(32), R(28), R(13), p["accent2"], page))], border=(R(14), R(13), R(14), R(13)))
    style.layout("Pill.TLabel", [("Pill.bg", {"sticky": "nswe", "children": [("Label.padding", {"sticky": "nswe",
                 "children": [("Label.label", {"sticky": "nswe"})]})]})])

    # progress bar: rounded mint track and fill
    _el(style, "Horizontal.Progressbar.trough", [ph(_rounded(R(30), R(10), R(5), p["accent2"], card))],
        border=(R(6), R(4), R(6), R(4)))
    _el(style, "pbar", [ph(_rounded(R(30), R(10), R(5), p["accent"], p["accent2"]))],
        border=(R(6), R(4), R(6), R(4)))
    style.layout("Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
        ("pbar", {"side": "left", "sticky": "ns"})]})])

    # sliders: a thin track with a round knob
    _el(style, "Horizontal.Scale.trough", [ph(_line(R(40), R(18), R(6), p["track"], card))],
        border=(R(8), 0, R(8), 0), sticky="ew")
    _el(style, "slider", [ph(_knob(p))], border=0, sticky="")
    style.layout("Horizontal.TScale", [("Horizontal.Scale.trough", {"sticky": "nswe", "children": [
        ("slider", {"side": "left", "sticky": ""})]})])

    # the notebook with its tab row hidden (the tabs are in the sidebar)
    style.layout("Side.TNotebook.Tab", [])
    style.layout("Side.TNotebook", [("Notebook.client", {"sticky": "nswe"})])


def configure(style, p, dark, ui, size_s):
    """Colours and fonts for Fresh Mint (after use_theme)."""
    page, card, text, muted, faint = p["bg"], p["card"], p["text"], p["muted"], p["faint"]
    F = lambda s, *w: (ui, s + size_s, *w)                   # noqa: E731
    style.configure(".", background=card, foreground=text, fieldbackground=p["fill"], bordercolor=p["line"],
                    darkcolor=card, lightcolor=card, troughcolor=card, focuscolor=p["accent"],
                    selectbackground=p["select"], selectforeground=text, insertcolor=text, font=F(10))
    style.map(".", foreground=[("disabled", faint)])
    for st in ("TButton", "Ghost.TButton"):
        style.configure(st, foreground=text, padding=(14, 6), font=F(10, "bold"), anchor="center")
        style.map(st, foreground=[("disabled", faint)])
    on_acc = "#0b201b" if dark else "#ffffff"
    style.configure("Accent.TButton", foreground=on_acc, padding=(18, 7), font=F(10, "bold"), anchor="center")
    style.map("Accent.TButton", foreground=[("disabled", faint)])
    style.configure("Stop.TButton", foreground=p["coral"], padding=(18, 7), font=F(10, "bold"), anchor="center")
    style.configure("Play.TButton", foreground="#ffffff" if not dark else "#0b201b", padding=(9, 6), anchor="center",
                    font=("Segoe MDL2 Assets" if IS_WIN else ui, 11 + size_s))
    style.configure("TEntry", padding=(10, 6), foreground=text)
    style.configure("TCombobox", padding=(10, 5), foreground=text, arrowsize=0)
    style.map("TCombobox", fieldbackground=[("readonly", p["fill"]), ("disabled", p["fill"])],
              foreground=[("disabled", faint)], selectbackground=[("readonly", p["fill"])],
              selectforeground=[("readonly", text)])
    style.configure("TCheckbutton", padding=(0, 3), foreground=text)
    style.map("TCheckbutton", background=[("active", card)], foreground=[("disabled", faint)])
    style.configure("Chip.TCheckbutton", padding=(10, 4), foreground=muted, font=F(9, "bold"))
    style.map("Chip.TCheckbutton", foreground=[("selected", p["accent_fg"])], background=[("active", card)])
    style.configure("TRadiobutton", background=card, indicatorbackground=card, indicatorforeground=text,
                    upperbordercolor=p["track_off"], lowerbordercolor=p["track_off"], padding=(2, 3))
    style.map("TRadiobutton", background=[("active", card)],
              indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
              indicatorforeground=[("selected", "#ffffff")])
    style.configure("TFrame", background=card)
    style.configure("Page.TFrame", background=page)
    style.configure("Page.TPanedwindow", background=page)
    style.configure("TPanedwindow", background=card)
    style.configure("TLabel", background=card, foreground=text)
    style.configure("Page.TLabel", background=page, foreground=text)
    style.configure("PageMuted.TLabel", background=page, foreground=muted)
    style.configure("Hero.TLabel", background=page, foreground=text, font=heading(18 + size_s, ui))
    style.configure("Card.TFrame", background=card)
    style.configure("Side.TFrame", background=card)
    style.configure("SideLine.TFrame", background=p["line"])
    style.configure("Line.TFrame", background=p["line"])
    style.configure("Side.TLabel", background=card, foreground=muted)
    style.configure("Brand.TLabel", background=card, foreground=text, font=heading(14 + size_s, ui))
    style.configure("Logo.TLabel", background=card)
    style.configure("Badge.TLabel", background=page)
    style.configure("Title.TLabel", background=card, foreground=text, font=heading(12 + size_s, ui))
    style.configure("Head.TLabel", background=card, foreground=muted, font=F(9, "bold"))
    style.configure("Status.TLabel", background=card, foreground=text, font=F(10, "bold"))
    style.configure("Pill.TLabel", foreground=p["accent_fg"], padding=(12, 4), font=F(9, "bold"))
    style.configure("Nav.TButton", foreground=muted, anchor="w", padding=(14, 9), font=F(11, "bold"))
    style.configure("NavOn.TButton", foreground=p["accent_fg"], anchor="w", padding=(14, 9), font=F(11, "bold"))
    style.map("Nav.TButton", foreground=[("active", text)])
    style.configure("Tool.TButton", foreground=muted, anchor="w", padding=(14, 7), font=F(10, "bold"))
    style.map("Tool.TButton", foreground=[("active", text)])
    style.configure("ToolAccent.TButton", foreground=on_acc, anchor="w", padding=(14, 7), font=F(10, "bold"))
    style.configure("Time.TLabel", background=card, foreground=muted, font=F(10))
    style.configure("Horizontal.TProgressbar", thickness=10)
    style.configure("Horizontal.TScale", background=card)
    style.configure("Treeview", background=card, fieldbackground=card, foreground=text, bordercolor=card,
                    lightcolor=card, darkcolor=card, rowheight=40, font=F(10), borderwidth=0)
    style.map("Treeview", background=[("selected", p["accent2"])], foreground=[("selected", text)])
    style.configure("Treeview.Heading", background=card, foreground=faint, bordercolor=card, lightcolor=card,
                    darkcolor=card, relief="flat", font=F(8, "bold"), padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", card)])
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", background=p["thumb"], troughcolor=card, bordercolor=card,
                    lightcolor=p["thumb"], darkcolor=p["thumb"], gripcount=0, relief="flat", arrowsize=8)
    style.map("Vertical.TScrollbar", background=[("active", p["thumb_hi"])],
              lightcolor=[("active", p["thumb_hi"])], darkcolor=[("active", p["thumb_hi"])])
    style.configure("Side.TNotebook", background=page, borderwidth=0, bordercolor=page, lightcolor=page,
                    darkcolor=page, tabmargins=0, padding=0)
    style.configure("TNotebook", background=page)
    style.configure("Drop.TLabel", background=card)
    style.configure("Small.TLabel", background=card, foreground=faint, font=F(8))
    style.configure("Muted.TLabel", background=card, foreground=muted)
    style.configure("Warn.TLabel", background=page)


# ---------------------------------------------------------------- the drop area
class DropZone(tk.Canvas):
    """'Drop audio or video files / folders here': a dashed rounded area with a big upload button, file-type
    chips and a little sound wave. Click it to browse."""

    TYPES = ("mp3", "wav", "m4a", "mp4", "mov", "mkv", "webm", "+ more")

    def __init__(self, parent, app, on_click, height=200):
        super().__init__(parent, highlightthickness=0, bd=0, height=height, cursor="hand2")
        self.app = app
        self.hot = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", lambda e: on_click())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))

    def _hover(self, on):
        self.hot = on
        self.redraw()

    def _rr(self, x0, y0, x1, y1, r, **kw):
        pts = []
        import math
        for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
            for i in range(0, 91, 10):
                a = math.radians(a0 + i)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return self.create_polygon(pts, **kw)

    def redraw(self):
        p = self.app.pal
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        self.configure(bg=p["bg"])
        self._rr(2, 2, w - 3, h - 3, 18, fill=p["drop_hot"] if self.hot else p["drop"], outline=p["drop_line"],
                 width=2, dash=(7, 5))
        from .theme import _S
        ui = FONT if _fonts_ok else ("Segoe UI" if IS_WIN else "Helvetica Neue")
        cy = h / 2 - 62
        s = 58
        self._rr(w / 2 - s / 2, cy - s / 2, w / 2 + s / 2, cy + s / 2, 16, fill=p["accent"], outline=p["accent"])
        ic = icon("upload", 24, "#ffffff")
        if ic is not None:
            self.create_image(w / 2, cy, image=ic)
        else:
            self.create_text(w / 2, cy, text="⬆", fill="#ffffff", font=(ui, 20 + _S))
        self.create_text(w / 2, cy + 52, text="Drop audio or video files / folders here", fill=p["text"],
                         font=heading(12 + _S, ui))
        self.create_text(w / 2, cy + 76, text="or click to browse  ·  no length limit", fill=p["muted"], font=(ui, 10 + _S))
        # file-type chips, wrapped onto lines that fit
        import tkinter.font as tkfont
        f = tkfont.Font(family=ui, size=9 + _S)
        chips = [(t, f.measure(t) + 22) for t in self.TYPES]
        lines, cur, cw = [], [], 0
        for t, cwid in chips:
            if cur and cw + cwid + 6 > w - 40:
                lines.append(cur)
                cur, cw = [], 0
            cur.append((t, cwid))
            cw += cwid + 6
        lines.append(cur)
        y = cy + 104
        for ln in lines:
            tot = sum(c for _, c in ln) + 6 * (len(ln) - 1)
            x = w / 2 - tot / 2
            for t, cwid in ln:
                self._rr(x, y - 12, x + cwid, y + 12, 12, fill=p["card"], outline=p["line"])
                self.create_text(x + cwid / 2, y, text=t, fill=p["muted"], font=(ui, 9 + _S))
                x += cwid + 6
            y += 30
        # a little sound wave
        import math
        n, bw = 28, 3
        x = w / 2 - n * (bw + 3) / 2
        for i in range(n):
            hh = 6 + round(abs(math.sin(i * 1.7) * 14 + math.sin(i * .6) * 7))
            hh = min(hh, 26)
            self.create_line(x, y + 10 - hh / 2, x, y + 10 + hh / 2, fill=p["wave"], width=bw, capstyle="round")
            x += bw + 3
