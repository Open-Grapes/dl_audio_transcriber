"""The Whisper models: what each one is, whether it's on this PC, update checks and removal.
Downloading itself runs in a worker process (see worker.py) so it can be cancelled at once."""

import datetime
import shutil
from pathlib import Path

from .common import MODELS, MODELS_DIR

REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}
FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.json",
         "vocabulary.txt")

# key -> (speed, accuracy, what it's for)
INFO = {
    "tiny": ("Fastest", "Rough", "Instant drafts and testing. Misses words and struggles with accents, "
                                  "noise and non-English speech."),
    "base": ("Very fast", "Fair", "Quick voice memos in clear English. Still weak on other languages."),
    "small": ("Fast", "Good", "A good choice on PCs without an NVIDIA GPU: decent accuracy at a usable speed "
                              "on the CPU."),
    "medium": ("Medium", "Very good", "More accurate than small for accents and other languages, but slow on "
                                      "the CPU. On a GPU, large-v3-turbo is both faster and better."),
    "large-v3-turbo": ("Fast (GPU)", "Excellent", "Best all-rounder on an NVIDIA GPU: almost large-v3 accuracy "
                                                  "at many times the speed. Can't translate (the app switches to "
                                                  "large-v3 for Translate to English)."),
    "large-v3": ("Slow", "Best", "Most accurate, best for hard audio and rare languages, and the model used "
                                 "for Translate to English. Slow without a GPU."),
}


def recommended(has_gpu):
    return {"large-v3-turbo"} if has_gpu else {"small"}


def path(key):
    return MODELS_DIR / key


def installed(key):
    """All the files are there (model.bin alone can land before the tokenizer during a download)."""
    p = path(key)
    return all((p / f).exists() for f in ("config.json", "model.bin", "tokenizer.json")) and \
        any((p / f).exists() for f in ("vocabulary.json", "vocabulary.txt"))


def disk_bytes(key=None):
    root = path(key) if key else MODELS_DIR
    try:
        return sum(f.stat().st_size for f in Path(root).rglob("*") if f.is_file())
    except OSError:
        return 0


def _meta(key):
    """(commit, etag, timestamp) that Hugging Face recorded when model.bin was downloaded, or None."""
    p = path(key) / ".cache" / "huggingface" / "download" / "model.bin.metadata"
    try:
        lines = p.read_text(encoding="utf-8").split("\n")
        return lines[0].strip(), lines[1].strip(), float(lines[2])
    except (OSError, IndexError, ValueError):
        return None


def downloaded_on(key):
    m = _meta(key)
    if m:
        return datetime.date.fromtimestamp(m[2]).isoformat()
    try:
        return datetime.date.fromtimestamp((path(key) / "model.bin").stat().st_mtime).isoformat()
    except OSError:
        return ""


def check_update(key):
    """Ask Hugging Face whether the model files changed since they were downloaded.
    Returns ("update" | "up-to-date", date of the online version)."""
    from huggingface_hub import HfApi
    info = HfApi().model_info(REPOS[key], files_metadata=True)
    date = info.last_modified.date().isoformat() if info.last_modified else ""
    m = _meta(key)
    if m and m[0] == info.sha:
        return "up-to-date", date
    # a new commit may only touch the README, so compare the files we actually use
    meta_dir = path(key) / ".cache" / "huggingface" / "download"
    for s in info.siblings or []:
        if s.rfilename not in FILES:
            continue
        remote = (s.lfs.sha256 if s.lfs else s.blob_id) if (s.lfs or s.blob_id) else None
        try:
            local = (meta_dir / f"{s.rfilename}.metadata").read_text(encoding="utf-8").split("\n")[1].strip()
        except (OSError, IndexError):
            local = None
        if not (path(key) / s.rfilename).exists() or (remote and local and remote != local):
            return "update", date
        if remote and not local:              # downloaded some other way: fall back to the size
            if s.size and (path(key) / s.rfilename).stat().st_size != s.size:
                return "update", date
    return "up-to-date", date


def remove(key):
    p = path(key)
    if p.exists():
        shutil.rmtree(p)


def remove_all():
    for k in MODELS:
        remove(k)


def fmt_size(b):
    return f"{b / 1e9:.1f} GB" if b >= 1e9 else f"{b / 1e6:.0f} MB" if b >= 1e6 else f"{b / 1e3:.0f} KB"
