"""Build dl_transcribe for macOS (run on a Mac; GitHub Actions does this: .github/workflows/mac.yml).

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python build_mac.py          # -> dist/MacOS/dl_transcribe-mac-apple-silicon.dmg

Makes the .app, smoke-tests the built app (transcribes a two-voice test recording with speakers, converts audio
to MP3, plays it through the app's player, writes a PDF with several scripts, opens the window and takes a
screenshot, checks for updates), packs a .dmg, then runs the real updater against that .dmg.
"""

import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dltr.common import APP_NAME, APP_VERSION  # noqa: E402

ARCH = "apple-silicon" if platform.machine() == "arm64" else "intel"
DIST = ROOT / "dist" / "MacOS"
WORK = ROOT / "build-mac"
SHOTS = WORK / "screenshots"
APP = WORK / "dist" / f"{APP_NAME}.app"
ICNS = ROOT / "assets" / "icon.icns"
BUNDLE_ID = "com.opengrapes.dl-transcribe"
DMG_NAME = f"{APP_NAME}-mac-{ARCH}.dmg"
SUP = Path.home() / "Library" / "Application Support" / APP_NAME


def sh(*cmd, **kw):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def make_icns():
    if ICNS.exists():
        return
    from PIL import Image
    img = Image.open(ROOT / "assets" / "icon.png").convert("RGBA").resize((1024, 1024), Image.LANCZOS)
    img.save(ICNS, format="ICNS")


def pyinstaller():
    shutil.rmtree(WORK, ignore_errors=True)
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
       "--name", APP_NAME, "--icon", ICNS, "--osx-bundle-identifier", BUNDLE_ID,
       "--add-data", f"{ROOT / 'assets' / 'icon.png'}:assets",
       "--add-data", f"{ROOT / 'assets' / 'speakers'}:assets/speakers",
       "--collect-all", "tkinterdnd2", "--collect-all", "faster_whisper", "--collect-binaries", "ctranslate2",
       "--collect-submodules", "av", "--collect-all", "fpdf", "--collect-all", "uharfbuzz",
       "--collect-submodules", "fontTools", "--collect-all", "sherpa_onnx", "--collect-data", "certifi",
       "--collect-all", "langid", "--collect-all", "tokenizers",
       "--hidden-import", "AVFoundation", "--hidden-import", "Foundation", "--hidden-import", "objc",
       "--distpath", WORK / "dist", "--workpath", WORK / "work", "--specpath", WORK, ROOT / "run.py")


def fill_bundle():
    res = APP / "Contents" / "Resources"
    shutil.copy2(ROOT / "README.md", res / "README.md")
    plist_path = APP / "Contents" / "Info.plist"
    with open(plist_path, "rb") as fh:
        pl = plistlib.load(fh)
    pl.update({
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION, "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        # "Open With" and dropping files on the Dock icon.
        "CFBundleDocumentTypes": [
            {"CFBundleTypeName": "Audio", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
             "LSItemContentTypes": ["public.audio"]},
            {"CFBundleTypeName": "Movie", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
             "LSItemContentTypes": ["public.movie"]},
        ],
    })
    with open(plist_path, "wb") as fh:
        plistlib.dump(pl, fh)
    # Ad-hoc signature (required on Apple Silicon). Not notarized: first launch needs "Open Anyway".
    sh("codesign", "--force", "--deep", "--sign", "-", APP)
    sh("codesign", "--verify", "--deep", "--strict", APP)


