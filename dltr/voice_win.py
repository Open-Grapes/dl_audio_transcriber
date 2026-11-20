"""Choose the read-aloud voice for a language: every voice for it, with a ▶ Preview that reads the user's own
first sentence, and Use this voice (remembered per language). Automatic = the app's best pick."""

import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import langs, media, theme, tts
from .common import APP_NAME, log


class VoiceWindow:
    ai_label = "Voice preview"

    def __init__(self, app, code, sample, on_done=None):
        self.app = app
        self.code = tts._base(code)
        self.sample = (sample or "").strip() or _SAMPLE.get(self.code, "Hello, this is a test of this voice.")
        self.on_done = on_done
        self.player = None
        self.busy = False
        self.voices = tts.voices(self.code)
        gender = app.s.get("tts_gender", "female")
        chosen = (app.s.get("voices") or {}).get(self.code)
        self.auto = tts.pick(self.code, gender)
        w = self.win = tk.Toplevel(app)
        w.title(f"Voice for {langs.name(code)}")
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        w.geometry("760x520")
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Read-aloud voice for {langs.name(code)}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, style="Muted.TLabel", wraplength=720, justify="left", text=(
            "The app picks a native voice for the language by itself (marked “auto”). Select another one and press "
            "▶ Preview to hear it read your own text, then Use this voice. Each voice downloads once, the first "
            "time you preview or use it.")).pack(anchor="w", pady=(4, 10))
        cols = (("gender", "Voice", 80), ("quality", "Sound", 80), ("size", "Size", 70), ("status", "On this PC", 150),
                ("license", "Licence", 150))
        t = self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="tree headings", height=12,
                                     selectmode="browse")
        t.heading("#0", text="Speaker", anchor="w")
        t.column("#0", width=210)
        for k, lab, wd in cols:
            t.heading(k, text=lab)
            t.column(k, width=wd, anchor="center")
        t.pack(fill="both", expand=True)
        t.bind("<Double-1>", lambda e: self.preview())
        self._fill(chosen)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(10, 0))
        self.prev_btn = ttk.Button(row, text="▶  Preview", command=self.preview, width=12)
        self.prev_btn.pack(side="left")
        ttk.Button(row, text="Use automatic", command=self.use_auto).pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=self.close).pack(side="right")
        ttk.Button(row, text="Use this voice", style="Accent.TButton", command=self.use).pack(side="right", padx=6)
        self.status = ttk.Label(f, style="Muted.TLabel", wraplength=720, justify="left")
        self.status.pack(fill="x", pady=(8, 0))
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        y = app.winfo_rooty() + 80
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _fill(self, chosen=None):
        t = self.tree
        cur = t.selection()
        t.delete(*t.get_children())
        chosen = chosen if chosen is not None else (self.app.s.get("voices") or {}).get(self.code)
        for v in self.voices:
            mark = []
            if v["id"] == self.auto["id"]:
                mark.append("auto")
            if v["id"] == chosen:
                mark.append("chosen")
            name = v["name"] + (f"   ({', '.join(mark)})" if mark else "")
            t.insert("", "end", iid=v["id"], text=name, values=(
                {"female": "♀ Female", "male": "♂ Male"}.get(v["gender"], "—"), v["quality"], f"{v['mb']} MB",
                "Downloaded ✓" if tts.installed(v) else "Downloads when used", v["license"]))
        sel = chosen or self.auto["id"]
        if cur and t.exists(cur[0]):
            sel = cur[0]
        if t.exists(sel):
            t.selection_set(sel)
            t.see(sel)

    def _sel(self):
        s = self.tree.selection()
        return next((v for v in self.voices if s and v["id"] == s[0]), None)

    def _ok_download(self, v):
        if tts.installed(v):
            return True
        return messagebox.askyesno(APP_NAME, f"{v['name']} needs a one-time download of about {v['mb']} MB.\n\n"
                                             f"Licence: {v['license']}.\n\nDownload now?", parent=self.win)

    # ------------------------------------------------------------ preview
    def preview(self):
        v = self._sel()
        if v is None or self.busy:
            return
        if not self._ok_download(v):
            return
        app = self.app
        if not app.ai.free_for(self):
            self.status.configure(text=f"The {app.ai.label()} tab is still using the AI; try again in a moment.")
            return
        self._stop_audio()
        self.busy = True
        self.prev_btn.configure(state="disabled")
        self.status.configure(text=f"Getting {v['name']} ready …")
        self._out = Path(tempfile.gettempdir()) / f"dltr-voice-preview-{v['id'].replace(':', '_')}.wav"
        app.ai.run(self, [{"kind": "tts", "id": "prev", "texts": [self.sample[:300]], "lang": self.code,
                           "voice": v["id"], "out": str(self._out)}])

    def on_ai(self, kind, *a):
        if kind == "prog":
            if self.alive():
                self.status.configure(text=a[2])
        elif kind == "voiced":
            self.app.ai.release()
            self.busy = False
            if not self.alive():
                return
            self.prev_btn.configure(state="normal")
            self._fill()
            try:
                self.player = media.Player()
                self.player.open(self._out)
                self.player.set_volume(self.app.player.volume)
                self.player.play(0)
                self.status.configure(text=f"Playing {a[1]['voice_name']}. Use this voice to read with it.")
            except Exception as e:
                log(f"voice preview: {e!r}")
                self.status.configure(text=f"Couldn't play the preview: {e}")
        elif kind in ("fail", "died"):
            self.app.ai.stop()
            self.busy = False
            if self.alive():
                self.prev_btn.configure(state="normal")
                self.status.configure(text="The preview failed: " + (a[1] if kind == "fail" else "the voice stopped."))

    def stop(self):
        if self.app.ai.owner is self:
            self.app.ai.stop()
        self.busy = False

    # ------------------------------------------------------------ choose
    def use(self):
        v = self._sel()
        if v is None:
            return
        self._save(v["id"])
        self.status.configure(text=f"{langs.name(self.code)} is read with {v['name']} from now on.")
        self._fill(v["id"])
        if self.on_done:
            self.on_done(v)

    def use_auto(self):
        self._save(None)
        self.status.configure(text=f"Automatic: {self.auto['name']} (follows Female / Male voice in the menu).")
        self._fill(None)
        if self.on_done:
            self.on_done(None)

    def _save(self, vid):
        s = self.app.s
        d = dict(s.get("voices") or {})
        if vid:
            d[self.code] = vid
        else:
            d.pop(self.code, None)
        s["voices"] = d
        self.app._save()

    def _stop_audio(self):
        if self.player is not None:
            try:
                self.player.close()
            except Exception:
                pass
            self.player = None

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self._stop_audio()
        if self.busy:
            self.stop()
        self.win.destroy()


_SAMPLE = {
    "zho_Hans": "你好，这是这个声音的试听。现在就接种流感疫苗。",
    "eng_Latn": "Hello, this is a preview of this voice. Book your flu vaccination now.",
    "zsm_Latn": "Selamat pagi, ini ialah contoh suara ini. Tempah vaksinasi flu anda sekarang.",
    "tam_Taml": "வணக்கம், இது இந்தக் குரலின் மாதிரி.",
    "ind_Latn": "Halo, ini adalah contoh suara ini.",
}
