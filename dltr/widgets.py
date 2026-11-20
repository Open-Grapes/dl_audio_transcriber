"""Small reusable widgets: type-to-search language lists and a Find bar for text boxes."""
import tkinter as tk
from tkinter import ttk


def _rank(choices, q):
    """Matches for q: names that start with it first, then names that contain it."""
    q = q.strip().lower()
    if not q:
        return list(choices)
    starts = [c for c in choices if c.lower().startswith(q)]
    words = [c for c in choices if c not in starts and any(w.startswith(q) for w in c.lower().replace("(", " ").split())]
    rest = [c for c in choices if c not in starts and c not in words and q in c.lower()]
    return starts + words + rest


def searchable(box, choices, fallback=None):
    """Make a language list searchable: type a few letters ("chin") to narrow the list, in the box or while
    the list is open; Up/Down + Enter or a click picks one. An unfinished entry snaps back to the closest match."""
    choices = list(choices)
    fallback = fallback or choices[0]
    st = {"q": "", "prev": box.get(), "typing": False}
    pop = str(box) + ".popdown"
    lb = pop + ".f.l"

    def refill(q):
        m = _rank(choices, q)
        if not m:
            return False
        box.configure(values=m)
        st["q"] = q
        box.set(q)
        box.icursor("end")
        if box.tk.call("winfo", "exists", pop) and box.tk.call("winfo", "ismapped", pop):
            box.tk.call("ttk::combobox::ConfigureListbox", box)
            box.update_idletasks()
            box.tk.call("ttk::combobox::PlacePopdown", box, pop)
        return True

    def on_post():
        if st["typing"]:
            st["typing"] = False
        else:
            st["q"], st["prev"] = "", box.get()
            box.configure(values=choices)

    def lb_key(keysym, char):
        """Typing while the list is open filters it (the letters show in the box above)."""
        if keysym == "BackSpace":
            q = st["q"][:-1]
        elif keysym == "Escape":
            box.set(st["prev"])
            box.configure(values=choices)
            return ""
        elif len(char) == 1 and char.isprintable():
            q = st["q"] + char
        else:
            return ""
        if not refill(q):
            box.bell()
        return "break"

    def entry_key(e):
        """Typing in the box opens the list, already narrowed down."""
        if e.keysym in ("Up", "Down", "Return", "Escape", "Tab", "ISO_Left_Tab") or e.keysym.startswith(
                ("Shift", "Control", "Alt", "Meta", "Caps", "Win", "Super")):
            return
        if not (e.char and e.char.isprintable()) and e.keysym not in ("BackSpace", "Delete"):
            return
        q = box.get()
        m = _rank(choices, q)
        if not q.strip() or not m:
            box.configure(values=m or choices)
            return
        box.configure(values=m)
        st["q"], st["typing"] = q, True
        st["prev"] = st["prev"] if st["prev"] in choices else fallback
        box.tk.call("ttk::combobox::Post", box)

    def fix(_e=None):
        if box.get() not in choices:
            m = _rank(choices, box.get())
            box.set(m[0] if m and box.get().strip() else (st["prev"] if st["prev"] in choices else fallback))
        box.configure(values=choices)
        box.selection_clear()

    def focus_in(_e):
        if box.get() in choices:
            st["prev"] = box.get()
            box.after_idle(lambda: box.selection_range(0, "end"))    # typing replaces the old choice

    box.configure(postcommand=on_post)
    box.tk.call("ttk::combobox::PopdownWindow", box)                  # create it now so we can bind its list
    cmd = box.register(lb_key)
    box.tk.call("bind", lb, "<KeyPress>", f"if {{[{cmd} %K %A] eq {{break}}}} break")
    box.bind("<KeyRelease>", entry_key)
    box.bind("<FocusOut>", lambda e: box.after(50, lambda: None if str(box.tk.call("focus")).startswith(pop)
                                               else fix()))
    box.bind("<FocusIn>", focus_in)
    box.bind("<<ComboboxSelected>>", fix, add="+")
    return box


