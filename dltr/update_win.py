"""The Updates window: is there a newer dl_transcribe on GitHub, are the downloaded models current,
and 'Update now' to bring everything up to date."""

import os
import queue
import re
import shutil
import tempfile
import threading
import time
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
        w.title("Check for updates")
        w.geometry("720x680")
        w.minsize(540, 440)
        w.transient(app)
        w.configure(bg=app.pal["bg"])
        f = ttk.Frame(w, padding=(18, 14, 18, 14))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{APP_NAME} {APP_VERSION}  (this version)",
                  font=(theme.UI, 12 + theme._S, "bold")).pack(anchor="w")
        self.head = tk.Label(f, font=(theme.UI, 14 + theme._S, "bold"), bg=app.pal["bg"], fg=app.pal["text"],
                             anchor="w", justify="left", wraplength=640)
        self.head.pack(anchor="w", fill="x", pady=(10, 0))
        self.sub = ttk.Label(f, style="Muted.TLabel", wraplength=640, justify="left")
        self.sub.pack(anchor="w", pady=(2, 0))
        self.models_lbl = ttk.Label(f, style="Muted.TLabel", wraplength=640, justify="left")
        self.models_lbl.pack(anchor="w", pady=(2, 8))

        # buttons + progress first (packed at the bottom), so the notes box can't push them out of view
        b = ttk.Frame(f)
        b.pack(side="bottom", fill="x", pady=(10, 0))
        self.pbar = ttk.Progressbar(f, maximum=1.0)
        self.pbar.pack(side="bottom", fill="x", pady=(8, 0))
        self.status = ttk.Label(f, wraplength=640, justify="left")
        self.status.pack(side="bottom", fill="x", pady=(6, 0))
        self.auto = tk.BooleanVar(value=app.s.get("auto_update", True))
        ttk.Checkbutton(f, text="Check for updates automatically (once a day)", variable=self.auto,
                        command=lambda: app.s.__setitem__("auto_update", self.auto.get())).pack(
            side="bottom", anchor="w", pady=(10, 0))
        p = app.pal
        self.notes = tk.Text(f, height=10, wrap="word", relief="flat", padx=12, pady=10,
                             font=(theme.UI, 10 + theme._S), bg=p["field"], fg=p["text"], highlightthickness=1,
                             highlightbackground=p["line"], borderwidth=0, spacing1=1, spacing3=3)
        self.notes.pack(fill="both", expand=True)
        self.notes.tag_configure("b", font=(theme.UI, 10 + theme._S, "bold"))
        self.notes.tag_configure("i", font=(theme.UI, 10 + theme._S, "italic"))
        self.notes.tag_configure("code", font=("Menlo" if IS_MAC else "Consolas", 9 + theme._S))
        self.notes.tag_configure("h", font=(theme.UI, 11 + theme._S, "bold"), spacing1=6)
        self.notes.tag_configure("li", lmargin1=14, lmargin2=30)
        self.notes.configure(state="disabled")

        self.go = ttk.Button(b, text="Update now", style="Accent.TButton", command=self.update_all, state="disabled")
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

    def _headline(self, text, kind=None):
        colors = {"ok": ("#1a7f37", "#4cc76a"), "new": ("#0969da", "#58a6ff"), "bad": ("#cf222e", "#ff7b72")}
        self.head.configure(text=text, fg=colors[kind][bool(self.app.dark)] if kind else self.app.pal["text"])

    _INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|(?<!\w)_[^_]+_(?!\w)|(?<![\w*])\*[^*\s][^*]*\*(?!\w))")

    def _inline(self, text, extra=()):
        for part in self._INLINE.split(text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                self.notes.insert("end", part[2:-2], ("b", *extra))
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                self.notes.insert("end", part[1:-1], ("code", *extra))
            elif part[0] in "_*" and part[-1] == part[0] and len(part) > 2:
                self.notes.insert("end", part[1:-1], ("i", *extra))
            else:
                self.notes.insert("end", part, extra)

    def _set_notes(self, text, title=None):
        """Release notes (Markdown) shown formatted: bold, italics, code, bullet lists, headings, simple tables."""
        t = self.notes
        t.configure(state="normal")
        t.delete("1.0", "end")
        if title:
            t.insert("end", title + "\n", ("h",))
        for line in (text or "").replace("\r", "").split("\n"):
            s = line.strip()
            if not s:
                t.insert("end", "\n")
            elif "-" in s and re.match(r"^\|?[\s:|-]+\|?$", s):      # table separator |---|---|
                continue
            elif s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                self._inline("   ".join(c for c in cells if c), ("li",))
                t.insert("end", "\n")
            elif s.startswith("#"):
                self._inline(s.lstrip("#").strip(), ("h",))
                t.insert("end", "\n")
            elif re.match(r"^[-*] ", s):
                t.insert("end", "•  ", ("li",))
                self._inline(s[2:], ("li",))
                t.insert("end", "\n")
            else:
                self._inline(s)
                t.insert("end", "\n")
        t.configure(state="disabled")

    def _app_newer(self):
        return bool(self.info and updater.is_newer(self.info["version"]))

    # ------------------------------------------------------------ check
    def check(self):
        if self.busy:
            return
        self.busy = True
        self.go.configure(state="disabled")
        self.recheck.configure(state="disabled")
        self._headline("Checking for updates …")
        self.sub.configure(text="Asking GitHub for the latest version.")
        self.status.configure(text="")
        self.models_lbl.configure(text="Models: checking …")
        have = [k for k in models.all_keys() if models.installed(k)]
        steps = 1 + len(have)                     # GitHub, then each downloaded model: the bar fills 0 -> 100 %
        self.pbar.stop()
        self.pbar.configure(mode="determinate", value=0.02)

        def step(done, text):
            self._ui(lambda: (self.pbar.configure(value=max(0.02, done / steps)), self.status.configure(text=text)))

        def work():
            step(0, "Asking GitHub for the latest version …")
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
            for i, k in enumerate(have):
                step(1 + i, f"Checking the models for updates: {k} ({i + 1} of {len(have)}) …")
                try:
                    if models.check_update(k)[0] == "update":
                        ups.append(k)
                except Exception as e:
                    merr = e
                    break
            step(steps, "")
            self._ui(lambda: self._show_models(ups, merr))
            self._ui(self._checked)
        threading.Thread(target=work, daemon=True).start()

    def _show_app(self, info, err):
        self.info, self.app_err = info, err
        if err:
            self._headline("Couldn't check for updates", "bad")
            self.sub.configure(text=err)
            self._set_notes("")
            return
        self.app.s["last_update_check"] = time.time()
        self.app.s["latest_seen"] = info["version"]
        line = f"Latest on GitHub: {info['version']}"
        if info.get("published"):
            line += f", released {info['published']}"
        if info.get("private"):
            line += "  ·  checked with this PC's GitHub login (private project)"
        self.sub.configure(text=line)
        if self._app_newer():
            self._headline(f"Version {info['version']} is available", "new")
        else:
            self._headline(f"You're on the latest version ({APP_VERSION}) ✔", "ok")
        self._set_notes((info.get("notes") or "").strip() or "No release notes.",
                        title=f"What's new in {info['version']}:")

    def _show_models(self, ups, err):
        self.model_updates = ups
        have = [k for k in models.all_keys() if models.installed(k)]
        if err is not None:
            self.models_lbl.configure(text="Couldn't check the models (Hugging Face not reachable).")
        elif not have:
            self.models_lbl.configure(text="No models downloaded yet.")
        elif ups:
            self.models_lbl.configure(text=f"Models: newer version online for {', '.join(ups)}")
        else:
            self.models_lbl.configure(text=f"Models: all up to date ✔  ({', '.join(have)})")

    def _can_app(self):
        ok, _why = updater.can_install()
        return self._app_newer() and bool(self.info.get("asset")) and ok

    def _checked(self):
        self.busy = False
        self.pbar.configure(value=1.0)              # check finished: full bar
        self.recheck.configure(state="normal")
        todo = self._can_app() or bool(self.model_updates)
        self.go.configure(state="normal" if todo else "disabled")
        if self._app_newer() and not updater.can_install()[0]:
            self.status.configure(text=updater.can_install()[1])
        elif todo:
            what = " and ".join(x for x in (f"{APP_NAME} {self.info['version']}" if self._can_app() else "",
                                            "the model updates" if self.model_updates else "") if x)
            self.status.configure(text=f"Update now downloads {what} and installs it. "
                                       "Your settings and models are kept.")
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
        self.pbar.configure(value=0)
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
