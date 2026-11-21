"""English lines under the text (the Transcribe tab, the bigger transcript window and Chat): each sentence is
translated to English in the background by the offline translator, once (kept for the rest of the session).

It uses the same AI worker as the Translate / Summary / Chat tabs, as background work: anything you start there
goes first, and the English lines carry on afterwards."""

import re

from . import models, pinyin

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def needs_english(text, lang=None):
    """Does this sentence need an English line? Not when it's English already."""
    t = (text or "").strip()
    if not t or not re.search(r"\w", t):
        return False
    if lang == "en":
        return bool(_NON_ASCII.search(t))         # an English transcript: only the lines in another script
    if lang:
        return True
    if _NON_ASCII.search(t):
        return True
    from . import langs
    return langs.detect(t, fallback="eng_Latn") != "eng_Latn"


def english_ok(lang, texts):
    """Can English lines be shown at all (greyed out for English)?"""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return False
    if lang and lang != "en":
        return True
    if lang == "en":
        return any(_NON_ASCII.search(t) and re.search(r"\w", t) for t in texts)
    from . import langs
    return langs.detect(" ".join(texts)[:2000], fallback="eng_Latn") != "eng_Latn" or any(
        pinyin.is_chinese(t) for t in texts)


class English:
    """Translates sentences to English in the background and remembers them. Views ask with want(); when new
    English arrives, every listener is called (they redraw)."""

    ai_label = "English lines"

    def __init__(self, app):
        self.app = app
        self.cache = {}          # sentence -> English
        self.failed = set()
        self.queue = []          # [(sentences, hint)]
        self.running = None      # sentences being translated now
        self.listeners = []
        self.declined = False    # said no to the model download: don't ask again this session
        self._notify_id = None
        self._retry_id = None

    def get(self, text):
        return self.cache.get((text or "").strip())

    def want(self, texts, hint=None):
        """Ask for the English of these sentences. True when all of them are known already."""
        need = list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))
        need = [t for t in need if t not in self.cache and t not in self.failed]
        if not need:
            return True
        busy = set(self.running or ()) | {t for q, _h in self.queue for t in q}
        need = [t for t in need if t not in busy]
        if need and not self.declined:
            for i in range(0, len(need), 200):
                self.queue.append((need[i:i + 200], hint))
            self._next()
        return False

    def _next(self):
        self._retry_id = None
        if self.running is not None or not self.queue or self.declined:
            return
        app = self.app
        if app.ai.busy() and app.ai.owner is not self:
            self._retry_id = app.after(1500, self._next)       # the AI is busy with something you started
            return
        texts, hint = self.queue[0]
        from . import ai
        eng, key, _srcs = ai.plan(texts, None, "eng_Latn", "auto", app.has_gpu, hint)
        if not models.installed(key):
            # use whichever translator is already on this PC before asking to download one
            for other in ("nllb", "ai"):
                e2, k2, _s = ai.plan(texts, None, "eng_Latn", other, app.has_gpu, hint)
                if models.installed(k2):
                    eng, key = e2, k2
                    break
        need = [] if models.installed(key) else [key]
        if need:
            from .tabs import ensure_models
            got = ensure_models(app, need, "English lines (a translation under each sentence)")
            if got is None:
                self.declined = True
                self.queue.clear()
                for v in self._off_vars():
                    v.set(False)
                return
        self.queue.pop(0)
        self.running = texts
        jobs = [{"kind": "download", "id": "en-dl", "models": need, "bg": True}] if need else []
        jobs.append({"kind": "translate", "id": "en", "texts": texts, "src": None, "tgt": "eng_Latn",
                     "engine": eng, "hint": hint, "bg": True})
        app.ai.run(self, jobs)

    def _off_vars(self):
        app = self.app
        out = [app.v_en]
        if hasattr(app, "chat_tab"):
            out.append(app.chat_tab.v_en)
        return out

    def ai_cancelled(self):
        """Something you started needed the AI: put these sentences back in line for later."""
        if self.running:
            self.queue.insert(0, (self.running, None))
        self.running = None
        if self._retry_id is None:
            self._retry_id = self.app.after(1500, self._next)

    def stop(self):
        self.ai_cancelled()

    def on_ai(self, kind, *a):
        app = self.app
        if kind == "tseg":
            _jid, i, text = a
            if self.running and i < len(self.running) and text:
                self.cache[self.running[i]] = text.strip()
                self._notify()
        elif kind == "tdone":
            _jid, res, _took = a
            for src, t in zip(self.running or (), res.get("texts") or ()):
                if t:
                    self.cache[src] = t.strip()
                else:
                    self.failed.add(src)
            self.running = None
            app.ai.release()
            self._notify()
            self._next()
        elif kind == "downloaded":
            app.refresh_models()
        elif kind in ("fail", "died"):
            for src in self.running or ():
                self.failed.add(src)
            self.running = None
            app.ai.stop()
            self._notify()
            self._next()

    def _notify(self):
        if self._notify_id is None:
            self._notify_id = self.app.after(350, self._fire)

    def _fire(self):
        self._notify_id = None
        for fn in list(self.listeners):
            try:
                fn()
            except Exception as e:
                from .common import log
                log(f"english lines: {e!r}")
