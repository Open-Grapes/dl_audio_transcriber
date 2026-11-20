"""Read text aloud, offline, with a native-sounding voice for the language (sherpa-onnx, already in the app).

Voices, best first for each language:
  * Kokoro multi-lang v1.1 (Apache-2.0, ~215 MB): Mandarin (55 female, 45 male speakers) and English
  * Malaysian voices by mesolitica (Yasmin, Osman, Idayu, Husein; native Malay speakers, ~115 MB each),
    converted to ONNX for this app
  * Piper voices (sherpa-onnx conversions, ~60 MB each): native speakers for ~40 languages (German, French,
    Spanish, Hindi, Indonesian, Vietnamese, Arabic, Russian, Turkish, …); licence per voice (MODEL_CARD)
  * Meta MMS-TTS (CC-BY-NC-4.0, ~115 MB per language, one voice): ~1,100 languages, the fallback

pick() chooses the voice automatically from the language (and female / male); the user can choose another
speaker per language (settings "voices"). Each voice downloads once, the first time it's needed.
synthesize() writes one WAV with a short pause between sentences and returns where each sentence starts and ends,
so the text can be clicked to play it."""

import re
import time
import wave
from pathlib import Path

from . import langs
from .common import MODELS_DIR, SUP, log

KOKORO_REPO = "csukuangfj/kokoro-int8-multi-lang-v1_1"
KOKORO_MB = 215
MMS_REPO = "willwade/mms-tts-multilingual-models-onnx"
MMS_MB = 115
PIPER_MB = 63
ESPEAK_KEY = "tts-espeak"                     # pronunciation data shared by the Piper voices (~18 MB)
ESPEAK_REPO = "csukuangfj/vits-piper-en_US-amy-medium"
MS_URL = "https://github.com/Open-Grapes/dl_audio_transcriber/releases/download/voices-1/{name}.zip"
GAP = 0.45                                    # seconds of silence between sentences

# NLLB code (first part) -> MMS code, where they differ
MMS_CODE = {"zsm": "zlm", "arb": "ara", "pes": "fas", "khk": "mon", "lvs": "lav", "npi": "nep", "ory": "ori",
            "plt": "mlg", "uzn": "uzb", "azj": "azb", "gaz": "orm", "ydd": "yid", "als": "sqi", "ekk": "est"}
# letters an MMS voice doesn't know, and the closest sound it does
SUBST = {"v": "f", "V": "F", "q": "k", "Q": "K", "x": "ks", "X": "Ks", "z": "s", "Z": "S"}

# ---------------------------------------------------------------- the voice catalog
# Kokoro: speakers 0-2 English (female), 3-57 Mandarin female, 58-102 Mandarin male. A spread of them:
_ZH_F = [50, 3, 9, 15, 21, 27, 33, 39, 45, 55]
_ZH_M = [60, 58, 63, 68, 73, 78, 83, 88, 93, 102]
# mesolitica Malay voices (name, file, speaker id, gender)
_MS = [("Yasmin", "ms-yasmin", 0, "female"), ("Osman", "ms-osman", 0, "male"),
       ("Idayu", "ms-multi", 1, "female"), ("Husein", "ms-multi", 0, "male")]
