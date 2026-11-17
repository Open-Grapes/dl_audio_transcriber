"""Shared paths, constants and small helpers for dl_transcribe."""

import datetime
import json
import os
import sys
from pathlib import Path

APP_NAME = "dl_transcribe"
APP_VERSION = "1.3"
RELEASE_ASSET = "dl_transcribe-windows.zip"

MEDIA_EXT = {
    # audio
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "wma", "aif", "aiff", "amr", "caf", "ac3",
    # video
    "mp4", "m4v", "mov", "mkv", "avi", "webm", "wmv", "flv", "mts", "m2ts", "ts", "mxf", "3gp", "mpg",
    "mpeg", "ogv",
}

FROZEN = getattr(sys, "frozen", False)
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent          # folder holding the .exe (Mac: .app/Contents/MacOS)
else:
    APP_DIR = Path(__file__).resolve().parent.parent         # repo root when run from source


def asset(*parts):
    """A file shipped with the app: next to the .exe on Windows, inside the bundle (Resources) on the Mac."""
    roots = [APP_DIR / "assets"]
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", APP_DIR))
        roots += [meipass / "assets", APP_DIR.parent / "Resources" / "assets"]
    for r in roots:
        p = r.joinpath(*parts)
        if p.exists():
            return p
    return roots[0].joinpath(*parts)


def open_path(path, reveal=False):
    """Show a folder (or select a file) in Explorer / Finder."""
    import subprocess
    p = Path(path)
    try:
        if IS_WIN:
            if reveal and p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(str(p if p.exists() else p.parent))
        elif IS_MAC:
            if reveal and p.exists():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p if p.exists() else p.parent)])
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
    except OSError:
        pass

# Per-user data on this computer: downloaded models, settings, log.
if IS_WIN:
    SUP = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
elif IS_MAC:
    SUP = Path.home() / "Library" / "Application Support" / APP_NAME
else:
    SUP = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
MODELS_DIR = SUP / "models"
LOG = SUP / f"{APP_NAME}.log"

# name -> (label shown in the menu, approx. download size in MB)
MODELS = {
    "tiny": ("tiny — instant, rough (75 MB)", 75),
    "base": ("base — very fast, quick memos (145 MB)", 145),
    "small": ("small — fast, decent (480 MB)", 484),
    "medium": ("medium — good, slower (1.5 GB)", 1530),
    "large-v3-turbo": ("large-v3-turbo — best speed/accuracy (1.6 GB)", 1620),
    "large-v3": ("large-v3 — most accurate, hardest audio (3.1 GB)", 3090),
}

# Whisper's languages (code -> name). Cantonese (yue) is large-v3 / turbo only.
LANGUAGES = {
    "en": "English", "zh": "Chinese (Mandarin)", "yue": "Cantonese", "ms": "Malay", "ta": "Tamil",
    "id": "Indonesian", "ja": "Japanese", "ko": "Korean", "th": "Thai", "vi": "Vietnamese", "tl": "Tagalog",
    "hi": "Hindi", "bn": "Bengali", "ar": "Arabic", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "nl": "Dutch", "tr": "Turkish", "pl": "Polish",
    "uk": "Ukrainian", "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish", "el": "Greek",
    "he": "Hebrew", "fa": "Persian", "ur": "Urdu", "cs": "Czech", "ro": "Romanian", "hu": "Hungarian",
    "ca": "Catalan", "hr": "Croatian", "bg": "Bulgarian", "lt": "Lithuanian", "la": "Latin", "mi": "Maori",
    "ml": "Malayalam", "cy": "Welsh", "sk": "Slovak", "te": "Telugu", "lv": "Latvian", "sr": "Serbian",
    "az": "Azerbaijani", "sl": "Slovenian", "kn": "Kannada", "et": "Estonian", "mk": "Macedonian",
    "br": "Breton", "eu": "Basque", "is": "Icelandic", "hy": "Armenian", "ne": "Nepali", "mn": "Mongolian",
    "bs": "Bosnian", "kk": "Kazakh", "sq": "Albanian", "sw": "Swahili", "gl": "Galician", "mr": "Marathi",
    "pa": "Punjabi", "si": "Sinhala", "km": "Khmer", "sn": "Shona", "yo": "Yoruba", "so": "Somali",
    "af": "Afrikaans", "oc": "Occitan", "ka": "Georgian", "be": "Belarusian", "tg": "Tajik", "sd": "Sindhi",
    "gu": "Gujarati", "am": "Amharic", "yi": "Yiddish", "lo": "Lao", "uz": "Uzbek", "fo": "Faroese",
    "ht": "Haitian Creole", "ps": "Pashto", "tk": "Turkmen", "nn": "Nynorsk", "mt": "Maltese",
    "sa": "Sanskrit", "lb": "Luxembourgish", "my": "Myanmar (Burmese)", "bo": "Tibetan", "mg": "Malagasy",
    "as": "Assamese", "tt": "Tatar", "haw": "Hawaiian", "ln": "Lingala", "ha": "Hausa", "ba": "Bashkir",
    "jw": "Javanese", "su": "Sundanese",
}
CJK = {"zh", "yue", "ja", "ko", "th", "lo", "km", "my", "bo"}   # no spaces between words

FORMATS = ("pdf", "txt", "srt", "vtt", "json", "csv")


def lang_name(code):
    return LANGUAGES.get(code or "", code or "?")


def ensure_dirs():
    try:
        SUP.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def log(msg):
    ensure_dirs()
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def load_settings():
    try:
        return json.loads((SUP / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data):
    ensure_dirs()
    try:
        (SUP / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def fmt_hms(sec):
    sec = int(max(0, sec or 0))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def find_media(paths):
    """Expand dropped files/folders into media files (folders are searched recursively)."""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix[1:].lower() in MEDIA_EXT)
        elif p.is_file():
            out.append(p)   # let the decoder decide; unknown extensions often still work
    return out
