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