_MS_MB = {"ms-yasmin": 114, "ms-osman": 114, "ms-multi": 121}
# Piper voices with one speaker: locale -> [(name, quality, gender)]
_PIPER = {
    "ar_JO": [("kareem", "medium", "male")],
    "ca_ES": [("upc_ona", "medium", "female")],
    "cs_CZ": [("jirka", "medium", "male")],
    "cy_GB": [("gwryw_gogleddol", "medium", "male")],
    "da_DK": [("talesyntese", "medium", "male")],
    "de_DE": [("thorsten", "high", "male"), ("kerstin", "low", "female"), ("ramona", "low", "female"),
              ("eva_k", "x_low", "female"), ("karlsson", "low", "male"), ("pavoque", "low", "male")],
    "el_GR": [("rapunzelina", "low", "female")],
    "en_GB": [("alba", "medium", "female"), ("alan", "medium", "male"), ("cori", "high", "female"),
              ("jenny_dioco", "medium", "female"), ("northern_english_male", "medium", "male"),
              ("southern_english_female", "medium", "female"), ("southern_english_male", "medium", "male")],
    "en_US": [("ryan", "high", "male"), ("lessac", "high", "female"), ("amy", "medium", "female"),
              ("joe", "medium", "male"), ("john", "medium", "male"), ("kristin", "medium", "female"),
              ("hfc_female", "medium", "female"), ("hfc_male", "medium", "male"), ("norman", "medium", "male"),
              ("bryce", "medium", "male"), ("kathleen", "low", "female"), ("ljspeech", "high", "female")],
    "es_AR": [("daniela", "high", "female")],
    "es_ES": [("davefx", "medium", "male"), ("carlfm", "x_low", "male")],
    "es_MX": [("claude", "high", "male"), ("ald", "medium", "male")],
    "fa_IR": [("amir", "medium", "male"), ("gyro", "medium", "male"), ("ganji", "medium", "male")],
    "fi_FI": [("harri", "medium", "male")],
    "fr_FR": [("siwis", "medium", "female"), ("tom", "medium", "male"), ("gilles", "low", "male")],
    "hi_IN": [("priyamvada", "medium", "female"), ("pratham", "medium", "male"), ("rohan", "medium", "male")],
    "hu_HU": [("anna", "medium", "female"), ("berta", "medium", "female"), ("imre", "medium", "male")],
    "id_ID": [("news_tts", "medium", "female")],
    "is_IS": [("salka", "medium", "female"), ("ugla", "medium", "female"), ("bui", "medium", "male"),
              ("steinn", "medium", "male")],
    "it_IT": [("paola", "medium", "female"), ("riccardo", "x_low", "male")],
    "ka_GE": [("natia", "medium", "female")],
    "kk_KZ": [("raya", "x_low", "female"), ("iseke", "x_low", "male")],
    "lb_LU": [("marylux", "medium", "female")],
    "lv_LV": [("aivars", "medium", "male")],
    "ne_NP": [("chitwan", "medium", "male")],
    "nl_BE": [("nathalie", "medium", "female"), ("rdh", "medium", "male")],
    "no_NO": [("talesyntese", "medium", "male")],
    "pl_PL": [("gosia", "medium", "female"), ("darkman", "medium", "male"), ("justyna_wg_glos", "medium", "female"),
              ("meski_wg_glos", "medium", "male")],
    "pt_BR": [("faber", "medium", "male"), ("cadu", "medium", "male"), ("jeff", "medium", "male")],
    "pt_PT": [("tugao", "medium", "male")],
    "ro_RO": [("mihai", "medium", "male")],
    "ru_RU": [("irina", "medium", "female"), ("denis", "medium", "male"), ("dmitri", "medium", "male"),
              ("ruslan", "medium", "male")],
    "sk_SK": [("lili", "medium", "female")],
    "sl_SI": [("artur", "medium", "male")],
    "sv_SE": [("lisa", "medium", "female"), ("nst", "medium", "male")],
    "tr_TR": [("dfki", "medium", "male"), ("fahrettin", "medium", "male"), ("fettah", "medium", "male")],
    "uk_UA": [("lada", "x_low", "female")],
    "vi_VN": [("vais1000", "medium", "female"), ("25hours_single", "low", "female")],
    "zh_CN": [("huayan", "medium", "female")],
}
_NICE = {"news_tts": "News reader", "hfc_female": "HFC female", "hfc_male": "HFC male", "25hours_single": "25hours",
         "jenny_dioco": "Jenny", "northern_english_male": "Northern English male",
         "southern_english_female": "Southern English female", "southern_english_male": "Southern English male",
         "upc_ona": "Ona", "gwryw_gogleddol": "Gwryw", "eva_k": "Eva", "vais1000": "Vais"}
_QUALITY = {"high": "Very good", "medium": "Good", "low": "Fair", "x_low": "Basic"}
_ISO = {"ar": "arb_Arab", "ca": "cat_Latn", "cs": "ces_Latn", "cy": "cym_Latn", "da": "dan_Latn", "de": "deu_Latn",
        "el": "ell_Grek", "en": "eng_Latn", "es": "spa_Latn", "fa": "pes_Arab", "fi": "fin_Latn", "fr": "fra_Latn",
        "hi": "hin_Deva", "hu": "hun_Latn", "id": "ind_Latn", "is": "isl_Latn", "it": "ita_Latn", "ka": "kat_Geor",
        "kk": "kaz_Cyrl", "lb": "ltz_Latn", "lv": "lvs_Latn", "ne": "npi_Deva", "nl": "nld_Latn", "no": "nob_Latn",
        "pl": "pol_Latn", "pt": "por_Latn", "ro": "ron_Latn", "ru": "rus_Cyrl", "sk": "slk_Latn", "sl": "slv_Latn",
        "sv": "swe_Latn", "tr": "tur_Latn", "uk": "ukr_Cyrl", "vi": "vie_Latn", "zh": "zho_Hans"}
