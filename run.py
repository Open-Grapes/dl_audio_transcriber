"""Launcher used by PyInstaller and for running from source: python run.py"""
import faulthandler
import os
import sys
import traceback
import warnings

warnings.filterwarnings("ignore")

# The window app has no console: sys.stdout / sys.stderr are None, and libraries that print progress bars
# (Hugging Face downloads) crash or hang writing to them. Give them somewhere harmless to write.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from dltr.common import LOG, ensure_dirs, log  # noqa: E402


def _clean_launches():
    """Frozen on Windows, the app's library folder (_internal) is on the DLL search path, and programs it starts
    inherit that: a browser opened from the app (Release page, a Wi-Fi link) then loads the app's own
    MSVCP140 / VCRUNTIME140 and keeps them locked until it closes. Drop the folder while starting them."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    import ctypes
    import subprocess
    import threading
    k32 = ctypes.windll.kernel32
    home = getattr(sys, "_MEIPASS", None)
    lock = threading.Lock()

    def outside(fn):
        def run(*a, **kw):
            with lock:
                k32.SetDllDirectoryW(None)
                try:
                    return fn(*a, **kw)
                finally:
                    k32.SetDllDirectoryW(home)
        return run
    os.startfile = outside(os.startfile)                 # also what webbrowser.open uses on Windows
    exe = os.path.normcase(sys.executable)
    real_exec = subprocess.Popen._execute_child

    def execute_child(self, args, executable, *rest):
        first = executable or (args if isinstance(args, str) else args[0])
        if os.path.normcase(str(first)) == exe:         # our own worker process sets this up itself
            return real_exec(self, args, executable, *rest)
        return outside(real_exec)(self, args, executable, *rest)
    subprocess.Popen._execute_child = execute_child


_clean_launches()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()     # the .exe re-launches itself as the transcription worker
    ensure_dirs()
    _crash = open(LOG.with_name("crash.log"), "a", encoding="utf-8")
    faulthandler.enable(_crash)          # native crashes (e.g. a missing GPU DLL) get a trace too
    try:
        from dltr.__main__ import main
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        log("CRASH\n" + traceback.format_exc())
        raise
