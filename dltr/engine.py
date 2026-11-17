"""Transcription with faster-whisper (Whisper on CTranslate2), fully offline once a model is downloaded."""

import csv
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import cuda
from .common import CJK, MODELS, MODELS_DIR, fmt_hms, lang_name, log

cuda.setup()

_lock = threading.Lock()
_loaded = {}          # (model, device) -> WhisperModel


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ models
def model_dir(name):
    return MODELS_DIR / name


def is_downloaded(name):
    from .models import installed
    return installed(name)


def _dir_size(p):
    try:
        return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())
    except OSError:
        return 0


def download(name, progress=None):
    """Download a model into MODELS_DIR (resumes if interrupted). progress(fraction, text)."""
    from faster_whisper.utils import download_model
    from . import models
    dest = model_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    total = models.size_mb(name) * 1e6
    done = threading.Event()
    err = []

    def run():
        try:
            if name in models.EXTRA:          # translation / AI models
                from huggingface_hub import snapshot_download
                snapshot_download(models.REPOS[name], local_dir=str(dest), allow_patterns=models.DOWNLOAD_PATTERNS)
            else:
                download_model(name, output_dir=str(dest))
        except Exception as e:   # network down, disk full …
            err.append(e)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    while not done.wait(0.5):
        if progress:
            got = _dir_size(dest)
            progress(min(got / total, 0.99), f"Downloading {name} model … {got / 1e6:,.0f} / ~{total / 1e6:,.0f} MB "
                                             "(one time only)")
    if err:
        raise RuntimeError(f"Couldn't download the {name} model (needs internet the first time): {err[0]}")
    if not is_downloaded(name):
        raise RuntimeError(f"The {name} model download is incomplete. Try again.")


_MPS = None


def _mps_ok():
    """Apple Silicon Macs: CTranslate2's Metal (MPS) backend can use the Mac's GPU. It's experimental and was
    slower than the CPU on the test Mac, so it's opt-in: set DLTR_USE_MPS=1 to try it."""
    global _MPS
    if _MPS is None:
        _MPS = (sys.platform == "darwin" and bool(os.environ.get("DLTR_USE_MPS"))
                and not os.environ.get("DLTR_FORCE_CPU"))
        if _MPS:
            try:
                import ctranslate2
                _MPS = "mps" in (getattr(ctranslate2, "get_supported_compute_types", None) and
                                 _mps_types(ctranslate2) or ())
            except Exception:
                _MPS = False
    return _MPS


def _mps_types(ctranslate2):
    try:
        return ("mps",) if ctranslate2.get_supported_compute_types("mps") else ()
    except Exception:
        return ()


def get_model(name, progress=None):
    """Load (downloading first if needed) and cache a model. Returns (model, device)."""
    from faster_whisper import WhisperModel
    has_gpu, _ = cuda.gpu()
    device = "cuda" if has_gpu else ("mps" if _mps_ok() else "cpu")
    with _lock:
        if (name, device) in _loaded:
            return _loaded[(name, device)], device
        if not is_downloaded(name):
            download(name, progress)
        if progress:
            progress(None, f"Loading {name} model on {'CPU' if device == 'cpu' else 'GPU'} …")
        for k in list(_loaded):          # keep one model in memory at a time
            del _loaded[k]
        try:
            m = WhisperModel(str(model_dir(name)), device=device,
                             compute_type="int8" if device == "cpu" else "float16")
            if device != "cpu":      # run the encoder once so missing/old GPU libraries fail here, not mid-file
                import numpy as np
                m.detect_language(np.zeros(16000, dtype=np.float32))
        except Exception as e:
            if device == "cpu":
                raise
            log(f"GPU ({device}) load failed, falling back to CPU: {e}")
            if device == "mps":
                global _MPS
                _MPS = False
            device = "cpu"
            m = WhisperModel(str(model_dir(name)), device="cpu", compute_type="int8")
        _loaded[(name, device)] = m
        return m, device


