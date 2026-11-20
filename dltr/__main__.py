"""Entry point.

  dl_transcribe.exe                     open the window
  dl_transcribe.exe FILE|FOLDER ...     open the window and transcribe these (drag onto the .exe)

Command-line mode (python -m dltr --cli ...):
  --cli [--model NAME] [--lang CODE] [--translate] [--mixed] [--speakers | --speakers=N] [--formats pdf,txt,srt,vtt,json,csv] [--outdir DIR] FILE ...
"""

import sys


def _print(*a):
    try:
        print(*a, flush=True)
    except (AttributeError, OSError):  # no console (windowed build)
        pass


def cli(args):
    from . import engine
    from .common import find_media, lang_name
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    opts = {"model": "large-v3-turbo", "language": None, "task": "transcribe"}
    formats, outdir, files = ["txt", "srt"], None, []
    it = iter(args)
    for a in it:
        if a == "--model":
            opts["model"] = next(it)
        elif a == "--lang":
            opts["language"] = next(it)
        elif a == "--translate":
            opts["task"] = "translate"
        elif a == "--mixed":
            opts["multilingual"] = True
        elif a == "--speakers":
            opts["speakers"] = True
        elif a.startswith("--speakers="):
            opts["speakers"], opts["num_speakers"] = True, int(a.split("=", 1)[1])
        elif a == "--formats":
            formats = next(it).split(",")
        elif a == "--outdir":
            outdir = next(it)
        else:
            files.append(a)
    rc = 0
    for f in find_media(files):
        try:
            r = engine.transcribe(f, opts, on_progress=lambda frac, t: _print(f"  {t}"))
            paths = engine.save(r, formats, outdir)
            extra = f", {len(r['speakers'])} speakers" if r.get("speakers") else ""
            _print(f"OK   {f}  [{lang_name(r['spoken_language'])}, {r['model']} on {r['device']}{extra}]")
            for p in paths:
                _print(f"     -> {p}")
        except Exception as e:
            rc = 1
            _print(f"FAIL {f}: {e}")
    return rc


def check_update(out):
    """--check-update OUT.json: what Check for updates sees (used by the Mac build test)."""
    import json
    from . import updater
    try:
        info = updater.latest()
        res = {"version": info["version"], "asset": (info.get("asset") or {}).get("name"),
               "newer": updater.is_newer(info["version"])}
    except Exception as e:
        res = {"error": str(e)}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    return 0 if "error" not in res else 1


def selftest_media(src, out):
    """--selftest-media AUDIO OUT.json: MP3 conversion, playback through the app's player, and a PDF with
    several scripts (used by the Mac build test; works on Windows too)."""
    import json
    import tempfile
    import time
    from pathlib import Path
    from . import engine, media
    res = {}
    tmp = Path(tempfile.mkdtemp())
    try:
        mp3 = media.to_mp3(src, tmp / "converted.mp3")
        res["mp3_bytes"] = mp3.stat().st_size
        p = media.Player()
        p.open(mp3)
        res["length"] = round(p.length, 2)
        p.set_volume(0.3)
        p.play(0.5)
        time.sleep(1.5)
        res["playing"] = p.playing()
        res["position"] = round(p.position(), 2)
        p.pause()
        p.seek(2.0)
        res["after_seek"] = round(p.position(), 2)
        p.close()
        fake = {"file": "selftest.wav", "task": "transcribe", "duration": 12.0, "spoken_language": "en",
                "language": "en", "model": "tiny", "speakers": ["Speaker 1", "Speaker 2"],
                "segments": [{"id": 0, "start": 0.0, "end": 2.0, "text": "Hello, this is a test.", "speaker": 0},
                             {"id": 1, "start": 2.0, "end": 4.0, "text": "你好，这是一个测试。", "speaker": 1},
                             {"id": 2, "start": 4.0, "end": 6.0, "text": "வணக்கம், இது ஒரு சோதனை.", "speaker": 0},
                             {"id": 3, "start": 6.0, "end": 8.0, "text": "สวัสดี นี่คือการทดสอบ", "speaker": 1}]}
        pdf = engine.to_pdf(fake, timestamps=True)
        (Path(out).parent / "selftest.pdf").write_bytes(pdf)
        res["pdf_bytes"] = len(pdf)
    except Exception as e:
        import traceback
        res["error"] = f"{e}\n{traceback.format_exc()}"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    return 0 if "error" not in res else 1


def selftest_ai(out, keys="nllb-1.3b,qwen3-4b"):
    """--selftest-ai OUT.json: download the translation/AI models if needed, translate a few sentences with both
    translators and write a short summary (used by the Mac build test)."""
    import json
    import time
    from . import ai, engine, models
    res = {}
    try:
        for k in keys.split(","):
            if not models.installed(k):
                t = time.time()
                engine.download(k)
                res[f"download_{k}"] = round(time.time() - t, 1)
        t = time.time()
        r = ai.translate(["Good morning everyone, the meeting starts at ten.", "我哋聽日去邊度食飯呀？"], None,
                         "zho_Hans", "nllb")
        res["nllb"] = r["texts"]
        r = ai.translate(["我哋聽日去邊度食飯呀？"], None, "eng_Latn", "nllb")
        res["nllb_to_en"] = r["texts"]
        res["nllb_s"] = round(time.time() - t, 1)
        t = time.time()
        r = ai.translate(["Good morning everyone, the meeting starts at ten."], None, "jpn_Jpan", "ai")
        res["ai"] = r["texts"]
        res["ai_s"] = round(time.time() - t, 1)
        t = time.time()
        res["summary"] = ai.summarize(
            "Anna: Sales were 15 percent above forecast this quarter, mostly from the new subscription plan.\n"
            "Ben: Great. Then let's double the marketing budget before the launch in March.\n"
            "Anna: Agreed, but keep some money for customer support. Ben will send the new budget by Friday.",
            "eng_Latn", "short", times=False)
        res["summary_s"] = round(time.time() - t, 1)
        res["device"] = ai.used_device()
    except Exception as e:
        import traceback
        res["error"] = f"{e}\n{traceback.format_exc()}"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return 0 if "error" not in res else 1


