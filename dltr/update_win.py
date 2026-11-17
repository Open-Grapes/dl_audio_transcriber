"""The Updates window: is there a newer dl_transcribe on GitHub, are the downloaded models current,
and 'Update all' to bring everything up to date."""

import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import messagebox, ttk

from . import models, theme, updater
from .common import APP_NAME, APP_VERSION, IS_MAC, MODELS, log
from .worker import Worker


class UpdateWindow:
    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()
        self.info = None              # latest release (dict) or None
        self.app_err = None
        self.model_updates = []       # keys of downloaded models with a newer version online
        self.busy = False
        self.cancel = threading.Event()
        self.mwk = None
        w = self.win = tk.Toplevel(app)
        w.title("Updates")
        w.geometry("660x520")
        w.minsize(520, 380)
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        f = ttk.Frame(w, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Updates", style="Title.TLabel").pack(anchor="w")
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=(10, 6))
        ttk.Label(grid, text="This version", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Label(grid, text=APP_VERSION).grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="Latest on GitHub", style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 16),
                                                                            pady=(4, 0))
        self.latest_lbl = ttk.Label(grid, text="checking …", wraplength=440, justify="left")
        self.latest_lbl.grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Label(grid, text="Models", style="Muted.TLabel").grid(row=2, column=0, sticky="nw", padx=(0, 16),
                                                                  pady=(4, 0))
        self.models_lbl = ttk.Label(grid, text="checking …", wraplength=440, justify="left")
        self.models_lbl.grid(row=2, column=1, sticky="w", pady=(4, 0))

        # buttons + progress first (packed at the bottom), so the notes box can't push them out of view
        b = ttk.Frame(f)
        b.pack(side="bottom", fill="x", pady=(10, 0))
        self.status = ttk.Label(f, style="Muted.TLabel", wraplength=600, justify="left")
        self.status.pack(side="bottom", fill="x", pady=(6, 0))
        self.pbar = ttk.Progressbar(f, maximum=1.0)
        self.pbar.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(f, text="What's new", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))
        p = app.pal
        self.notes = tk.Text(f, height=8, wrap="word", relief="flat", padx=10, pady=8, font=(theme.UI, 9),
                             bg=p["field"], fg=p["text"], highlightthickness=1, highlightbackground=p["line"],
                             borderwidth=0)
        self.notes.pack(fill="both", expand=True)
        self.notes.configure(state="disabled")

        self.go = ttk.Button(b, text="Update all", style="Accent.TButton", command=self.update_all, state="disabled")
        self.go.pack(side="left")
        self.recheck = ttk.Button(b, text="Check again", command=self.check)
        self.recheck.pack(side="left", padx=6)
        ttk.Button(b, text="Release page", command=lambda: webbrowser.open(
            (self.info or {}).get("page") or updater.RELEASES_PAGE)).pack(side="left")
        self.close_btn = ttk.Button(b, text="Close", command=self.close)
        self.close_btn.pack(side="right")

        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.update_idletasks()
        theme.title_bar(w, app.dark)
        x = app.winfo_rootx() + (app.winfo_width() - w.winfo_width()) // 2
        y = app.winfo_rooty() + 80
        w.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self._poll()
        self.check()

    # ------------------------------------------------------------ helpers
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _ui(self, fn):
        self.q.put(("call", fn))

    def _poll(self):
        if not self.alive():
            return
        try:
            while True:
                kind, *a = self.q.get_nowait()
                if kind == "call":
                    a[0]()
        except queue.Empty:
            pass
        self.win.after(100, self._poll)

    def _set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        self.notes.configure(state="disabled")

    def _app_newer(self):
        return bool(self.info and updater.is_newer(self.info["version"]))

    # ------------------------------------------------------------ check
    def check(self):
        if self.busy:
            return
        self.busy = True
        self.go.configure(state="disabled")
        self.recheck.configure(state="disabled")
        self.status.configure(text="Asking GitHub for the latest version …")
        self.latest_lbl.configure(text="checking …")
        self.models_lbl.configure(text="checking …")
        self.pbar.configure(mode="indeterminate")
        self.pbar.start(12)

        def work():
            info, err = None, None
            try:
                info = updater.latest()
            except updater.UpdateError as e:
                err = str(e)
            except Exception as e:
                log("update check failed\n" + traceback.format_exc())
                err = f"Update check failed: {e}"
            self._ui(lambda: self._show_app(info, err))
            ups, merr = [], None
            for k in models.ALL:
                if not models.installed(k):
                    continue
                try:
                    if models.check_update(k)[0] == "update":
                        ups.append(k)
                except Exception as e:
                    merr = e
                    break
            self._ui(lambda: self._show_models(ups, merr))
            self._ui(self._checked)
        threading.Thread(target=work, daemon=True).start()

    def _show_app(self, info, err):
        self.info, self.app_err = info, err
        if err:
            self.latest_lbl.configure(text=err)
            self._set_notes("")
            return
        when = f"  (released {info['published']})" if info.get("published") else ""
        priv = "  · seen with this PC's GitHub login (private project)" if info.get("private") else ""
        if self._app_newer():
            txt = f"{info['version']}{when} — a newer version is available{priv}"
            if not info.get("asset"):
                txt += f"\n(but that release has no {updater.asset_name()} to download yet)"
        else:
            txt = f"{info['version']}{when} — you're on the latest version ✓{priv}"
        self.latest_lbl.configure(text=txt)
        self._set_notes(info.get("notes") or "(no release notes)")

    def _show_models(self, ups, err):
        self.model_updates = ups
        have = [k for k in models.ALL if models.installed(k)]
        if err is not None:
            self.models_lbl.configure(text="Couldn't check the models (Hugging Face not reachable).")
        elif not have:
            self.models_lbl.configure(text="No models downloaded yet.")
        elif ups:
            self.models_lbl.configure(text=f"Newer version online: {', '.join(ups)}")
        else:
            self.models_lbl.configure(text=f"All downloaded models are up to date ✓ ({', '.join(have)})")

    def _can_app(self):
        ok, _why = updater.can_install()
        return self._app_newer() and bool(self.info.get("asset")) and ok

    def _checked(self):
        self.busy = False
        self.pbar.stop()
        self.pbar.configure(mode="determinate", value=0)
        self.recheck.configure(state="normal")
        todo = self._can_app() or bool(self.model_updates)
        self.go.configure(state="normal" if todo else "disabled")
        if self._app_newer() and not updater.can_install()[0]:
            self.status.configure(text=updater.can_install()[1])
        elif todo:
            self.status.configure(text="Press Update all to download and install the updates.")
        elif not self.app_err:
            self.status.configure(text="Everything is up to date.")
        else:
            self.status.configure(text="")
        self.app.set_update_badge(self.info["version"] if self._app_newer() else None)

    # ------------------------------------------------------------ update all
    def update_all(self):
        if self.busy:
            return
        if self.app.running:
            messagebox.showinfo("Updates", "Wait for the transcription to finish first.", parent=self.win)
            return
        do_app = self._can_app()
        ups = list(self.model_updates)
        self.busy = True
        self.cancel.clear()
        self.go.configure(state="disabled")
        self.recheck.configure(state="disabled")
        self.close_btn.configure(text="Cancel")
        steps = len(ups) + (1 if do_app else 0)

        def prog(frac, text):
            self._ui(lambda: (self.pbar.configure(value=frac), self.status.configure(text=text)))

        def work():
            err, helper = None, None
            try:
                if ups:
                    self.app.wk.kill()         # let go of the old model files
                    done = threading.Event()
                    res = {}

                    def on_event(kind, *a):
                        if kind == "prog" and a[0] == "upd":
                            prog(a[1] * len(ups) / steps, a[2].replace(" (one time only)", ""))
                        elif kind == "downloaded" or (kind == "fail" and a[0] == "upd"):
                            res["err"] = a[1] if kind == "fail" else None
                            done.set()
                        elif kind == "died":
                            res["err"] = "The model download stopped unexpectedly."
                            done.set()
                    self.mwk = Worker(on_event)
                    self.mwk.submit({"kind": "download", "id": "upd", "models": ups, "force": True})
                    while not done.wait(0.3):
                        if self.cancel.is_set():
                            self.mwk.kill()
                            raise updater.UpdateError("Cancelled")
                    self.mwk.close()
                    self.mwk = None
                    if res.get("err"):
                        raise updater.UpdateError(f"Model update failed: {res['err']}")
                    self.model_updates = []
                if do_app:
                    base = len(ups)
                    work_dir = tempfile.mkdtemp(prefix="dltr_dl_", dir=updater._work_root())
                    pkg = updater.download(self.info, work_dir, lambda d, t: prog(
                        (base + d / max(t, 1)) / steps,
                        f"Downloading {APP_NAME} {self.info['version']} — {d / 1e6:,.0f} / {t / 1e6:,.0f} MB"),
                        self.cancel)
                    prog(0.99, "Unpacking …")
                    helper, _ = updater.prepare_install(pkg, os.getpid())
                    if not IS_MAC:               # the Mac helper mounts the .dmg from that folder
                        shutil.rmtree(work_dir, ignore_errors=True)
            except updater.UpdateError as e:
                err = str(e)
            except Exception as e:
                log("update failed\n" + traceback.format_exc())
                err = f"Update failed: {e}"
            self._ui(lambda: self._updated(err, helper))
        threading.Thread(target=work, daemon=True).start()

    def _updated(self, err, helper):
        self.busy = False
        self.close_btn.configure(text="Close")
        self.recheck.configure(state="normal")
        self.app.refresh_models()
        if self.app.models_win is not None and self.app.models_win.alive():
            self.app.models_win.refresh()
        if err:
            self.pbar["value"] = 0
            self.status.configure(text=err)
            self.go.configure(state="normal")
            if err != "Cancelled":
                messagebox.showerror("Updates", err, parent=self.win)
            return
        self.pbar["value"] = 1.0
        self._show_models([], None)
        if not helper:
            self.status.configure(text="Models updated. Everything is up to date.")
            return
        updater.start_helper(helper)     # waits for this app to close, then swaps the files and restarts it
        self.status.configure(text=f"{self.info['version']} is ready — it installs when the app closes.")
        if messagebox.askyesno("Update ready", f"{APP_NAME} {self.info['version']} is downloaded.\n\n"
                                               "Restart now to finish the update?", parent=self.win):
            self.app.after(100, self.app._close)

    def close(self):
        if self.busy:
            if not messagebox.askyesno("Updates", "An update is running. Cancel it?", parent=self.win):
                return
            self.cancel.set()
            if self.mwk is not None:
                self.mwk.kill()
            return
        self.app.update_win = None
        self.win.destroy()
