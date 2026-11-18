"""Who spoke when (speaker diarization), fully offline with sherpa-onnx:
pyannote segmentation-3.0 (MIT) finds speech turns, a 3D-Speaker ERes2Net (Apache-2.0) voice print tells voices
apart, then the turns are clustered into speakers. The two models (~45 MB) ship with the app in assets/speakers."""

import bisect
import os

from .common import asset, log

MODEL_DIR = asset("speakers")


def available():
    import importlib.util
    try:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return False
    except Exception:
        return False
    return (MODEL_DIR / "segmentation.onnx").exists() and (MODEL_DIR / "embedding.onnx").exists()


def diarize(audio, num_speakers=None, progress=None, sample_rate=16000):
    """audio: float32 mono 16 kHz. Returns [(start, end, speaker index)], speakers numbered from 0."""
    import sherpa_onnx
    threads = max(1, min(8, (os.cpu_count() or 4) // 2))
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(MODEL_DIR / "segmentation.onnx")),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL_DIR / "embedding.onnx"),
                                                             num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(num_speakers) if num_speakers else -1,
                                                    threshold=0.8),     # tuned: 0.6 split one narrator into 5 voices
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("Speaker models are missing or broken.")
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    if sd.sample_rate != sample_rate:
        raise RuntimeError(f"Speaker model expects {sd.sample_rate} Hz audio.")

    def cb(done, total):
        if progress and total:
            progress(done / total)
        return 0
    res = sd.process(audio, callback=cb).sort_by_start_time()
    turns = [(round(t.start, 3), round(t.end, 3), int(t.speaker)) for t in res]
    log(f"  speakers: {len({t[2] for t in turns})} found in {len(turns)} turns")
    return turns


def _speaker_at(turns, starts, a, b):
    """The speaker who talks most between a and b (or the nearest turn if nobody does)."""
    if not turns:
        return None
    best, best_ov = None, 0.0
    i = max(0, bisect.bisect_right(starts, a) - 2)
    while i < len(turns) and turns[i][0] < b:
        ov = min(b, turns[i][1]) - max(a, turns[i][0])
        if ov > best_ov:
            best, best_ov = turns[i][2], ov
        i += 1
    if best is not None:
        return best
    mid = (a + b) / 2
    return min(turns, key=lambda t: 0 if t[0] <= mid <= t[1] else min(abs(t[0] - mid), abs(t[1] - mid)))[2]


_gender = None


def _gender_model():
    global _gender
    if _gender is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 4) // 2))
        s = ort.InferenceSession(str(MODEL_DIR / "gender.onnx"), opts, providers=["CPUExecutionProvider"])
        _gender = (s, s.get_inputs()[0].name)
    return _gender


def voices(audio, segments, n, sample_rate=16000):
    """Male / female voice for each speaker, from an ECAPA-TDNN classifier (MIT, ~16 MB, assets/speakers).
    Up to ~40 s of each speaker's longest sentences are scored and averaged. Returns one entry per speaker:
    {"gender": "female" | "male" | None (not sure), "p": P(female)}."""
    import numpy as np
    if not n or not (MODEL_DIR / "gender.onnx").exists():
        return []
    try:
        sess, inp = _gender_model()
    except Exception as e:
        log(f"  voice model unavailable: {e!r}")
        return []
    out = []
    for k in range(n):
        mine = sorted((s for s in segments if s.get("speaker") == k), key=lambda s: s["end"] - s["start"],
                      reverse=True)
        total, ps, ws = 0.0, [], []
        for s in mine:
            a, b = s["start"], min(s["end"], s["start"] + 6.0)
            if b - a < 1.0:
                continue
            clip = audio[int(a * sample_rate):int(b * sample_rate)].astype(np.float32)
            if len(clip) < sample_rate:
                continue
            try:
                logits = sess.run(None, {inp: clip[None, :]})[0][0]
            except Exception as e:
                log(f"  voice check failed: {e!r}")
                break
            e_ = np.exp(logits - logits.max())
            ps.append(float(e_[1] / e_.sum()))       # {0: male, 1: female}
            ws.append(b - a)
            total += b - a
            if total >= 40:
                break
        if not ps:
            out.append({"gender": None, "p": None})
            continue
        p = float(np.average(ps, weights=ws))
        out.append({"gender": "female" if p >= 0.7 else "male" if p <= 0.3 else None, "p": round(p, 3)})
    log(f"  voices: {[v['gender'] or '?' for v in out]}")
    return out


def assign(segments, turns):
    """Give every segment a "speaker" (0, 1, …). A sentence where the voice changes is split at the change.
    Speakers are renumbered in order of first appearance. Returns (segments, number of speakers)."""
    if not turns:
        return segments, 0
    starts = [t[0] for t in turns]
    out = []
    for seg in segments:
        words = seg.get("words") or []
        if len(words) < 4:
            out.append(dict(seg, speaker=_speaker_at(turns, starts, seg["start"], seg["end"])))
            continue
        spk = [_speaker_at(turns, starts, w["start"], w["end"]) for w in words]
        # ignore blips: a run shorter than 3 words takes the speaker around it
        runs = []
        for k, s in enumerate(spk):
            if runs and runs[-1][0] == s:
                runs[-1][2] = k + 1
            else:
                runs.append([s, k, k + 1])
        merged = []
        for r in runs:
            if merged and (r[2] - r[1] < 3 or merged[-1][0] == r[0]):
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        if len(merged) > 1 and merged[0][2] - merged[0][1] < 3:
            merged[1][1] = 0
            merged.pop(0)
        for s, a, b in merged:
            part = words[a:b]
            text = "".join(w["word"] for w in part).strip()
            if text:
                out.append({"id": 0, "start": part[0]["start"], "end": part[-1]["end"], "text": text,
                            "words": part, "speaker": s})
    order = {}
    for s in out:
        if s["speaker"] is not None and s["speaker"] not in order:
            order[s["speaker"]] = len(order)
    for i, s in enumerate(out):
        s["id"] = i
        s["speaker"] = order.get(s["speaker"], 0)
    return out, len(order)