def wifi(seconds="600", out=None):
    """--wifi [SECONDS] [OUT.json]: run Share via Wi-Fi without the window for a while (testing)."""
    import json
    import time
    from .common import load_settings
    from .web import Sharing
    s = Sharing.start(load_settings())
    time.sleep(4)                       # let the .local name come up
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"urls": s.urls, "mdns": s.mdns}, fh)
    _print("Sharing on:", " ".join(s.urls))
    try:
        time.sleep(float(seconds))
    finally:
        s.stop()
    return 0


def selftest_chat(src, out):
    """--selftest-chat AUDIO OUT.json: the microphone library, voice-to-text (dictation) and, when the AI model
    is on this computer, a chat answer with the speakers named. Used to check a built app."""
    import json
    import time
    res = {}
    try:
        import sounddevice as sd
        res["mic_devices"] = sum(1 for d in sd.query_devices() if d.get("max_input_channels", 0) > 0)
        from faster_whisper.audio import decode_audio
        from . import engine, models
        audio = decode_audio(src, sampling_rate=16000)
        name = next((m for m in ("large-v3-turbo", "tiny", "base", "small") if engine.is_downloaded(m)), "tiny")
        t = time.time()
        res["dictated"] = engine.dictate(audio[:16000 * 8], name)
        res["dictate_s"] = round(time.time() - t, 1)
        from . import ai
        if models.installed(ai.AI_KEY):
            r = engine.transcribe(src, {"model": name, "speakers": True})
            ctx = {"title": "selftest", "speakers": [{"name": n, "voice": engine.voice_of(r, i)}
                                                     for i, n in enumerate(r["speakers"])],
                   "lines": [[s["start"], engine.speaker_name(r, s) or "", s["text"]] for s in r["segments"]]}
            res["names"] = ai.find_names(ctx)
            t = time.time()
            res["answer"] = ai.chat(ctx, [], "At which point is a woman speaking?")
            res["answer_s"] = round(time.time() - t, 1)
            res["suggestions"] = ai.suggestions(ctx, [])
    except Exception as e:
        import traceback
        res["error"] = f"{e!r}\n{traceback.format_exc()}"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return 0 if "error" not in res else 1


def selftest_voice(out, langs_="zho_Hans,zsm_Latn"):
    """--selftest-voice OUT.json [LANGS]: pinyin, and reading a sentence aloud in each language (downloads the
    voices if needed). Used to check a built app."""
    import json
    import tempfile
    import time
    from pathlib import Path
    from . import pinyin, tts
    res = {}
    try:
        res["pinyin"] = pinyin.line("保护自己，远离Ah Choo。现在就接种流感疫苗。")
        say = {"zho_Hans": ["你可能很注重卫生，", "现在就接种流感疫苗。"], "eng_Latn": ["Book your flu vaccination now."],
               "zsm_Latn": ["Tempah vaksinasi flu anda sekarang."], "tam_Taml": ["வணக்கம், இது ஒரு சோதனை."]}
        tmp = Path(tempfile.mkdtemp())
        for code in langs_.split(","):
            t = time.time()
            vid = None
            if ":" in code:                       # LANG:VOICE_ID picks a voice
                code, vid = code.split(":", 1)
            info = tts.synthesize(say.get(code, ["Hello."]), code, voice=vid, out=tmp / f"{code}.wav")
            res[code] = {"times": info["times"], "duration": info["duration"], "voice": info["voice"],
                         "secs": round(time.time() - t, 1), "bytes": (tmp / f"{code}.wav").stat().st_size,
                         "name": info["voice_name"]}
    except Exception as e:
        import traceback
        res["error"] = f"{e!r}\n{traceback.format_exc()}"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return 0 if "error" not in res else 1


def main():
    args = sys.argv[1:]
    if args[:1] == ["--selftest-voice"] and len(args) in (2, 3):
        return selftest_voice(*args[1:])
    if args[:1] == ["--wifi"]:
        return wifi(*args[1:3])
    if args[:1] == ["--selftest-ai"] and len(args) in (2, 3):
        return selftest_ai(*args[1:])
    if args[:1] == ["--cli"]:
        return cli(args[1:])
    if args[:1] == ["--check-update"] and len(args) == 2:
        return check_update(args[1])
    if args[:1] == ["--selftest-media"] and len(args) == 3:
        return selftest_media(args[1], args[2])
    if args[:1] == ["--selftest-chat"] and len(args) == 3:
        return selftest_chat(args[1], args[2])
    import os
    from .gui import App
    # --selftest-restart OUT: open the window, press "Restart now" (as the Theme window does), and the reopened
    # copy writes OUT (with its version and theme) and closes. Checks that a restart really comes back.
    if args[:1] == ["--selftest-restart"] and len(args) == 2:
        os.environ["DLTR_RESTART_MARK"] = args[1]
        app = App(files=[])
        app.after(3000, app.restart)
        app.mainloop()
        return 0
    mark = os.environ.pop("DLTR_RESTART_MARK", None)
    app = App(files=args)
    if mark:
        def done():
            from . import theme
            from .common import APP_VERSION
            with open(mark, "w", encoding="utf-8") as fh:
                fh.write(f"restarted ok: {APP_VERSION} {theme.STYLE} pid {os.getpid()}")
            app._close()
        app.after(3000, done)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