_REGION = {"en_US": "US", "en_GB": "UK", "es_ES": "Spain", "es_MX": "Mexico", "es_AR": "Argentina",
           "pt_BR": "Brazil", "pt_PT": "Portugal", "nl_BE": "Belgium", "zh_CN": ""}


def _base(code):
    """'zho_Hant' / 'yue_Hant' -> the family used to find voices."""
    if code in ("zho_Hans", "zho_Hant", "yue_Hant"):
        return "zho_Hans"
    return code


def voices(code):
    """Every voice for a language (NLLB code), best first. Each: dict(id, name, gender, quality, engine, key, sid,
    mb, license, note)."""
    code = _base(code)
    out = []
    if code == "zho_Hans":
        out += [dict(id=f"kokoro:{s}", name=f"Mandarin female {i + 1}", gender="female", quality="Natural",
                     engine="kokoro", key="tts-kokoro", sid=s, mb=KOKORO_MB, license="Apache-2.0")
                for i, s in enumerate(_ZH_F)]
        out += [dict(id=f"kokoro:{s}", name=f"Mandarin male {i + 1}", gender="male", quality="Natural",
                     engine="kokoro", key="tts-kokoro", sid=s, mb=KOKORO_MB, license="Apache-2.0")
                for i, s in enumerate(_ZH_M)]
    if code == "eng_Latn":
        out += [dict(id=f"kokoro:{s}", name=n, gender="female", quality="Natural", engine="kokoro",
                     key="tts-kokoro", sid=s, mb=KOKORO_MB, license="Apache-2.0")
                for s, n in ((0, "Maple (US)"), (1, "Sol (US)"), (2, "Vale (UK)"))]
    if code == "zsm_Latn":
        out += [dict(id=f"{f}:{sid}", name=f"{n} (Malaysian)", gender=g, quality="Natural", engine="vits",
                     key=f"tts-{f}", sid=sid, mb=_MS_MB[f], license="see mesolitica (Hugging Face)")
                for n, f, sid, g in _MS]
    rank = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
    piper = []
    for loc, vs in _PIPER.items():
        if _ISO.get(loc.split("_")[0]) != code:
            continue
        reg = _REGION.get(loc, loc.split("_")[1])
        for n, q, g in vs:
            nice = _NICE.get(n) or n.replace("_wg_glos", "").replace("_", " ").title()
            piper.append((rank[q], loc != "en_US", dict(
                id=f"piper:{loc}-{n}-{q}", name=f"{nice}" + (f" ({reg})" if reg else ""), gender=g,
                quality=_QUALITY[q], engine="vits", key=f"tts-piper-{loc}-{n}-{q}", sid=0, mb=PIPER_MB,
                license="see the voice's MODEL_CARD")))
    out += [x[2] for x in sorted(piper, key=lambda x: (x[0], x[1]))]      # best sounding first
    mms = MMS_CODE.get(code.split("_")[0], code.split("_")[0])
    if code != "zho_Hans":
        out.append(dict(id=f"mms:{mms}", name="MMS voice (Meta)", gender="", quality="Fair", engine="vits",
                        key=f"tts-mms-{mms}", sid=0, mb=MMS_MB, license="CC-BY-NC-4.0"))
    return out


def by_id(vid, code=None):
    for v in voices(code) if code else []:
        if v["id"] == vid:
            return v
    for c in set(_ISO.values()) | {"zsm_Latn"}:
        for v in voices(c):
            if v["id"] == vid:
                return v
    if vid.startswith("mms:"):
        return dict(id=vid, name="MMS voice (Meta)", gender="", quality="Fair", engine="vits",
                    key=f"tts-mms-{vid[4:]}", sid=0, mb=MMS_MB, license="CC-BY-NC-4.0")
    return None


def pick(code, gender="female", chosen=None):
    """The voice to use: the one the user chose for this language, else the best one of the wanted gender
    (else the best one)."""
    vs = voices(code)
    if chosen:
        v = next((v for v in vs if v["id"] == chosen), None)
        if v:
            return v
    return next((v for v in vs if v["gender"] == gender), None) or (vs[0] if vs else None)