DMG_README = f"""{APP_NAME} {APP_VERSION} for macOS (Apple Silicon: M1 or newer, macOS 14 Sonoma or newer)

INSTALL
1. Drag {APP_NAME} onto the Applications folder.
2. Open Applications and double-click {APP_NAME}.
   macOS says it can't verify the developer: click Done.
3. Apple menu > System Settings > Privacy & Security, scroll down to Security:
   "{APP_NAME}" was blocked... click Open Anyway, then Open Anyway again and enter your password.
   (Only needed once.)
4. The first time you press Start, the app offers to download a speech model (one time; "small" is a
   good start on a Mac). Models are stored for your Mac user in
   ~/Library/Application Support/{APP_NAME}

USE
- Drop audio or video files on the window, press Start.
- Click any sentence to play it; Edit transcript to fix words; Find (Cmd+F).
- Tick formats (PDF, TXT, SRT, VTT, JSON, CSV) and press Export.
- Translate tab: import a transcript (or paste text), pick the languages, press Translate.
- Summary tab: an AI summary (overview, key points, key moments) in the language you pick.
- Check for updates (top right) installs new versions.

Runs on the Mac's processor (there's no NVIDIA GPU on a Mac): "small" or "base" are quick, "large-v3-turbo"
is more accurate but slower.
"""


