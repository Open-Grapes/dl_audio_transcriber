"""Build dl_transcribe for Windows.

    py -3.13 -m venv .venv
    .venv\\Scripts\\pip install -r requirements.txt
    .venv\\Scripts\\python build.py            # -> dist\\Windows\\dl_transcribe\\ and dl_transcribe-windows.zip

The NVIDIA cuBLAS / cuDNN DLLs are bundled so the GPU works without installing CUDA.
Whisper models are NOT bundled: each one downloads once (to %LOCALAPPDATA%\\dl_transcribe\\models) the first
time it's used, then everything runs offline.
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dltr.common import APP_NAME, RELEASE_ASSET  # noqa: E402

DIST = ROOT / "dist" / "Windows"
ICON = ROOT / "assets" / "icon.ico"


def make_icon():
    if ICON.exists():
        return
    from PIL import Image, ImageDraw
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(32, 92, 214))
    # sound wave bars on the left …
    for x, h in ((40, 40), (64, 84), (88, 120), (112, 64)):
        d.rounded_rectangle((x, 128 - h // 2, x + 14, 128 + h // 2), radius=7, fill=(255, 255, 255))
    # … turning into lines of text on the right
    for i, w in enumerate((70, 54, 70, 40)):
        y = 80 + i * 30
        d.rounded_rectangle((146, y, 146 + w, y + 14), radius=7, fill=(255, 210, 63))
    ICON.parent.mkdir(exist_ok=True)
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((128, 128), Image.LANCZOS).save(ICON.with_suffix(".png"))


def nvidia_binaries():
    """--add-binary args for the pip-installed CUDA DLLs, kept in nvidia\\<lib>\\bin (dltr.cuda looks there)."""
    args = []
    import site
    for sp in site.getsitepackages():
        for dll in Path(sp, "nvidia").glob("*/bin/*.dll"):
            rel = dll.parent.relative_to(Path(sp))
            args += ["--add-binary", f"{dll};{rel.as_posix()}"]
    return args


def pyinstaller():
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                           "--name", APP_NAME, "--icon", str(ICON),
                           "--add-data", f"{ICON};assets",
                           "--collect-all", "tkinterdnd2",
                           "--collect-all", "faster_whisper",      # includes the Silero VAD model
                           "--collect-binaries", "ctranslate2",
                           "--collect-submodules", "av", "--collect-all", "fpdf", "--collect-all", "uharfbuzz",
                           "--collect-submodules", "fontTools",
                           "--collect-all", "sherpa_onnx", "--collect-all", "langid", "--collect-all", "tokenizers",
                           "--add-data", f"{ROOT / 'dltr' / 'web_page.html'};dltr", "--add-data", f"{ROOT / 'assets' / 'icon.png'};assets",
                           "--collect-submodules", "zeroconf", "--collect-submodules", "qrcode",
                           "--hidden-import", "sounddevice", "--collect-all", "_sounddevice_data", "--collect-all", "pypinyin", "--collect-submodules", "pypdf",
                           *nvidia_binaries(),
                           "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
                           "--specpath", str(ROOT / "build"), str(ROOT / "run.py")])


def newer_vc_runtime(internal):
    """PyInstaller copies the MSVC runtime that shipped with Python (14.36). CTranslate2 is built with a newer
    compiler and crashes (access violation in std::mutex) on runtimes older than 14.40, so ship the newer
    system copies instead."""
    import os
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        src = sys32 / name
        if not src.exists():
            continue
        for dst in internal.glob(name):           # case-insensitive on Windows
            if src.stat().st_size and _ver(src) > _ver(dst):
                shutil.copy2(src, dst)
                print(f"Updated {dst.name} {_ver(dst)}")
    if _ver(sys32 / "msvcp140.dll") < (14, 40):
        sys.exit("Install the latest Visual C++ Redistributable (x64) first: the built app needs runtime >= 14.40.")


def _ver(path):
    import ctypes
    from ctypes import wintypes
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return (0,)
    buf = ctypes.create_string_buffer(size)
    ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf)
    p, n = ctypes.c_void_p(), wintypes.UINT()
    ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
    ms, ls = ctypes.cast(p, ctypes.POINTER(wintypes.DWORD * 4)).contents[2:4]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def assemble():
    out = DIST / APP_NAME
    newer_vc_runtime(out / "_internal")
    (out / "assets").mkdir(exist_ok=True)
    shutil.copy2(ICON, out / "assets" / "icon.ico")
    shutil.copytree(ROOT / "assets" / "speakers", out / "assets" / "speakers", dirs_exist_ok=True)
    shutil.copy2(ROOT / "README.md", out / "README.md")
    z = DIST / RELEASE_ASSET
    z.unlink(missing_ok=True)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, Path(APP_NAME) / f.relative_to(out))
    print(f"\nBuilt {out}\nZipped {z} ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    make_icon()
    pyinstaller()
    assemble()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    print(f"Run: {DIST / APP_NAME / (APP_NAME + '.exe')}")