# ---------------------------------------------------------------- files on disk
def folder_of(key):
    return MODELS_DIR / key


def _piper_parts(key):
    """'tts-piper-de_DE-thorsten-high' -> ('de_DE', 'thorsten', 'high')."""
    rest = key[len("tts-piper-"):]
    loc, rest = rest[:5], rest[6:]
    name, q = rest.rsplit("-", 1)
    return loc, name, q


def model_files(key):
    """(model.onnx, tokens.txt, data_dir or '') for a vits voice."""
    f = folder_of(key)
    if key.startswith("tts-mms-"):
        c = key[len("tts-mms-"):]
        return f / c / "model.onnx", f / c / "tokens.txt", ""
    if key.startswith("tts-piper-"):
        loc, name, q = _piper_parts(key)
        return f / f"{loc}-{name}-{q}.onnx", f / "tokens.txt", folder_of(ESPEAK_KEY) / "espeak-ng-data"
    return f / "model.onnx", f / "tokens.txt", ""


def installed_key(key):
    f = folder_of(key)
    if key == "tts-kokoro":
        return all((f / n).exists() for n in ("model.int8.onnx", "voices.bin", "tokens.txt")) and \
            (f / "espeak-ng-data").is_dir() and (f / "dict").is_dir()
    if key == ESPEAK_KEY:
        return (f / "espeak-ng-data" / "phontab").exists()
    m, t, d = model_files(key)
    return m.exists() and t.exists() and (not d or (Path(d) / "phontab").exists())


def installed(v):
    return installed_key(v["key"])


def repo_of_key(key):
    if key == "tts-kokoro":
        return KOKORO_REPO
    if key.startswith("tts-mms-"):
        return MMS_REPO
    if key.startswith("tts-piper-"):
        loc, name, q = _piper_parts(key)
        return f"csukuangfj/vits-piper-{loc}-{name}-{q}"
    if key == ESPEAK_KEY:
        return ESPEAK_REPO
    return None                                   # mesolitica: our own download (GitHub)


def main_file(key):
    if key == "tts-kokoro":
        return "model.int8.onnx"
    if key.startswith("tts-mms-"):
        return f"{key[len('tts-mms-'):]}/model.onnx"
    if key.startswith("tts-piper-"):
        loc, name, q = _piper_parts(key)
        return f"{loc}-{name}-{q}.onnx"
    if key == ESPEAK_KEY:
        return "espeak-ng-data/phontab"
    return "model.onnx"


def size_key(key):
    if key == "tts-kokoro":
        return KOKORO_MB
    if key.startswith("tts-mms-"):
        return MMS_MB
    if key.startswith("tts-piper-"):
        return PIPER_MB
    if key == ESPEAK_KEY:
        return 18
    return _MS_MB.get(key[len("tts-"):], 115)


def label_key(key):
    if key == "tts-kokoro":
        return "Kokoro voices: Mandarin + English"
    if key == ESPEAK_KEY:
        return "Pronunciation data for Piper voices"
    if key.startswith("tts-mms-"):
        c = key[len("tts-mms-"):]
        rev = {v: k for k, v in MMS_CODE.items()}
        base = rev.get(c, c)
        name = next((langs.name(x) for x in langs.NLLB if x.split("_")[0] == base), c)
        return f"{name} voice (Meta MMS)"
    if key.startswith("tts-piper-"):
        loc, name, q = _piper_parts(key)
        lang = langs.name(_ISO.get(loc[:2], "")) if _ISO.get(loc[:2]) else loc
        return f"{lang} voice: {name.replace('_', ' ').title()} (Piper)"
    n = {"ms-yasmin": "Yasmin", "ms-osman": "Osman", "ms-multi": "Idayu + Husein"}.get(key[len("tts-"):], key)
    return f"Malay voice: {n} (mesolitica)"


def license_key(key):
    if key == "tts-kokoro":
        return "Apache-2.0"
    if key.startswith("tts-mms-"):
        return "CC-BY-NC-4.0"
    if key.startswith("tts-piper-") or key == ESPEAK_KEY:
        return "per voice" if key != ESPEAK_KEY else "GPL-3.0"
    return "mesolitica"


