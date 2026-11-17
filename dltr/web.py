"""Share via Wi-Fi: phones and laptops on the same network open http://192.168.x.x (or http://dl-transcribe.local)
and use the app in their browser. They upload audio/video; this PC (its GPU) transcribes, translates and
summarises it; the page plays the recording, shows the transcript and offers the downloads.

The server runs inside the window app (started and stopped from its "Share via Wi-Fi" window) with its own
worker process, so phone jobs don't disturb what you do in the window. Like dl_pdf_tools: no password, but the
Host header is checked (other websites can't reach it), and uploads are deleted when sharing stops.
"""

import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, langs, media, models
from .common import APP_NAME, APP_VERSION, FORMATS, LANGUAGES, MODELS, SUP, asset, lang_name, log
from .worker import Worker

WORK = SUP / "wifi"
PORTS = (80, 8776, 0)            # port 80 gives short addresses like http://192.168.0.20
MDNS_HOST = "dl-transcribe"      # http://dl-transcribe.local
MAX_UPLOAD = 8 * 1024 ** 3
BROWSER_OK = {".mp3", ".m4a", ".aac", ".wav", ".mp4", ".m4v"}     # play as they are in every browser
MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".mp4": "video/mp4",
        ".m4v": "video/mp4"}


def lan_ip():
    """This PC's address on the local network (the adapter with the default route), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))      # picks the route; nothing is sent
            ip = s.getsockname()[0]
        return None if ip.startswith(("127.", "0.")) else ip
    except OSError:
        return None


def _safe_name(name):
    name = Path(name.replace("\\", "/")).name
    name = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip(" .")
    return name or "recording"


class BadRequest(Exception):
    pass


# ============================================================================ jobs
class Jobs:
    """Uploaded files and their transcripts/translations/summaries. One worker process does the work, in order."""

    def __init__(self, app_settings):
        self.lock = threading.Lock()
        self.items = {}            # id -> job dict
        self.order = []            # ids in upload order
        self.queue = []            # (job id, step) waiting for the worker
        self.current = None        # (job id, step) the worker is doing
        self.has_gpu = False
        self.device = "checking …"
        self.settings = app_settings
        self.wk = Worker(self._event)
        self.wk.ensure()

    def close(self):
        try:
            self.wk.kill()
        except Exception:
            pass

    # ---- queue
    def _kick(self):
        """Give the worker the next step (called with the lock held)."""
        if self.current or not self.queue:
            return
        jid, step = self.queue.pop(0)
        j = self.items.get(jid)
        if not j:
            return self._kick()
        self.current = (jid, step)
        kind = step["kind"]
        if kind == "transcribe":
            missing = [] if models.installed(step["opts"]["model"]) else [step["opts"]["model"]]
            if missing:
                j.update(state="downloading", stage="Downloading the speech model (one time) …")
                self.queue.insert(0, (jid, step))
                self.current = (jid, {"kind": "download"})
                self.wk.submit({"kind": "download", "id": f"dl:{jid}", "models": missing})
                return
            j.update(state="working", stage="Starting …", progress=0.0, live=[])
            self.wk.submit({"kind": "transcribe", "id": f"tr:{jid}", "file": j["path"], "opts": step["opts"]})
        elif kind == "translate":
            from . import ai
            texts = [s["text"] for s in j["result"]["segments"]]
            eng, key, _ = ai.plan(texts, None, step["tgt"], step.get("engine", "auto"), self.has_gpu, j.get("hint"))
            if not models.installed(key):
                j["tr"].update(state="downloading", stage="Downloading the translator (one time) …")
                self.queue.insert(0, (jid, step))
                self.current = (jid, {"kind": "download"})
                self.wk.submit({"kind": "download", "id": f"dl:{jid}", "models": [key]})
                return
            j["tr"].update(state="working", stage="Translating …", engine=eng, texts=[None] * len(texts))
            self.wk.submit({"kind": "translate", "id": f"tl:{jid}", "texts": texts, "src": None, "tgt": step["tgt"],
                            "engine": eng, "hint": j.get("hint")})
        elif kind == "summarize":
            from . import ai
            if not models.installed(ai.AI_KEY):
                j["sum"].update(state="downloading", stage="Downloading the AI model (one time, 4 GB) …")
                self.queue.insert(0, (jid, step))
                self.current = (jid, {"kind": "download"})
                self.wk.submit({"kind": "download", "id": f"dl:{jid}", "models": [ai.AI_KEY]})
                return
            res = j["tl_result"] if step.get("which") == "translation" and j.get("tl_result") else j["result"]
            j["sum"].update(state="working", stage="Reading …", text="")
            self.wk.submit({"kind": "summarize", "id": f"sm:{jid}", "text": engine.to_text(res, True),
                            "lang": step["lang"], "detail": step.get("detail", "normal"), "times": True})

    def add_step(self, jid, step):
        with self.lock:
            self.queue.append((jid, step))
            self._kick()

    def position(self, jid):
        with self.lock:
            ahead = [q for q in self.queue if q[0] != jid]
            idx = next((i for i, q in enumerate(self.queue) if q[0] == jid), None)
            return (idx if idx is not None else len(ahead)) + (1 if self.current else 0)

    # ---- worker events (reader thread)
    def _event(self, kind, *a):
        with self.lock:
            if kind == "device":
                self.has_gpu, self.device = a[0], a[1]
                return
            jid_full = a[0] if a else None
            if kind == "died":
                cur = self.current
                self.current = None
                if cur and cur[0] in self.items:
                    self._fail(cur[0], cur[1]["kind"], "The worker stopped unexpectedly. Try again.")
                self.wk = Worker(self._event)          # a fresh one for the next job
                self.wk.ensure()
                self._kick()
                return
            if not isinstance(jid_full, str) or ":" not in jid_full:
                return
            tag, jid = jid_full.split(":", 1)
            j = self.items.get(jid)
            if j is None:
                if kind in ("done", "fail", "downloaded", "tdone", "sdone"):
                    self.current = None
                    self._kick()
                return
            if kind == "prog":
                frac, text = a[1], a[2]
                target = {"tr": j, "tl": j.get("tr"), "sm": j.get("sum"), "dl": None}.get(tag)
                if tag == "dl":
                    target = j if j["state"] == "downloading" else (j["tr"] if j.get("tr", {}).get("state") ==
                                                                   "downloading" else j.get("sum"))
                if target is not None:
                    if frac is not None:
                        target["progress"] = frac if tag != "tr" or text.startswith("Downloading") \
                            else 0.05 + 0.95 * frac
                    target["stage"] = text
            elif kind == "seg":
                j.setdefault("live", []).append(a[1])
            elif kind == "tseg":
                i, text = a[1], a[2]
                tr = j.get("tr") or {}
                if tr.get("texts") is not None and i < len(tr["texts"]):
                    tr["texts"][i] = text
            elif kind == "stext":
                j.get("sum", {})["text"] = a[1]
            elif kind == "downloaded":
                self.current = None
                self._kick()
            elif kind == "done":
                res, took = a[1], a[2]
                j.update(state="done", result=res, took=round(took, 1), progress=1.0, live=None,
                         stage=f"Done in {took:.0f} s")
                hint = langs.from_iso(res.get("spoken_language"))
                if hint == "zho_Hans":
                    hint = langs.chinese_variant(" ".join(s["text"] for s in res["segments"][:50]))
                j["hint"] = hint
                self.current = None
                self._kick()
            elif kind == "tdone":
                res = a[1]
                tgt = j["tr"]["tgt"]
                j["tr"].update(state="done", texts=res["texts"], progress=1.0, engine=res["engine"],
                               stage=f"Translated with {'the AI model' if res['engine'] == 'ai' else 'NLLB-200'}")
                base = j["result"]
                iso = "yue" if tgt == "yue_Hant" else langs.NLLB_TO_ISO.get(tgt, "en")
                j["tl_result"] = dict(base, task="translate", language=iso, target_name=langs.name(tgt),
                                      source_name=lang_name(base.get("spoken_language")), model=res["model"],
                                      segments=[dict(s, text=t or "", words=[]) for s, t in
                                                zip(base["segments"], res["texts"])])
                self.current = None
                self._kick()
            elif kind == "sdone":
                j["sum"].update(state="done", text=a[1], progress=1.0, stage="Summary ready")
                self.current = None
                self._kick()
            elif kind == "fail":
                self.current = None
                self._fail(jid, {"tr": "transcribe", "tl": "translate", "sm": "summarize",
                                 "dl": "download"}.get(tag), a[1])
                self._kick()

    def _fail(self, jid, kind, msg):
        j = self.items.get(jid)
        if not j:
            return
        if kind == "translate" or (kind == "download" and j.get("tr", {}).get("state") == "downloading"):
            j["tr"].update(state="failed", stage=msg)
        elif kind == "summarize" or (kind == "download" and j.get("sum", {}).get("state") == "downloading"):
            j["sum"].update(state="failed", stage=msg)
        else:
            j.update(state="failed", stage=msg)


# ============================================================================ HTTP
class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, hosts, jobs):
        super().__init__(("0.0.0.0", port), Handler)
        self.hosts, self.jobs = hosts, jobs

    def server_bind(self):
        # Skip HTTPServer's machine-name lookup (socket.getfqdn), which can hang on some networks.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def _page():
    for p in (Path(__file__).parent / "web_page.html", asset("..", "dltr", "web_page.html")):
        if p.exists():
            return p.read_bytes()
    raise FileNotFoundError("web_page.html")


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", (headers or {}).pop("Cache-Control", "no-store"))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _allowed(self):
        """Only requests addressed to this PC (blocks other websites via DNS rebinding)."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in self.server.hosts

    def _json(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            raise BadRequest("bad request") from None

    def _job(self, jid):
        j = self.server.jobs.items.get(jid or "")
        if not j:
            raise BadRequest("That recording is no longer here (sharing was restarted). Upload it again.")
        return j

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/icon.png", "/favicon.ico"):
            ico = asset("icon.png")
            return self._send(200, ico.read_bytes() if ico.exists() else b"", "image/png",
                              {"Cache-Control": "max-age=86400"})
        if not self._allowed():
            return self._send(403, "Open the address shown in dl_transcribe's Share via Wi-Fi window.", "text/plain")
        try:
            if path in ("/", "/index.html"):
                return self._send(200, _page(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                              "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                                              "media-src 'self'"})
            if path == "/api/info":
                return self._send(200, self._info())
            if path == "/api/jobs":
                return self._send(200, self._list())
            if path.startswith("/api/job/"):
                return self._send(200, self._detail(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/media/"):
                return self._media(path.rsplit("/", 1)[-1])
            if path.startswith("/api/export/"):
                return self._export(path.rsplit("/", 1)[-1])
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            log(f"wifi GET {path}: {e!r}")
            return self._send(500, {"error": f"{e.__class__.__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def _info(self):
        jobs = self.server.jobs
        s = jobs.settings
        installed = [k for k in MODELS if models.installed(k)]
        default = s.get("model") if s.get("model") in MODELS else "large-v3-turbo"
        return {"app": APP_NAME, "version": APP_VERSION, "pc": socket.gethostname(), "device": jobs.device,
                "gpu": jobs.has_gpu, "models": [{"key": k, "label": MODELS[k][0], "installed": k in installed}
                                                 for k in MODELS],
                "model": default, "languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
                "targets": [{"code": c, "name": n} for c, n in langs.NLLB.items()], "formats": list(FORMATS),
                "speakers": bool(s.get("speakers")), "ai": models.installed("qwen3-4b"),
                "translator": models.installed("nllb-3.3b") or models.installed("nllb-1.3b")
                or models.installed("qwen3-4b")}

    @staticmethod
    def _brief(j, jobs):
        out = {k: j.get(k) for k in ("id", "name", "state", "stage", "progress", "size", "added", "took")}
        out["duration"] = (j.get("result") or {}).get("duration") or j.get("duration")
        if j["state"] == "queued":
            out["ahead"] = jobs.position(j["id"])
        return out

    def _list(self):
        jobs = self.server.jobs
        with jobs.lock:
            return {"jobs": [self._brief(jobs.items[i], jobs) for i in reversed(jobs.order) if i in jobs.items],
                    "device": jobs.device, "gpu": jobs.has_gpu}

    def _detail(self, jid):
        jobs = self.server.jobs
        with jobs.lock:
            j = self._job(jid)
            out = self._brief(j, jobs)
            res = j.get("result")
            segs = res["segments"] if res else (j.get("live") or [])
            out["segments"] = [{"start": s["start"], "end": s["end"], "text": s["text"],
                                "speaker": s.get("speaker")} for s in segs]
            out["speakers"] = (res or {}).get("speakers") or []
            out["language"] = lang_name(res.get("spoken_language")) if res else None
            out["media"] = f"/api/media/{jid}" if j.get("media") else None
            out["media_type"] = MIME.get(Path(j.get("media") or "").suffix.lower(), "audio/mpeg")
            out["video"] = Path(j.get("media") or "").suffix.lower() in (".mp4", ".m4v")
            tr = j.get("tr")
            if tr:
                out["tr"] = {k: tr.get(k) for k in ("state", "stage", "progress", "tgt", "engine", "texts")}
                out["tr"]["target_name"] = langs.name(tr["tgt"])
            sm = j.get("sum")
            if sm:
                out["sum"] = {k: sm.get(k) for k in ("state", "stage", "progress", "text")}
            return out

    def _media(self, jid):
        j = self._job(jid)
        p = Path(j.get("media") or "")
        if not p.is_file():
            raise BadRequest("The audio isn't ready yet.")
        size = p.stat().st_size
        ctype = MIME.get(p.suffix.lower(), "audio/mpeg")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as fh:
            fh.seek(start)
            left = length
            try:
                while left > 0:
                    chunk = fh.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionError, OSError):
                pass                       # the browser stopped reading (seeking) — normal

    def _export(self, jid):
        q = self._query()
        fmt = q.get("fmt", "txt")
        if fmt not in engine.WRITERS:
            raise BadRequest("Unknown format.")
        jobs = self.server.jobs
        with jobs.lock:
            j = self._job(jid)
            which = q.get("which", "transcript")
            res = j.get("tl_result") if which == "translation" else j.get("result")
            if not res:
                raise BadRequest("Not ready yet.")
            res = dict(res)
            if q.get("speakers") == "0":
                res["speakers"] = []
        ts = q.get("ts", "1") == "1"
        data = engine.WRITERS[fmt](res, ts) if fmt in engine.TIMESTAMPED else engine.WRITERS[fmt](res)
        if isinstance(data, str):
            data = data.encode("utf-8-sig" if fmt in ("txt", "csv") else "utf-8")
        stem = Path(j["name"]).stem
        if which == "translation":
            tgt = j["tr"]["tgt"]
            stem += "_" + ({"zho_Hans": "zh-Hans", "zho_Hant": "zh-Hant", "yue_Hant": "yue"}.get(tgt) or
                           langs.NLLB_TO_ISO.get(tgt) or tgt)
        name = f"{stem}.{fmt}"
        ctype = {"pdf": "application/pdf", "json": "application/json", "csv": "text/csv",
                 "srt": "application/x-subrip", "vtt": "text/vtt"}.get(fmt, "text/plain")
        return self._send(200, data, ctype, {
            "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        routes = {"/api/upload": self._upload, "/api/start": self._start, "/api/translate": self._translate,
                  "/api/summarize": self._summarize, "/api/remove": self._remove}
        fn = routes.get(path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            return self._send(200, fn())
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            log(f"wifi POST {path}: {e!r}")
            return self._send(500, {"error": f"{e.__class__.__name__}: {e}"})

    def _upload(self):
        name = _safe_name(self._query().get("name", "recording"))
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            raise BadRequest("The file is empty or too large (8 GB at most).")
        jid = secrets.token_hex(6)
        d = WORK / jid
        d.mkdir(parents=True, exist_ok=True)
        src = d / name
        left = length
        with open(src, "wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        if left:
            shutil.rmtree(d, ignore_errors=True)
            raise BadRequest("The upload was interrupted.")
        dur = engine.probe_duration(src)
        if not dur:
            shutil.rmtree(d, ignore_errors=True)
            raise BadRequest(f"{name} doesn't seem to be audio or video.")
        jobs = self.server.jobs
        j = {"id": jid, "name": name, "path": str(src), "size": length, "duration": dur, "added": time.time(),
             "state": "uploaded", "stage": "Uploaded — press Start", "progress": 0.0, "media": None}
        with jobs.lock:
            jobs.items[jid] = j
            jobs.order.append(jid)
        if src.suffix.lower() in BROWSER_OK:
            j["media"] = str(src)
        else:                               # make an MP3 the browser can play (in the background)
            def conv():
                try:
                    out = media.to_mp3(src, d / "playback.mp3")
                    j["media"] = str(out)
                except Exception as e:
                    log(f"wifi mp3 for {name}: {e!r}")
            threading.Thread(target=conv, daemon=True).start()
        return {"id": jid, "name": name, "duration": dur}

    def _start(self):
        req = self._json()
        jobs = self.server.jobs
        j = self._job(req.get("id"))
        if j["state"] in ("queued", "working", "downloading"):
            return {"ok": True}
        model = req.get("model") if req.get("model") in MODELS else self._info()["model"]
        lang = req.get("language") if req.get("language") in LANGUAGES else None
        opts = {"model": model, "language": lang, "task": "transcribe", "multilingual": bool(req.get("mixed")),
                "hints": str(req.get("hints") or "")[:500], "speakers": bool(req.get("speakers")),
                "num_speakers": None}
        with jobs.lock:
            j.update(state="queued", stage="Waiting for its turn …", progress=0.0, result=None, tr=None, sum=None,
                     tl_result=None)
        jobs.add_step(j["id"], {"kind": "transcribe", "opts": opts})
        return {"ok": True}

    def _translate(self):
        req = self._json()
        jobs = self.server.jobs
        j = self._job(req.get("id"))
        if not j.get("result"):
            raise BadRequest("Transcribe it first.")
        tgt = req.get("tgt") if req.get("tgt") in langs.NLLB else "eng_Latn"
        eng = req.get("engine") if req.get("engine") in ("auto", "ai", "nllb") else "auto"
        with jobs.lock:
            j["tr"] = {"state": "queued", "stage": "Waiting for its turn …", "progress": 0.0, "tgt": tgt,
                       "engine": eng, "texts": None}
            j["tl_result"] = None
        jobs.add_step(j["id"], {"kind": "translate", "tgt": tgt, "engine": eng})
        return {"ok": True}

    def _summarize(self):
        req = self._json()
        jobs = self.server.jobs
        j = self._job(req.get("id"))
        if not j.get("result"):
            raise BadRequest("Transcribe it first.")
        lang = req.get("lang") if req.get("lang") in langs.NLLB else "eng_Latn"
        detail = req.get("detail") if req.get("detail") in ("short", "normal", "detailed") else "normal"
        with jobs.lock:
            j["sum"] = {"state": "queued", "stage": "Waiting for its turn …", "progress": 0.0, "text": ""}
        jobs.add_step(j["id"], {"kind": "summarize", "lang": lang, "detail": detail, "which": req.get("which")})
        return {"ok": True}

    def _remove(self):
        req = self._json()
        jobs = self.server.jobs
        jid = req.get("id")
        with jobs.lock:
            if jobs.current and jobs.current[0] == jid:
                raise BadRequest("It's being worked on right now; wait for it to finish.")
            jobs.queue = [q for q in jobs.queue if q[0] != jid]
            jobs.items.pop(jid, None)
            if jid in jobs.order:
                jobs.order.remove(jid)
        shutil.rmtree(WORK / str(jid), ignore_errors=True)
        return {"ok": True}


# ============================================================================ start / stop (from the window)
class Sharing:
    """The running Wi-Fi server: .urls, .stop(). Start with Sharing.start(settings)."""

    def __init__(self):
        self.srv = None
        self.jobs = None
        self.urls = []
        self.mdns = "pending"
        self._closers = []
        self._stopped = False

    @classmethod
    def start(cls, settings, on_change=None):
        self = cls()
        ip = lan_ip()
        if not ip:
            raise RuntimeError("This PC isn't connected to a network.")
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)
        hosts = {ip, f"{MDNS_HOST}.local", f"{socket.gethostname().lower()}.local", "127.0.0.1", "localhost"}
        self.jobs = Jobs(settings)
        for port in PORTS:
            try:
                self.srv = Server(port, hosts, self.jobs)
                break
            except OSError:
                continue
        if self.srv is None:
            self.jobs.close()
            raise RuntimeError("Couldn't open a network port.")
        port = self.srv.server_address[1]
        suffix = "" if port == 80 else f":{port}"
        self.urls = [f"http://{ip}{suffix}"]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()

        def announce():          # http://dl-transcribe.local follows when mDNS is ready (can be slow or blocked)
            close = _announce(ip, port)
            if close and not self._stopped:
                self._closers.append(close)
                self.urls.append(f"http://{MDNS_HOST}.local{suffix}")
            elif close:
                close()
            self.mdns = "on" if close else "off"
            if on_change:
                on_change()
        threading.Thread(target=announce, daemon=True).start()
        return self

    def stop(self):
        self._stopped = True
        try:
            if self.srv:
                self.srv.shutdown()
                self.srv.server_close()
        except Exception:
            pass
        for c in self._closers:
            try:
                c()
            except Exception:
                pass
        if self.jobs:
            self.jobs.close()
        shutil.rmtree(WORK, ignore_errors=True)

    def counts(self):
        if not self.jobs:
            return 0, 0
        with self.jobs.lock:
            n = len(self.jobs.items)
            busy = (1 if self.jobs.current else 0) + len(self.jobs.queue)
        return n, busy


def _announce(ip, port):
    """Make http://dl-transcribe.local work (mDNS / Bonjour). Returns a closer, or None."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
        zc = Zeroconf(interfaces=[ip])
        info = ServiceInfo("_http._tcp.local.", f"{APP_NAME} on {socket.gethostname()}._http._tcp.local.",
                           port=port, addresses=[socket.inet_aton(ip)], server=f"{MDNS_HOST}.local.",
                           properties={"path": "/"})
        zc.register_service(info, allow_name_change=True)

        def close():
            try:
                zc.unregister_service(info)
            finally:
                zc.close()
        return close
    except Exception as e:
        log(f"mDNS: {e!r}")
        return None