class FindBar(ttk.Frame):
    """Find (Ctrl+F) for one or more read-only text boxes: highlights every match, Next/Previous step through
    them, across the boxes in order."""

    def __init__(self, parent, texts):
        super().__init__(parent)
        self.texts = texts
        self.hits, self.i = [], -1
        self.v = tk.StringVar()
        self.place_fn = None
        ttk.Label(self, text="Find").pack(side="left", padx=(0, 6))
        self.ent = ttk.Entry(self, textvariable=self.v, width=34)
        self.ent.pack(side="left")
        ttk.Button(self, text="Previous", command=lambda: self.step(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(self, text="Next", command=lambda: self.step(1)).pack(side="left", padx=6)
        self.lbl = ttk.Label(self, style="Muted.TLabel")
        self.lbl.pack(side="left", padx=6)
        ttk.Button(self, text="Close", command=self.close).pack(side="right")
        self.ent.bind("<KeyRelease>", lambda e: None if e.keysym in ("Return", "Escape", "Shift_L", "Shift_R", "F3")
                      else self.find_all())
        self.ent.bind("<Return>", lambda e: self.step(1))
        self.ent.bind("<Shift-Return>", lambda e: self.step(-1))
        self.ent.bind("<Escape>", lambda e: self.close())

    def recolor(self, dark):
        for t in self.texts:
            t.tag_configure("find", background="#5c4a12" if dark else "#ffe08a")
            t.tag_configure("findcur", background="#d97706" if dark else "#ff9f43",
                            foreground="#ffffff" if dark else "#000000")
            t.tag_raise("find")
            t.tag_raise("findcur")
            t.tag_raise("sel")

    def shown(self):
        return bool(self.winfo_manager())

    def open(self):
        if not self.shown() and self.place_fn:
            self.place_fn()
        self.ent.focus_set()
        self.ent.select_range(0, "end")
        if self.v.get():
            self.find_all()
        return "break"

    def close(self):
        self.pack_forget()
        self._clear()
        self.texts[0].focus_set()
        return "break"

    def _clear(self):
        for t in self.texts:
            t.tag_remove("find", "1.0", "end")
            t.tag_remove("findcur", "1.0", "end")
        self.hits, self.i = [], -1

    def refresh(self):
        """Call after the text changes so the highlights follow."""
        if self.shown():
            self.find_all(scroll=False)

    def find_all(self, scroll=True):
        self._clear()
        q = self.v.get()
        if not q.strip():
            self.lbl.configure(text="")
            return
        cnt = tk.IntVar()
        for t in self.texts:
            idx = "1.0"
            while True:
                pos = t.search(q, idx, "end", nocase=True, count=cnt)
                if not pos or not cnt.get():
                    break
                end = f"{pos}+{cnt.get()}c"
                self.hits.append((t, pos, end))
                t.tag_add("find", pos, end)
                idx = end
        if not self.hits:
            self.lbl.configure(text="No matches")
            return
        t0 = self.texts[0]
        here = t0.index("@0,0")
        self.i = next((k for k, (t, a, _b) in enumerate(self.hits) if t is t0 and t.compare(a, ">=", here)), 0)
        self.show(scroll)

    def step(self, d):
        if not self.shown():
            return self.open()
        if not self.hits:
            self.find_all()
            if not self.hits:
                return "break"
        else:
            self.i = (self.i + d) % len(self.hits)
        self.show(True)
        return "break"

    def show(self, scroll=True):
        for t in self.texts:
            t.tag_remove("findcur", "1.0", "end")
        t, a, b = self.hits[self.i]
        t.tag_add("findcur", a, b)
        if scroll:
            t.see(a)
        self.lbl.configure(text=f"{self.i + 1} of {len(self.hits)}")


def copyable(label, app):
    """Right-click a status line to copy its message (e.g. an error to send to someone)."""
    import sys
    import tkinter as tk

    def menu(e):
        text = label.cget("text")
        if not text:
            return
        p = app.pal
        m = tk.Menu(label, tearoff=0, bg=p["panel"], fg=p["text"], activebackground=p["accent"],
                    activeforeground="#ffffff")
        m.add_command(label="Copy message", command=lambda: (app.clipboard_clear(), app.clipboard_append(text)))
        m.tk_popup(e.x_root, e.y_root)
    for ev in (("<Button-2>", "<Control-Button-1>") if sys.platform == "darwin" else ("<Button-3>",)):
        label.bind(ev, menu)
