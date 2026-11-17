"""Launcher used by PyInstaller and for running from source: python run.py"""
import faulthandler
import sys
import traceback
import warnings

warnings.filterwarnings("ignore")

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
