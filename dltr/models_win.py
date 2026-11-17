"""The Models window: see every Whisper model, download / update / remove them."""

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import models, theme
from .common import MODELS, MODELS_DIR, log, open_path
from .worker import Worker


class ModelsWindow:
    COLS = (("type", "Type", 110), ("speed", "Speed", 80), ("accuracy", "Accuracy", 75), ("size", "Size", 70),
            ("status", "Status", 190), ("version", "Downloaded", 95), ("license", "License", 100))

    def __init__(self, app):
        self.app = app
        self.busy = None            # "download" | "check" | "remove"
        self.q = queue.Queue()
        self.wk = None              # its own download process, so Cancel stops at once
        self.queue_keys, self.done_keys = [], []
        self.remote = {}            # key -> ("update" | "up-to-date", online date)
        self.cancel = threading.Event()
        w = self.win = tk.Toplevel(app)
        w.title("Models")
        w.geometry("1180x540")
        w.minsize(760, 380)
        w.transient(app)
        w.configure(bg=app.pal["bg"])

        top = ttk.Frame(w, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Transcription models", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Open folder", command=self.open_folder).pack(side="right", padx=(10, 0))
        self.where = ttk.Label(top, style="Muted.TLabel")
        self.where.pack(side="right")

        mid = ttk.Frame(w, padding=(12, 0))
        mid.pack(fill="both", expand=True)
        t = self.tree = ttk.Treeview(mid, columns=[c[0] for c in self.COLS], show="tree headings",
                                     selectmode="extended")
        t.heading("#0", text="Model", anchor="w")
        t.column("#0", width=200, stretch=False)
        for k, lab, wd in self.COLS:
            t.heading(k, text=lab)
            t.column(k, width=wd, anchor="w" if k == "status" else "center", stretch=k == "status")
        t.pack(fill="both", expand=True)
        t.bind("<<TreeviewSelect>>", lambda e: self._describe())
        t.bind("<Double-1>", lambda e: self.use_selected())
        self.desc = ttk.Label(mid, text="Select a model to see what it's for. Double-click to use it.",
                              style="Muted.TLabel", wraplength=940, justify="left")
        self.desc.pack(fill="x", pady=(6, 0))

        bot = ttk.Frame(w, padding=12)
        bot.pack(fill="x")
        b = ttk.Frame(bot)
        b.pack(fill="x")
        self.btns = []
        for text, cmd, style in (("Download recommended", self.download_recommended, "Accent.TButton"),
                                 ("Download selected", lambda: self.download(self._sel(False)), "TButton"),
                                 ("Download all", self.download_all, "TButton"),
                                 ("Check for updates", self.check_updates, "TButton"),
                                 ("Install updates", self.install_updates, "TButton"),
                                 ("Remove selected", self.remove_selected, "TButton"),
                                 ("Remove all", self.remove_all, "TButton")):
            x = ttk.Button(b, text=text, command=cmd, style=style)
            x.pack(side="left", padx=(0, 6))
            self.btns.append(x)
        line = ttk.Frame(bot)
        line.pack(fill="x", pady=(10, 0))
        ttk.Button(line, text="Use selected model", command=self.use_selected).pack(side="left", padx=(0, 10))
        self.cancel_btn = ttk.Button(line, text="Cancel", command=self.stop, state="disabled")
        self.cancel_btn.pack(side="right")
        self.pbar = ttk.Progressbar(line, maximum=1.0, length=320)
        self.pbar.pack(side="right", padx=8)
        self.status = ttk.Label(line, style="Muted.TLabel")
        self.status.pack(side="left", fill="x", expand=True)

        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        y = app.winfo_rooty() + 60
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.refresh()
        sel = app._model_name()
        if t.exists(sel):
            t.selection_set(sel)
            t.see(sel)
        self._poll()

    # ------------------------------------------------------------ helpers
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _sel(self, want_installed=None):
        keys = [k for k in self.tree.selection() if k in models.ALL]
        if want_installed is not None:
            keys = [k for k in keys if models.installed(k) == want_installed]
        return keys

    def _set_busy(self, what):
        self.busy = what
        if not self.alive():
            return
        for x in self.btns:
            x.configure(state="disabled" if what else "normal")
        self.cancel_btn.configure(state="normal" if what in ("download", "check") else "disabled")
        if not what:
            self.pbar["value"] = 0

    def refresh(self):
        if not self.alive():
            return
        t, p = self.tree, self.app.pal
        sel = t.selection()
        t.delete(*t.get_children())
        tags = p["tags"]
        t.tag_configure("installed", foreground=tags["DONE"])
        t.tag_configure("update", foreground=tags["RUN"])
        t.tag_configure("missing", foreground=p["text"])
        t.tag_configure("using", font=(theme.UI, 9 + theme._S, "bold"))
        rec = models.recommended(self.app.has_gpu)
        using = self.app._model_name()
        for k in models.ALL:
            mb = models.size_mb(k)
            speed, acc, _ = models.INFO[k]
            inst = models.installed(k)
            rs = self.remote.get(k)
            if self.busy == "download" and k in self.queue_keys and k not in self.done_keys:
                status, tag = "Waiting to download…", "update"
            elif not inst:
                status, tag = "Not downloaded" + (" (recommended)" if k in rec else ""), "missing"
            elif rs and rs[0] == "update":
                status, tag = f"Update available ({rs[1]})", "update"
            elif rs:
                status, tag = "Installed · up to date ✓", "installed"
            else:
                status, tag = "Installed", "installed"
            if inst and k == using:
                status += " · in use"
            elif inst and k in models.EXTRA:
                status += " · used by " + ("Summary + Translate" if k == "qwen3-4b" else "Translate")
            size = models.fmt_size(models.disk_bytes(k)) if inst else models.fmt_size(mb * 1e6)
            name = ("★ " if k in rec else "    ") + k
            t.insert("", "end", iid=k, text=name, tags=(tag,) + (("using",) if k == using else ()),
                     values=(models.kind(k), speed, acc, size, status, models.downloaded_on(k) if inst else "",
                             models.license_of(k)))
        for s in sel:
            if t.exists(s):
                t.selection_add(s)
        self.where.configure(text=f"{MODELS_DIR}   ·   using {models.fmt_size(models.disk_bytes())}")
        self.app.refresh_models()

    def _describe(self):
        s = self._sel()
        if not s:
            return
        k = s[0]
        speed, acc, about = models.INFO[k]
        extra = "" if models.installed(k) else f"  Download: about {models.fmt_size(models.size_mb(k) * 1e6)}."
        self.desc.configure(text=f"{k} — {about}{extra}\nhttps://huggingface.co/{models.REPOS[k]}")

    def _poll(self):
        if not self.alive():
            return
        try:
            while True:
                self._event(*self.q.get_nowait())
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    # ------------------------------------------------------------ download (in its own process)
    def download(self, keys, force=False):
        keys = [k for k in keys if force or not models.installed(k)]
        if not keys:
            messagebox.showinfo("Models", "Nothing to download: select models that aren't downloaded yet "
                                          "(or they're all on this PC already).", parent=self.win)
            return
        if self.busy:
            return
        self.queue_keys, self.done_keys = keys, []
        self._set_busy("download")
        self.status.configure(text="Starting download …")
        self.wk = Worker(lambda *a: self.q.put(a))
        self.wk.submit({"kind": "download", "id": "mdl", "models": keys, "force": force})
        self.refresh()

    def _event(self, kind, *a):
        if kind == "call":
            a[0]()
        elif kind == "prog" and a[0] == "mdl":
            frac, text = a[1], a[2]
            self.pbar["value"] = frac or 0
            n = len(self.queue_keys)
            i = min(int((frac or 0) * n), n - 1)
            prefix = f"({i + 1}/{n}) " if n > 1 else ""
            self.status.configure(text=prefix + text.replace(" (one time only)", ""))
            for k in self.queue_keys[:i]:
                if k not in self.done_keys:
                    self.done_keys.append(k)
                    self.refresh()
        elif kind == "downloaded":
            self._end_download(f"Downloaded: {', '.join(self.queue_keys)}. Ready to use offline.")
            for k in self.queue_keys:
                self.remote.pop(k, None)
            self.refresh()
        elif kind == "fail" and a[0] == "mdl":
            self._end_download("Download failed.")
            self.refresh()
            messagebox.showerror("Models", a[1], parent=self.win)
        elif kind == "died":
            self._end_download("The download stopped unexpectedly. Try again — it resumes where it left off.")
            self.refresh()

    def _end_download(self, msg):
        wk, self.wk = self.wk, None
        if wk:
            threading.Thread(target=wk.close, daemon=True).start()
        self._set_busy(None)
        self.status.configure(text=msg)

    def stop(self):
        if self.busy == "download" and self.wk:
            wk, self.wk = self.wk, None
            wk.kill()
            self._set_busy(None)
            self.status.configure(text="Download cancelled. Starting it again later resumes where it stopped.")
            self.refresh()
        elif self.busy == "check":
            self.cancel.set()

    def download_recommended(self):
        keys = [k for k in models.recommended(self.app.has_gpu) if not models.installed(k)]
        if not keys:
            messagebox.showinfo("Models", "The recommended model is already on this PC.", parent=self.win)
            return
        self.download(keys)

    def download_all(self):
        keys = [k for k in models.ALL if not models.installed(k)]
        if not keys:
            messagebox.showinfo("Models", "All models are already on this PC.", parent=self.win)
            return
        mb = sum(models.size_mb(k) for k in keys)
        if messagebox.askyesno("Download all models",
                               f"Download {len(keys)} model(s), about {models.fmt_size(mb * 1e6)}?\n\n"
                               "Then you can switch models any time without internet.", parent=self.win):
            self.download(keys)

    # ------------------------------------------------------------ updates
    def check_updates(self):
        keys = [k for k in models.ALL if models.installed(k)]
        if not keys:
            messagebox.showinfo("Models", "No models downloaded yet.", parent=self.win)
            return
        self.cancel.clear()
        self._set_busy("check")
        self.status.configure(text="Checking Hugging Face for newer versions …")

        def work():
            n, err = 0, None
            for i, k in enumerate(keys):
                if self.cancel.is_set():
                    break
                self.q.put(("call", lambda i=i, k=k: (self.status.configure(text=f"Checking {k} …"),
                                                      self.pbar.configure(value=i / len(keys)))))
                try:
                    self.remote[k] = models.check_update(k)
                    n += self.remote[k][0] == "update"
                except Exception as e:
                    log(f"update check {k}: {e!r}")
                    err = e
                    break
            if err is not None:
                msg = "Couldn't reach Hugging Face (needs internet). Your models still work offline."
            elif self.cancel.is_set():
                msg = "Check cancelled."
            else:
                msg = f"{n} update(s) available — press Install updates." if n else \
                    "All downloaded models are up to date."
            self.q.put(("call", lambda: (self._set_busy(None), self.status.configure(text=msg), self.refresh())))
        threading.Thread(target=work, daemon=True).start()

    def install_updates(self):
        keys = [k for k, (st, _) in self.remote.items() if st == "update"]
        if not keys:
            messagebox.showinfo("Models", "No updates waiting. Press 'Check for updates' first.", parent=self.win)
            return
        if self.app.running and self.app._model_name() in keys:
            messagebox.showinfo("Models", "Wait for the transcription to finish first.", parent=self.win)
            return
        self.app.wk.kill()          # let go of the old model files (a fresh worker starts with the next job)
        self.download(keys, force=True)

    # ------------------------------------------------------------ remove
    def _remove(self, keys, label):
        if self.app.running:
            messagebox.showinfo("Models", "Wait for the transcription to finish first.", parent=self.win)
            return
        self.app.wk.kill()          # the worker may still have a model open
        self._set_busy("remove")
        self.status.configure(text=label)

        def work():
            err = None
            for k in keys:
                try:
                    models.remove(k)
                except OSError as e:
                    err = f"Couldn't delete {k}: {e}"
                self.remote.pop(k, None)
            msg = err or f"Removed: {', '.join(keys)}."
            self.q.put(("call", lambda: (self._set_busy(None), self.status.configure(text=msg), self.refresh())))
        threading.Thread(target=work, daemon=True).start()

    def remove_selected(self):
        keys = self._sel(True)
        if not keys:
            messagebox.showinfo("Models", "Select downloaded models to remove.", parent=self.win)
            return
        names = "\n".join(f"•  {k}  ({models.fmt_size(models.disk_bytes(k))})" for k in keys)
        if messagebox.askyesno("Remove models", f"Delete these models from this PC?\n\n{names}\n\n"
                                                "You can download them again later.", parent=self.win):
            self._remove(keys, "Removing …")

    def remove_all(self):
        keys = [k for k in models.ALL if models.installed(k)]
        if not keys:
            messagebox.showinfo("Models", "No models downloaded.", parent=self.win)
            return
        if messagebox.askyesno("Remove all models",
                               f"Delete ALL downloaded models ({models.fmt_size(models.disk_bytes())}) from\n"
                               f"{MODELS_DIR}?\n\nThe app will ask to download one again when you press Start.",
                               parent=self.win):
            self._remove(keys, "Removing all models …")

    # ------------------------------------------------------------ other
    def use_selected(self):
        s = self._sel()
        if s and s[0] in models.EXTRA:
            self.status.configure(text=f"{s[0]} is used automatically by the Translate / Summary tabs.")
            return
        if s:
            self.app.set_model(s[0])
            self.status.configure(text=f"Using {s[0]}" + ("" if models.installed(s[0]) else
                                                          " — it downloads when you press Start (or download it here)."))
            self.refresh()

    def open_folder(self):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        open_path(MODELS_DIR)

    def close(self):
        if self.busy == "download":
            if not messagebox.askyesno("Models", "A download is running. Cancel it and close?", parent=self.win):
                return
            self.stop()
        self.cancel.set()
        self.app.models_win = None
        self.win.destroy()
        self.app.refresh_models()
