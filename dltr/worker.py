"""Transcription runs in its own process so the window never freezes, Cancel can really stop it,
and a GPU/driver crash on another PC only kills the worker (the file is then retried on the CPU)."""

import multiprocessing as mp
import os
import queue
import threading
import time
import traceback


# ------------------------------------------------------------------ inside the worker process
def _main(jobs, out, force_cpu):
    if force_cpu:
        os.environ["DLTR_FORCE_CPU"] = "1"

    def watchdog():
        # if the window is killed (crash, Task Manager), don't linger in the background
        parent = mp.parent_process()
        while parent is None or parent.is_alive():
            time.sleep(2)
        os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()
    import warnings
    warnings.filterwarnings("ignore")
    from . import cuda, engine
    from .common import log

    has_gpu, name = cuda.gpu()
    out.put(("device", has_gpu, name, cuda.problem()))
    while True:
        job = jobs.get()
        if job is None:
            return
        kind, jid = job["kind"], job["id"]
        t0 = time.time()
        try:
            if kind == "download":
                for i, m in enumerate(job["models"]):
                    if job.get("force") or not engine.is_downloaded(m):
                        engine.download(m, lambda f, t, i=i: out.put(
                            ("prog", jid, (i + (f or 0)) / len(job["models"]), t)))
                out.put(("downloaded", jid))
                continue
            if kind == "translate":
                from . import ai
                res = ai.translate(job["texts"], job.get("src"), job["tgt"], job.get("engine", "auto"),
                                   progress=lambda f, t: out.put(("prog", jid, f, t)),
                                   on_text=lambda i, t: out.put(("tseg", jid, i, t)), hint=job.get("hint"))
                out.put(("tdone", jid, res, time.time() - t0))
                continue
            if kind == "summarize":
                from . import ai
                text = ai.summarize(job["text"], job.get("lang", "eng_Latn"), job.get("detail", "normal"),
                                    job.get("times", True), progress=lambda f, t: out.put(("prog", jid, f, t)),
                                    on_text=lambda t: out.put(("stext", jid, t)))
                out.put(("sdone", jid, text, time.time() - t0))
                continue
            if kind == "chat":
                from . import ai
                ans = ai.chat(job["ctx"], job.get("history") or [], job["question"],
                              on_text=lambda t: out.put(("ctext", jid, t)),
                              progress=lambda f, t: out.put(("prog", jid, f, t)))
                out.put(("cdone", jid, ans, time.time() - t0))
                continue
            if kind == "suggest":
                from . import ai
                out.put(("sugg", jid, ai.suggestions(job["ctx"], job.get("history") or [])))
                continue
            if kind == "names":
                from . import ai
                out.put(("names", jid, ai.find_names(job["ctx"])))
                continue
            if kind == "tts":
                from . import tts
                info = tts.synthesize(job["texts"], job["lang"], voice=job.get("voice"), out=job["out"],
                                      gender=job.get("gender", "female"),
                                      progress=lambda f, t: out.put(("prog", jid, f, t)))
                out.put(("voiced", jid, dict(info, out=job["out"]), time.time() - t0))
                continue
            if kind == "dictate":
                out.put(("prog", jid, 0.3, "Turning your voice into text …"))
                out.put(("dictated", jid, engine.dictate(job["audio"], job["model"], job.get("lang"))))
                continue
            stage = {"t": t0}

            def prog(frac, text):
                stage["t"] = time.time()
                log(f"  [{time.time() - t0:6.1f}s] {text}")
                out.put(("prog", jid, frac, text))

            log(f"START {job['file']} opts={job['opts']}")
            res = engine.transcribe(job["file"], job["opts"], on_progress=prog,
                                    on_segment=lambda seg: out.put(("seg", jid, seg)))
            log(f"OK {job['file']} in {time.time() - t0:.1f}s on {res['device']}")
            out.put(("done", jid, res, time.time() - t0))
        except Exception as e:
            log(f"FAIL {job.get('file') or kind}: {traceback.format_exc()}")
            out.put(("fail", jid, str(e) or e.__class__.__name__))


# ------------------------------------------------------------------ used by the window
class Worker:
    """Owns one worker process. Messages from it are passed to on_event(kind, *args) on a reader thread."""

    def __init__(self, on_event, force_cpu=False):
        self.on_event = on_event
        self.force_cpu = force_cpu
        self.ctx = mp.get_context("spawn")
        self.proc = None
        self.jobs = self.out = None
        self.busy_id = None
        self._gen = 0

    def ensure(self):
        if self.proc is not None and self.proc.is_alive():
            return
        self._gen += 1
        self.jobs, self.out = self.ctx.Queue(), self.ctx.Queue()
        self.proc = self.ctx.Process(target=_main, args=(self.jobs, self.out, self.force_cpu), daemon=True,
                                     name="dl_transcribe-worker")
        self.proc.start()
        threading.Thread(target=self._read, args=(self.proc, self.out, self._gen), daemon=True).start()

    def submit(self, job):
        self.ensure()
        self.busy_id = job["id"]
        self.jobs.put(job)

    def kill(self):
        """Stop whatever is running right now (Cancel). A fresh worker starts with the next job."""
        p, self.proc = self.proc, None
        self.busy_id = None
        if p is not None and p.is_alive():
            p.kill()
            p.join(5)

    def close(self):
        try:
            if self.proc is not None and self.proc.is_alive():
                self.jobs.put(None)
                self.proc.join(2)
        except Exception:
            pass
        self.kill()

    def _read(self, proc, out, gen):
        while True:
            try:
                msg = out.get(timeout=0.5)
            except queue.Empty:
                if not proc.is_alive():
                    if gen == self._gen and self.proc is proc:   # died by itself, not via kill()
                        self.on_event("died", self.busy_id, proc.exitcode)
                    return
                continue
            except (EOFError, OSError):
                return
            if gen != self._gen:
                return
            if msg[0] in ("done", "fail", "downloaded", "tdone", "sdone", "cdone", "sugg", "names", "dictated",
                          "voiced"):
                self.busy_id = None
            self.on_event(*msg)
