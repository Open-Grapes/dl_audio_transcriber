"""Audio playback (Windows MCI, built in) and MP3 conversion (PyAV) for the transcript player."""

import ctypes
import hashlib
import os
import shutil
import sys
import threading
from pathlib import Path

from .common import SUP

CACHE = SUP / "cache"
# Played as they are; everything else (videos, other audio) gets an MP3 copy first.
DIRECT = {".mp3", ".wav", ".m4a", ".aac", ".aif", ".aiff", ".caf"} if sys.platform == "darwin" else {".mp3", ".wav"}


def to_mp3(src, dst, progress=None, cancel=None):
    """Convert the audio track of any audio/video file to MP3 (128 kbps). progress(fraction)."""
    import av
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    with av.open(str(src)) as inp:
        a = next((s for s in inp.streams if s.type == "audio"), None)
        if a is None:
            raise RuntimeError("No audio track found.")
        total = (inp.duration / 1e6) if inp.duration else None
        rate = a.codec_context.sample_rate or 44100
        rate = rate if rate in (32000, 44100, 48000, 22050, 24000, 16000) else 44100
        layout = "mono" if (a.codec_context.channels or 2) == 1 else "stereo"
        with av.open(str(tmp), "w", format="mp3") as out:
            o = out.add_stream("libmp3lame", rate=rate)
            o.layout = layout
            o.bit_rate = 128_000
            rs = av.AudioResampler(format="s16p", layout=layout, rate=rate)
            n = 0
            for frame in inp.decode(a):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("cancelled")
                t = frame.time
                frame.pts = None
                for f in rs.resample(frame):
                    for p in o.encode(f):
                        out.mux(p)
                n += 1
                if progress and total and n % 200 == 0 and t is not None:
                    progress(min(t / total, 0.99))
            for f in rs.resample(None):
                for p in o.encode(f):
                    out.mux(p)
            for p in o.encode(None):
                out.mux(p)
    os.replace(tmp, dst)
    return dst


def cached_mp3(src):
    """Where the MP3 copy of src is kept (it may not exist yet)."""
    src = Path(src)
    st = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    return CACHE / f"{key}.mp3"


def playable(src, progress=None, cancel=None):
    """A path Windows can play for src: the file itself, or a cached MP3 copy (made once)."""
    src = Path(src)
    if src.suffix.lower() in DIRECT:
        return src
    CACHE.mkdir(parents=True, exist_ok=True)
    dst = cached_mp3(src)
    if not dst.exists():
        to_mp3(src, dst, progress, cancel)
    return dst


def clean_cache(keep_mb=2000):
    """Delete the oldest cached MP3s once the cache grows past keep_mb."""
    try:
        files = sorted(CACHE.glob("*.mp3"), key=lambda p: p.stat().st_atime, reverse=True)
        total = 0
        for p in files:
            total += p.stat().st_size
            if total > keep_mb * 1e6:
                p.unlink(missing_ok=True)
        for p in CACHE.glob("*.part"):
            p.unlink(missing_ok=True)
    except OSError:
        pass


class _AVPlayer:
    """macOS: plays one file with AVFoundation's AVAudioPlayer (built into macOS, through PyObjC).
    Same interface as the Windows player below. All calls must come from the UI thread."""

    def __init__(self):
        self.p = None
        self.length = 0.0
        self.volume = 0.8
        self.alias = None              # kept for the same interface: "a file is open"

    def open(self, path):
        self.close()
        from AVFoundation import AVAudioPlayer
        from Foundation import NSURL
        url = NSURL.fileURLWithPath_(str(path))
        res = AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)
        p, err = res if isinstance(res, tuple) else (res, None)
        if p is None:
            raise RuntimeError(str(err.localizedDescription()) if err is not None else "Can't open the audio.")
        p.prepareToPlay()
        self.p, self.alias = p, str(path)
        self.length = float(p.duration() or 0.0)
        self.set_volume(self.volume)

    def close(self):
        if self.p is not None:
            try:
                self.p.stop()
            except Exception:
                pass
        self.p, self.alias, self.length = None, None, 0.0

    def play(self, at=None):
        if self.p is None:
            return
        if at is not None:
            self.p.setCurrentTime_(max(0.0, min(float(at), self.length)))
        self.p.play()

    def pause(self):
        if self.p is not None:
            self.p.pause()

    def seek(self, at):
        if self.p is not None:
            self.p.setCurrentTime_(max(0.0, min(float(at), self.length)))

    def position(self):
        return float(self.p.currentTime()) if self.p is not None else 0.0

    def playing(self):
        return bool(self.p is not None and self.p.isPlaying())

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        if self.p is not None:
            self.p.setVolume_(self.volume)