def make_dmg():
    DIST.mkdir(parents=True, exist_ok=True)
    stage = WORK / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    sh("ditto", APP, stage / APP.name)              # ditto keeps symlinks and signatures intact
    os.symlink("/Applications", stage / "Applications")
    (stage / "READ ME.txt").write_text(DMG_README)
    out = DIST / DMG_NAME
    out.unlink(missing_ok=True)
    sh("hdiutil", "create", "-volname", APP_NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", out)
    print(f"\nBuilt {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


# ---------------------------------------------------------------- smoke tests on the built app

LINES = [("Samantha", "Good morning everyone. Today we will talk about the weather forecast for the weekend."),
         ("Daniel", "Thank you. It will be sunny on Saturday, with some rain on Sunday afternoon."),
         ("Samantha", "Great. Should people bring an umbrella to the football match?"),
         ("Daniel", "Yes, I would bring one. The rain should stop by the evening.")]


def speech_sample(t):
    """A two-voice recording made with macOS text-to-speech (16 kHz mono WAV)."""
    parts = []
    for i, (voice, text) in enumerate(LINES):
        f = t / f"part{i}.wav"
        sh("say", "-v", voice, "-o", f, "--file-format=WAVE", "--data-format=LEI16@16000", text)
        parts.append(f)
    out = t / "dialog.wav"
    silence = b"\x00\x00" * (16000 // 2)            # half a second between speakers
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        for f in parts:
            with wave.open(str(f), "rb") as r:
                assert r.getframerate() == 16000 and r.getnchannels() == 1, "unexpected TTS format"
                w.writeframes(r.readframes(r.getnframes()))
            w.writeframes(silence)
    return out


def screenshot(name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", str(SHOTS / name)], check=False)


def window_test(binary, f, wait, name):
    p = subprocess.Popen([str(binary), str(f)])
    time.sleep(wait)
    alive = p.poll() is None
    if alive:
        subprocess.run(["osascript", "-e", f'tell application "System Events" to set frontmost of '
                                           f'(first process whose unix id is {p.pid}) to true'], check=False)
        time.sleep(2)
        screenshot(name)
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
    assert alive, f"the window closed by itself ({name})"


def smoke_test():
    binary = APP / "Contents" / "MacOS" / APP_NAME
    t = WORK / "smoke"
    shutil.rmtree(t, ignore_errors=True)
    t.mkdir(parents=True)
    wav = speech_sample(t)
    out = t / "out"

    # 1. Transcribe with speakers (downloads the tiny model from inside the app: proves certificates work).
    p = subprocess.run([str(binary), "--cli", "--model", "tiny", "--speakers=2", "--formats", "txt,srt,pdf,json",
                        "--outdir", str(out), str(wav)], capture_output=True, text=True, timeout=1800)
    print(p.stdout[-3000:], p.stderr[-3000:], flush=True)
    assert p.returncode == 0, f"--cli failed ({p.returncode})"
    txt = (out / "dialog.txt").read_text(encoding="utf-8")
    print("TRANSCRIPT:\n" + txt, flush=True)
    res = json.loads((out / "dialog.json").read_text(encoding="utf-8"))
    assert "weather" in txt.lower() or "sunny" in txt.lower(), "the transcript doesn't contain the spoken words"
    assert len(res.get("speakers") or []) >= 2, f"expected 2 speakers, got {res.get('speakers')}"
    print("Transcribed on:", res.get("device"), flush=True)
    for ext in ("srt", "pdf"):
        assert (out / f"dialog.{ext}").stat().st_size > 100, f"dialog.{ext} is empty"

    # 2. MP3 conversion, the app's audio player (AVFoundation) and a PDF with Chinese / Tamil / Thai.
    sj = t / "selftest.json"
    subprocess.run([str(binary), "--selftest-media", str(wav), str(sj)], timeout=300)
    st = json.loads(sj.read_text())
    print("selftest:", st, flush=True)
    assert "error" not in st, st.get("error")
    assert st["mp3_bytes"] > 10000 and st["length"] > 5, "MP3 conversion / opening failed"
    assert st["pdf_bytes"] > 10000, "PDF too small"
    if not st["playing"]:
        print("NOTE: the player didn't report playing (this build machine may have no audio device)", flush=True)

    # 2b. Translation (NLLB-200 + the Qwen3 AI model) and an AI summary, on the Mac's processor.
    aj = t / "ai.json"
    subprocess.run([str(binary), "--selftest-ai", str(aj)], timeout=2400)
    aires = json.loads(aj.read_text(encoding="utf-8"))
    print("AI selftest:", json.dumps(aires, ensure_ascii=False, indent=1), flush=True)
    assert "error" not in aires, aires.get("error")
    assert all(aires["nllb"]) and aires["ai"][0] and len(aires["summary"]) > 80, "translation / summary empty"

    # 3. Check for updates reaches GitHub from inside the app (certificates, network).
    cu = t / "update.json"
    subprocess.run([str(binary), "--check-update", str(cu)], timeout=120)
    up = json.loads(cu.read_text())
    print("Check for updates:", up, flush=True)
    assert "error" not in up and up.get("version"), f"update check failed: {up}"

    # 4. The window opens with a file queued and stays up.
    window_test(binary, wav, 30, "1_window.png")

    log = (SUP / f"{APP_NAME}.log").read_text() if (SUP / f"{APP_NAME}.log").exists() else ""
    assert "CRASH" not in log, "the app logged a crash:\n" + log[-3000:]
    crash = SUP / "crash.log"
    if crash.exists() and crash.read_text().strip():
        print("crash.log:\n" + crash.read_text(), flush=True)
    print("\nSMOKE TEST PASSED", flush=True)


def update_install_test(dmg):
    """Run the real updater against the new .dmg: replace a copy of the app while a process 'is' the app."""
    import threading
    from dltr import updater
    t = WORK / "update-test"
    shutil.rmtree(t, ignore_errors=True)
    t.mkdir(parents=True)
    old = t / f"{APP_NAME}.app"
    sh("ditto", APP, old)
    (old / "Contents" / "Resources" / "OLD_MARKER").write_text("old copy")
    pkg = t / dmg.name
    shutil.copy2(dmg, pkg)                     # the helper deletes the package it installed
    dummy = subprocess.Popen(["sleep", "3"])
    threading.Thread(target=dummy.wait, daemon=True).start()     # reap it, as a real app that quits disappears
    cmd, work = updater.prepare_install(pkg, dummy.pid, launch=False, loc=old)
    updater.start_helper(cmd)
    for _ in range(240):
        if (work / "finished.txt").exists():
            break
        time.sleep(0.5)
    assert (work / "finished.txt").exists(), "the update helper didn't finish"
    assert not (old / "Contents" / "Resources" / "OLD_MARKER").exists(), "the app wasn't replaced"
    assert (old / "Contents" / "MacOS" / APP_NAME).exists(), "the replaced app has no program"
    sh("codesign", "--verify", "--deep", "--strict", old)
    print("UPDATE INSTALL TEST PASSED", flush=True)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("build_mac.py must run on macOS (GitHub Actions builds it: .github/workflows/mac.yml)")
    make_icns()
    pyinstaller()
    fill_bundle()
    smoke_test()
    update_install_test(make_dmg())