def probe_duration(path):
    try:
        import av
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1e6
            a = next((s for s in c.streams if s.type == "audio"), None)
            if a is not None and a.duration and a.time_base:
                return float(a.duration * a.time_base)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ transcription
def transcribe(path, opts, on_progress=None, on_segment=None, cancel=None):
    """Transcribe one file.

    opts: model, language (None = detect), task ("transcribe" | "translate"), multilingual (bool),
          hints (str), word_timestamps (bool).
    on_progress(fraction|None, text); on_segment(segment dict) as text arrives. cancel: threading.Event.
    Returns a result dict (see to_json).
    """
    from faster_whisper import BatchedInferencePipeline
    from faster_whisper.audio import decode_audio

    name = opts.get("model", "large-v3-turbo")
    task = opts.get("task", "transcribe")
    note = ""
    if task == "translate" and "turbo" in name:
        # large-v3-turbo was fine-tuned without translation data and mostly just transcribes.
        name, note = "large-v3", "turbo can't translate, used large-v3"
    model, device = get_model(name, on_progress)
    if cancel is not None and cancel.is_set():
        raise Cancelled()

    if on_progress:
        on_progress(None, "Reading audio …")
    log(f"  decoding {path}")
    audio = decode_audio(str(path), sampling_rate=16000)    # PyAV: pulls the audio track out of any video
    duration = len(audio) / 16000
    if duration < 0.1:
        raise RuntimeError("No audio track found (or it's silent).")

    language = opts.get("language") or None
    hints = (opts.get("hints") or "").strip()
    prompt = hints or None
    if not prompt and language == "zh":
        prompt = "以下是普通话的句子。"   # nudges Whisper to Simplified Chinese with punctuation
    kw = dict(language=language, task=task, beam_size=5, word_timestamps=opts.get("word_timestamps", True),
              vad_filter=True, initial_prompt=prompt, multilingual=bool(opts.get("multilingual")))
    def start(**kw):
        if device in ("cuda", "mps"):
            return BatchedInferencePipeline(model=model).transcribe(audio, batch_size=16, **kw)
        return model.transcribe(audio, vad_parameters={"min_silence_duration_ms": 500},
                                condition_on_previous_text=False, **kw)

    seg_iter, info = start(**kw)     # lazy: only language detection has run so far
    if task == "translate" and info.language == "en":
        # Already English: "translating" it drops punctuation and capitals, so transcribe instead.
        kw.update(task="transcribe", language="en")
        seg_iter, info = start(**kw)
        note = (note + "; " if note else "") + "already English"

    detected = info.language
    if on_progress:
        on_progress(0.0, f"Language: {lang_name(detected)} ({info.language_probability:.0%})")
    want_spk = bool(opts.get("speakers"))
    span = 0.85 if want_spk else 1.0        # share of the progress bar for the words (the rest: speakers)
    segments, t0 = [], time.time()
    for s in seg_iter:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        seg = {"id": len(segments), "start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
               "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                          "probability": round(w.probability, 3)} for w in (s.words or [])]}
        if not seg["text"]:
            continue
        for part in split_sentences(seg):
            part["id"] = len(segments)
            segments.append(part)
            if on_segment:
                on_segment(part)
        if on_progress:
            el = time.time() - t0
            frac = min(s.end / duration, 1.0) * span
            eta = el / frac - el if frac > 0.02 else None
            on_progress(frac, f"{fmt_hms(s.end)} / {fmt_hms(duration)}" + (f" · ~{fmt_hms(eta)} left" if eta else ""))
    speakers = []
    if want_spk and segments:
        from . import speakers as spk
        if not spk.available():
            note = (note + "; " if note else "") + "speaker detection unavailable"
        else:
            if on_progress:
                on_progress(span, "Finding speakers …")

            def sp_prog(f):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if on_progress:
                    on_progress(span + (1 - span) * f, f"Finding speakers … {f:.0%}")
            turns = spk.diarize(audio, opts.get("num_speakers") or None, sp_prog)
            segments, n = spk.assign(segments, turns)
            speakers = [f"Speaker {i + 1}" for i in range(n)]
            if n:
                note = (note + "; " if note else "") + f"{n} speaker{'s' if n != 1 else ''}"
    return {
        "file": str(path), "model": name, "device": device, "task": task, "note": note, "speakers": speakers,
        "language": detected if task == "transcribe" else "en", "spoken_language": detected,
        "language_probability": round(info.language_probability, 3), "duration": round(duration, 3),
        "segments": segments,
    }


_ENDS = tuple(".?!。？！…")
_SOFT = tuple(",，、;；:")


def split_sentences(seg):
    """Cut a long recognised chunk into sentences using its word timings, so every sentence gets its own
    timestamp and can be clicked to play. (The fast GPU mode returns chunks of up to 30 seconds.)"""
    words = seg.get("words") or []
    if len(words) < 2:
        return [seg]
    parts, cur = [], []
    for w in words:
        cur.append(w)
        t = w["word"].strip()
        if t.endswith(_ENDS) or (len(cur) >= 30 and t.endswith(_SOFT)):
            parts.append(cur)
            cur = []
    if cur:
        if parts and len(cur) <= 2 and not "".join(x["word"] for x in cur).strip().endswith(_ENDS):
            parts[-1].extend(cur)      # a dangling word or two belongs to the sentence before
        else:
            parts.append(cur)
    if len(parts) == 1:
        return [seg]
    out = []
    for p in parts:
        text = "".join(x["word"] for x in p).strip()
        if text:
            out.append({"id": 0, "start": p[0]["start"], "end": p[-1]["end"], "text": text, "words": p})
    return out or [seg]


# ------------------------------------------------------------------ output formats
def _ts(sec, sep=","):
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _joiner(result):
    return "" if result.get("language") in CJK else " "


def speaker_name(result, seg):
    """'Speaker 1' (or the name it was renamed to), or None when speakers weren't detected."""
    names = result.get("speakers") or []
    k = seg.get("speaker")
    if not names or k is None:
        return None
    return names[k] if k < len(names) else f"Speaker {k + 1}"


def paragraphs(result):
    """Group segments into readable paragraphs: a new one after a pause, when the speaker changes,
    or once a paragraph gets long."""
    paras, cur, last_end = [], [], None
    spk = bool(result.get("speakers"))
    untimed = result.get("no_times")          # pasted text: keep its own paragraphs
    for s in result["segments"]:
        long_para = sum(len(x["text"]) for x in cur) > 600 and cur and cur[-1]["text"][-1:] in ".?!。？！"
        turn = spk and cur and s.get("speaker") != cur[-1].get("speaker")
        gap = s.get("para") if untimed else (last_end is not None and s["start"] - last_end > 2.0)
        if cur and (gap or long_para or turn):
            paras.append(cur)
            cur = []
        cur.append(s)
        last_end = s["end"]
    if cur:
        paras.append(cur)
    return paras


def stamp(sec):
    return f"({fmt_hms(sec)})"


def labelled(result):
    """paragraphs() paired with the speaker label to show above each (None when the speaker didn't change)."""
    out, last = [], object()
    for para in paragraphs(result):
        name = speaker_name(result, para[0])
        out.append((name if name and para[0].get("speaker") != last else None, para))
        last = para[0].get("speaker")
    return out


def to_text(result, timestamps=False):
    """Readable paragraphs; with timestamps each sentence starts with (m:ss) like TurboScribe;
    with speakers each turn starts with 'Speaker 1:'."""
    timestamps = timestamps and not result.get("no_times")
    j = _joiner(result)
    out = []
    for name, para in labelled(result):
        if timestamps:
            body = " ".join(f"{stamp(s['start'])} {s['text']}" for s in para)
        else:
            body = j.join(s["text"] for s in para)
        out.append(f"{name}: {body}" if name else body)
    return "\n\n".join(out) + "\n"


def cues(result):
    """Subtitle cues of at most 2 lines, re-cut from word timestamps so they're readable."""
    cjk = result.get("language") in CJK
    line_max = 18 if cjk else 42
    max_chars, max_dur, max_gap = line_max * 2, 6.0, 0.8
    out = []

    who = [None]

    def flush(words):
        if words:
            text = "".join(w["word"] for w in words).strip()
            if text:
                out.append((words[0]["start"], words[-1]["end"], _wrap(text, line_max, cjk), who[0]))

    for seg in result["segments"]:
        who[0] = speaker_name(result, seg)
        words = seg.get("words") or []
        if not words:
            out.append((seg["start"], seg["end"], _wrap(seg["text"], line_max, cjk), who[0]))
            continue
        cur = []
        for w in words:
            if cur:
                text = "".join(x["word"] for x in cur + [w]).strip()
                if (len(text) > max_chars or w["end"] - cur[0]["start"] > max_dur
                        or w["start"] - cur[-1]["end"] > max_gap):
                    flush(cur)
                    cur = []
            cur.append(w)
            cur_text = "".join(x["word"] for x in cur).strip()
            if cur_text[-1:] in ".?!。？！" and len(cur_text) > max_chars * 0.4:
                flush(cur)
                cur = []
        flush(cur)
    # never let a cue overlap the next one
    fixed = []
    for i, (a, b, t, w) in enumerate(out):
        if i + 1 < len(out):
            b = min(b, out[i + 1][0])
        fixed.append((a, max(b, a + 0.3), t, w))
    return fixed


def _wrap(text, line_max, cjk):
    if len(text) <= line_max:
        return text
    if cjk:
        mid = len(text) // 2
        return text[:mid] + "\n" + text[mid:]
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    i = min(spaces, key=lambda k: abs(k - mid))
    return text[:i] + "\n" + text[i + 1:]


def to_srt(result):
    out, last = [], None
    for n, (a, b, t, w) in enumerate(cues(result), 1):
        if w and w != last:
            t = f"{w}: {t}"            # name the speaker when the voice changes
        last = w
        out.append(f"{n}\n{_ts(a)} --> {_ts(b)}\n{t}\n")
    return "\n".join(out)


def to_vtt(result):
    return "WEBVTT\n\n" + "\n".join(f"{_ts(a, '.')} --> {_ts(b, '.')}\n" + (f"<v {w}>" if w else "") + f"{t}\n"
                                     for a, b, t, w in cues(result))


def to_json(result):
    return json.dumps(result, ensure_ascii=False, indent=2)


def to_csv(result):
    buf = io.StringIO()
    w = csv.writer(buf)
    spk = bool(result.get("speakers"))
    w.writerow(["start", "end"] + (["speaker"] if spk else []) + ["text"])
    for s in result["segments"]:
        w.writerow([_ts(s["start"], "."), _ts(s["end"], ".")] + ([speaker_name(result, s)] if spk else []) +
                   [s["text"]])
    return buf.getvalue()


# Fonts that ship with the system, so PDFs show Chinese, Tamil, Thai … without bundling fonts.
# (file, index inside a .ttc collection). The first one found is the main font, the rest are fallbacks.
if os.name == "nt":
    _FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    _PDF_FONTS = [("segoeui.ttf", 0), ("msyh.ttc", 0), ("Nirmala.ttc", 0), ("malgun.ttf", 0), ("YuGothR.ttc", 0),
                  ("LeelawUI.ttf", 0), ("ebrima.ttf", 0), ("seguisym.ttf", 0)]
else:                                                    # macOS
    _FONT_DIR = Path("/System/Library/Fonts")
    _PDF_FONTS = [("Supplemental/Arial.ttf", 0), ("Supplemental/Arial Unicode.ttf", 0), ("STHeiti Medium.ttc", 0),
                  ("Supplemental/Songti.ttc", 0), ("Supplemental/Tamil Sangam MN.ttc", 0), ("Thonburi.ttc", 0),
                  ("AppleSDGothicNeo.ttc", 0), ("Apple Symbols.ttf", 0)]


def to_pdf(result, timestamps=False):
    """A clean, printable transcript (bytes)."""
    timestamps = timestamps and not result.get("no_times")
    from fpdf import FPDF
    import logging
    logging.getLogger("fontTools").setLevel(logging.ERROR)   # chatty about font subsetting
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, 18)
    names = []
    for i, (fname, idx) in enumerate(_PDF_FONTS):
        f = _FONT_DIR / fname
        if f.exists():
            fam = f"f{i}"
            try:
                pdf.add_font(fam, "", str(f), collection_font_number=idx)
                names.append(fam)
            except Exception as e:
                log(f"pdf font {fname} skipped: {e}")
    if names:
        main = names[0]
        if len(names) > 1:
            pdf.set_fallback_fonts(names[1:])
        try:
            pdf.set_text_shaping(True)        # joins Tamil / Hindi / Thai letters correctly
        except Exception:
            pass
    else:
        main = "helvetica"
    pdf.set_title(Path(result["file"]).stem)
    pdf.set_creator("dl_transcribe")
    pdf.add_page()
    target = result.get("target_name") or "English"
    title = Path(result["file"]).stem + (f" ({target})" if result["task"] == "translate" else "")
    pdf.set_font(main, size=18)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    meta = [] if result.get("no_times") else [fmt_hms(result.get("duration") or 0)]
    meta.append(result.get("source_name") or lang_name(result.get("spoken_language")))
    if result["task"] == "translate":
        meta[-1] += f" → {target}"
    meta.append(f"model {result.get('model', '')}")
    pdf.set_font(main, size=9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 6, "  ·  ".join(meta), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    j = _joiner(result)
    with pdf.text_columns(text_align="LEFT", line_height=1.45) as cols:
        for name, para in labelled(result):
            if name:
                with cols.paragraph(bottom_margin=0.5) as par:
                    pdf.set_font(main, size=9.5)
                    pdf.set_text_color(37, 99, 235)
                    par.write(name.upper())
            with cols.paragraph(bottom_margin=3.5) as par:
                for s in para:
                    if timestamps:
                        pdf.set_font(main, size=9)
                        pdf.set_text_color(135, 135, 135)
                        par.write(stamp(s["start"]) + " ")    # keep the stamp on the same line as its text
                    pdf.set_font(main, size=11)
                    pdf.set_text_color(25, 25, 25)
                    par.write(s["text"] + (" " if timestamps or j else ""))
    return bytes(pdf.output())


WRITERS = {"txt": to_text, "srt": to_srt, "vtt": to_vtt, "json": to_json, "csv": to_csv, "pdf": to_pdf}
TIMESTAMPED = {"txt", "pdf"}       # follow the "Show timestamps" switch


def default_outdir(src):
    """Default export folder: a "Transcripts" folder beside the original file."""
    return Path(src).parent / "Transcripts"


def save(result, formats, outdir=None, timestamps=False, stem=None):
    """Write the chosen formats into outdir (default: Transcripts folder beside the source).
    Re-exporting replaces the earlier export of the same file. Returns the paths written."""
    src = Path(result["file"])
    folder = Path(outdir) if outdir else default_outdir(src)
    folder.mkdir(parents=True, exist_ok=True)
    stem = stem or (src.stem + ("_en" if result["task"] == "translate" else ""))
    paths = []
    for fmt in formats:
        p = folder / f"{stem}.{fmt}"
        data = WRITERS[fmt](result, timestamps) if fmt in TIMESTAMPED else WRITERS[fmt](result)
        tmp = p.with_name(p.name + ".tmp")
        if isinstance(data, bytes):
            tmp.write_bytes(data)
        else:
            # utf-8-sig so Notepad/Excel show Chinese, Tamil etc. correctly
            enc = "utf-8-sig" if fmt in ("txt", "csv") else "utf-8"
            tmp.write_text(data, encoding=enc, newline="" if fmt == "csv" else None)
        try:
            os.replace(tmp, p)
        except PermissionError:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{p.name} is open in another program (close it and export again).")
        paths.append(p)
    return paths