class _MciPlayer:
    """Windows: plays one file through Windows MCI. All calls must come from the UI thread."""
    _n = 0

    def __init__(self):
        self.alias = None
        self.length = 0.0
        self.volume = 0.8


    @staticmethod
    def _mci(cmd):
        buf = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
        if err:
            eb = ctypes.create_unicode_buffer(256)
            ctypes.windll.winmm.mciGetErrorStringW(err, eb, 255)
            raise RuntimeError(eb.value or f"MCI error {err}")
        return buf.value

    @staticmethod
    def _short(path):
        """MCI can't take long paths (deep project folders): use the 8.3 short name, or a short-named copy of the
        file in the app's cache when the drive has no short names."""
        path = str(path)
        if len(path) < 120:
            return path
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
            if 0 < n < 1024 and len(buf.value) < 120 and buf.value != path:
                return buf.value
        except Exception:
            pass
        src = Path(path)
        CACHE.mkdir(parents=True, exist_ok=True)
        st = src.stat()
        key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
        dst = CACHE / f"{key}{src.suffix.lower()}"
        if not dst.exists():
            try:
                os.link(src, dst)                       # same drive: instant, no extra space
            except OSError:
                shutil.copyfile(src, dst)
        return str(dst)

    def open(self, path):
        self.close()
        _MciPlayer._n += 1
        alias = f"dltr{_MciPlayer._n}"
        self._mci(f'open "{self._short(path)}" type mpegvideo alias {alias}')
        self.alias = alias
        self._mci(f"set {alias} time format milliseconds")
        try:
            self.length = int(self._mci(f"status {alias} length")) / 1000
        except (ValueError, RuntimeError):
            self.length = 0.0
        self.set_volume(self.volume)

    def close(self):
        if self.alias:
            try:
                self._mci(f"close {self.alias}")
            except RuntimeError:
                pass
        self.alias, self.length = None, 0.0

    def play(self, at=None):
        if not self.alias:
            return
        if at is not None:
            self._mci(f"play {self.alias} from {int(max(0, at) * 1000)}")
        else:
            self._mci(f"play {self.alias}")

    def pause(self):
        if self.alias:
            self._mci(f"pause {self.alias}")

    def seek(self, at):
        """Jump to `at` seconds, keeping the current play/pause state."""
        if not self.alias:
            return
        if self.playing():
            self.play(at)
        else:
            self._mci(f"seek {self.alias} to {int(max(0, at) * 1000)}")

    def position(self):
        if not self.alias:
            return 0.0
        try:
            return int(self._mci(f"status {self.alias} position")) / 1000
        except (ValueError, RuntimeError):
            return 0.0

    def playing(self):
        if not self.alias:
            return False
        try:
            return self._mci(f"status {self.alias} mode") == "playing"
        except RuntimeError:
            return False

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        if self.alias:
            try:
                self._mci(f"setaudio {self.alias} volume to {int(self.volume * 1000)}")
            except RuntimeError:
                pass


Player = _AVPlayer if sys.platform == "darwin" else _MciPlayer


def run_bg(fn, *args, done=None, fail=None):
    """Run fn in a thread; done(result) / fail(exc) are called from that thread."""
    def go():
        try:
            r = fn(*args)
        except Exception as e:
            if fail:
                fail(e)
            return
        if done:
            done(r)
    threading.Thread(target=go, daemon=True).start()