def download_key(key, progress=None):
    """Download a voice (and the shared pronunciation data a Piper voice needs)."""
    from huggingface_hub import snapshot_download
    try:
        from faster_whisper.utils import disabled_tqdm      # no progress bar: the app has no console
        kw = {"tqdm_class": disabled_tqdm}
    except Exception:
        kw = {}
    f = folder_of(key)
    if progress:
        progress(None, f"Downloading the voice (one time, about {size_key(key)} MB) …")
    if key == "tts-kokoro":
        snapshot_download(KOKORO_REPO, local_dir=str(f),
                          allow_patterns=["model.int8.onnx", "voices.bin", "tokens.txt", "*.fst", "lexicon-*.txt",
                                          "espeak-ng-data/*", "dict/*", "LICENSE"], **kw)
    elif key == ESPEAK_KEY:
        snapshot_download(ESPEAK_REPO, local_dir=str(f), allow_patterns=["espeak-ng-data/*"], **kw)
    elif key.startswith("tts-mms-"):
        c = key[len("tts-mms-"):]
        if not exists_online(key):
            raise RuntimeError("There's no offline voice for this language yet.")
        snapshot_download(MMS_REPO, local_dir=str(f), allow_patterns=[f"{c}/model.onnx", f"{c}/tokens.txt"], **kw)
    elif key.startswith("tts-piper-"):
        if not installed_key(ESPEAK_KEY):
            download_key(ESPEAK_KEY)
        loc, name, q = _piper_parts(key)
        snapshot_download(repo_of_key(key), local_dir=str(f),
                          allow_patterns=[f"{loc}-{name}-{q}.onnx", "tokens.txt", "MODEL_CARD"], **kw)
    else:                                                  # mesolitica Malay voices: a zip on our GitHub release
        _download_zip(MS_URL.format(name=key[len("tts-"):]), f)
    if not installed_key(key):
        raise RuntimeError("The voice download is incomplete. Try again.")


def _download_zip(url, dest):
    import io
    import urllib.request
    import zipfile
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": "dl_transcribe"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("This Malaysian voice isn't online yet: it's published together with the next app "
                               "version. Until then, Malay is read with the MMS voice (choose it in Choose the "
                               "Malay voice…).") from None
        raise
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.endswith("/") or ".." in n or n.startswith(("/", "\\")):
                continue
            (dest / Path(n).name).write_bytes(z.read(n))


def exists_online(key):
    """Is there an MMS voice for this language? (The others are in the catalog, so they exist.)"""
    if not key.startswith("tts-mms-"):
        return True
    from huggingface_hub import HfApi
    c = key[len("tts-mms-"):]
    try:
        files = [x.path for x in HfApi().list_repo_tree(MMS_REPO, path_in_repo=c)]
    except Exception as e:
        if "404" in str(e) or "not found" in str(e).lower() or "EntryNotFound" in type(e).__name__:
            return False
        raise
    return any(p.endswith("model.onnx") for p in files)


# ---------------------------------------------------------------- speaking
_cache = {}


def _engine(v, threads=4):
    import sherpa_onnx as so
    k = v["key"]
    if k in _cache:
        return _cache[k]
    f = folder_of(k)
    if v["engine"] == "kokoro":
        model = so.OfflineTtsModelConfig(kokoro=so.OfflineTtsKokoroModelConfig(
            model=str(f / "model.int8.onnx"), voices=str(f / "voices.bin"), tokens=str(f / "tokens.txt"),
            data_dir=str(f / "espeak-ng-data"), dict_dir=str(f / "dict"),
            lexicon=f"{f / 'lexicon-us-en.txt'},{f / 'lexicon-zh.txt'}"), num_threads=threads)
        cfg = so.OfflineTtsConfig(model=model, rule_fsts=f"{f / 'phone-zh.fst'},{f / 'date-zh.fst'},"
                                                         f"{f / 'number-zh.fst'}")
        tokens = None
    else:
        m, t, d = model_files(k)
        model = so.OfflineTtsModelConfig(vits=so.OfflineTtsVitsModelConfig(
            model=str(m), tokens=str(t), data_dir=str(d)), num_threads=threads)
        cfg = so.OfflineTtsConfig(model=model)
        tokens = None
        if k.startswith(("tts-mms-", "tts-ms-")):               # letter-based voices: fit the text to them
            tokens = {ln.rsplit(" ", 1)[0] if ln.count(" ") else ln
                      for ln in Path(t).read_text(encoding="utf-8").splitlines() if ln} | {" "}
    _cache.clear()                      # one voice in memory at a time
    _cache[k] = (so.OfflineTts(cfg), tokens)
    return _cache[k]


