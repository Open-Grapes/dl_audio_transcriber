"""GPU detection. The CUDA libraries (cuBLAS / cuDNN) ship inside the app, so any PC with an NVIDIA card and a
reasonably recent NVIDIA driver uses the GPU automatically. Anything else (AMD, Intel, no GPU, old driver)
runs on the CPU."""

import os
import sys
from pathlib import Path

_done = False
_problem = ""


def dll_dirs():
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    roots += [Path(p) for p in sys.path if p and Path(p).is_dir()]
    out = []
    for r in roots:
        nv = r / "nvidia"
        if nv.is_dir():
            out += [d for d in sorted(nv.glob("*/bin")) if d.is_dir()]
    return list(dict.fromkeys(out))


def setup():
    global _done
    if _done or os.name != "nt":
        return
    _done = True
    dirs = dll_dirs()
    for d in dirs:
        try:
            os.add_dll_directory(str(d))
        except OSError:
            pass
    # CTranslate2 loads cuDNN lazily with LoadLibrary, which also searches PATH.
    os.environ["PATH"] = os.pathsep.join([str(d) for d in dirs] + [os.environ.get("PATH", "")])


def display_adapters():
    """Names of the graphics cards Windows knows about (no extra tools needed)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class DD(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]
    names, i = [], 0
    while True:
        dd = DD()
        dd.cb = ctypes.sizeof(dd)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.DeviceString and dd.DeviceString not in names:
            names.append(dd.DeviceString)
        i += 1
    return names


def gpu():
    """(use_cuda, description). Sets problem() when there is a GPU we can't use."""
    global _problem
    if sys.platform == "darwin":
        import platform
        return False, "Apple Silicon CPU" if platform.machine() == "arm64" else "Intel Mac CPU"
    setup()
    adapters = display_adapters()
    nvidia = [a for a in adapters if "nvidia" in a.lower()]
    if os.environ.get("DLTR_FORCE_CPU"):
        _problem = "The GPU failed on this PC, so the CPU is used. Updating the NVIDIA driver usually fixes this."
        return False, "CPU"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return True, nvidia[0] if nvidia else "NVIDIA GPU"
    except Exception:
        pass
    if nvidia:
        _problem = (f"{nvidia[0]} found but its driver is too old for CUDA 12. "
                    "Update it from nvidia.com/drivers to use the GPU (much faster).")
    elif adapters:
        _problem = f"{adapters[0]} isn't supported (NVIDIA only), so the CPU is used. Pick small or base for speed."
    return False, "CPU"


def problem():
    return _problem