def _fit_letters(text, tokens):
    """Fit text to a letter-based voice's alphabet: swap letters it doesn't have for the nearest sound, keep
    punctuation it knows (pauses), drop the rest."""
    out = []
    for c in text:
        if c in tokens:
            out.append(c)
        elif c.lower() in tokens:
            out.append(c.lower())
        elif c in SUBST and all(x in tokens or x.lower() in tokens for x in SUBST[c]):
            out.append(SUBST[c].lower() if SUBST[c].lower()[0] in tokens else SUBST[c])
        elif c.isspace() or c in "-–—":
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


_MS_ONES = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "lapan", "sembilan"]


def _ms_num(n):
    """Malay number words: 65 -> 'enam puluh lima', 2024 -> 'dua ribu dua puluh empat'."""
    n = int(n)
    if n == 0:
        return "kosong"
    if n < 10:
        return _MS_ONES[n]
    if n == 10:
        return "sepuluh"
    if n == 11:
        return "sebelas"
    if n < 20:
        return _MS_ONES[n - 10] + " belas"
    if n < 100:
        return _MS_ONES[n // 10] + " puluh" + (" " + _MS_ONES[n % 10] if n % 10 else "")
    if n < 1000:
        h = "seratus" if n // 100 == 1 else _MS_ONES[n // 100] + " ratus"
        return h + (" " + _ms_num(n % 100) if n % 100 else "")
    if n < 1_000_000:
        t = "seribu" if n // 1000 == 1 else _ms_num(n // 1000) + " ribu"
        return t + (" " + _ms_num(n % 1000) if n % 1000 else "")
    if n < 1_000_000_000:
        return _ms_num(n // 1_000_000) + " juta" + (" " + _ms_num(n % 1_000_000) if n % 1_000_000 else "")
    return " ".join(_MS_ONES[int(d)] or "kosong" for d in str(n))


def _prepare(text, v, code):
    if code in ("zsm_Latn", "ind_Latn") and v["key"].startswith(("tts-ms-", "tts-mms-")):
        text = re.sub(r"(\d+)%", lambda m: m.group(1) + " peratus", text)
        text = re.sub(r"\d+", lambda m: _ms_num(m.group(0)), text.replace(",000", "000"))
    return text


def synthesize(texts, code, voice=None, out=None, progress=None, cancel=None, speed=1.0, gender="female"):
    """Read the sentences aloud into the WAV file out. voice: a voice id (None = automatic). Returns
    {"times": [(start, end)], "duration", "voice", "voice_name"}."""
    import numpy as np
    v = by_id(voice, code) if voice else None
    v = v or pick(code, gender)
    if v is None:
        raise RuntimeError("No voice for this language.")
    if not installed(v):
        download_key(v["key"], progress)
    if progress:
        progress(0.0, f"Loading the voice ({v['name']}) …")
    tts, tokens = _engine(v)
    sr = tts.sample_rate
    gap = np.zeros(int(GAP * sr), np.float32)
    lead = np.zeros(int(0.3 * sr), np.float32)               # a breath before the first word
    pieces, times, t, t0 = [lead], [], 0.3, time.time()
    n = len(texts)
    for i, text in enumerate(texts):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Stopped.")
        say = _prepare((text or "").strip(), v, code)
        if tokens is not None:
            say = _fit_letters(say, tokens)
        audio = np.zeros(0, np.float32)
        if say and re.search(r"\w", say):
            audio = np.asarray(tts.generate(say, sid=v["sid"], speed=speed).samples, np.float32)
        dur = len(audio) / sr
        times.append((round(t, 3), round(t + dur, 3)))
        pieces += [audio, gap]
        t += dur + GAP
        if progress:
            el = time.time() - t0
            left = el / (i + 1) * (n - i - 1)
            progress((i + 1) / n, f"Reading aloud ({v['name']}) … sentence {i + 1} of {n}" +
                     (f" · ~{left:.0f} s left" if i < n - 1 else ""))
    audio = np.concatenate(pieces)
    peak = float(np.abs(audio).max()) if len(audio) else 0
    if peak > 0.98:
        audio = audio * (0.98 / peak)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((audio * 32767).astype("<i2").tobytes())
    log(f"tts: {n} sentences, {langs.name(code)}, {v['id']}, {len(audio) / sr:.1f}s audio in "
        f"{time.time() - t0:.1f}s")
    return {"times": times, "duration": round(len(audio) / sr, 3), "voice": v["id"], "voice_name": v["name"]}


def voices_dir():
    return SUP / "voices"
