"""Offline translation and AI summaries, on CTranslate2 (the same engine as the transcriber, GPU or CPU).

Translation goes sentence by sentence, so every translated sentence keeps its timestamp and speaker:
  - NLLB-200 (Meta): 200 languages, fast, also quick on a CPU.
  - Qwen3-4B-Instruct (Alibaba): an AI model that writes more natural translations for the big languages
    (English, Chinese, Cantonese, Japanese, Korean, Malay, ...). Needs an NVIDIA GPU to be quick.
Summaries use Qwen3-4B-Instruct. Long transcripts are summarised part by part, then combined.
"""

import os
import re
import threading

from . import cuda, langs, models
from .common import log

_lock = threading.Lock()
_cache = {}                       # model key -> (model, tokenizer, device)

NLLB_KEYS = ("nllb-3.3b", "nllb-1.3b")      # best first
AI_KEY = "qwen3-4b"


class Cancelled(Exception):
    pass


def device():
    if os.environ.get("DLTR_FORCE_CPU"):
        return "cpu"
    if cuda.gpu()[0]:
        return "cuda"
    from . import engine
    return "mps" if engine._mps_ok() else "cpu"


def used_device():
    """The device the loaded model really runs on (after any fallback)."""
    return next((v[2] for v in _cache.values()), device())


def nllb_key():
    """The best NLLB model on this computer (or the one to download)."""
    return next((k for k in NLLB_KEYS if models.installed(k)), NLLB_KEYS[0])


def plan(texts, src, tgt, choice="auto", has_gpu=True, hint=None):
    """Decide how to translate. Returns (engine "ai"|"nllb", model key, per-sentence source codes).
    src None = detect per sentence (mixed-language transcripts work too)."""
    if src:
        srcs = [src] * len(texts)
    else:
        doc = hint or langs.detect(" ".join(texts)[:3000])
        srcs = [langs.detect(t, fallback=doc) for t in texts]
    if choice == "ai":
        eng = "ai"
    elif choice == "nllb":
        eng = "nllb"
    else:
        need = {s for s, t in zip(srcs, texts) if s != tgt and t.strip()}
        eng = "ai" if (has_gpu and tgt in langs.AI_GOOD and need <= langs.AI_GOOD) else "nllb"
    return eng, (AI_KEY if eng == "ai" else nllb_key()), srcs


def _load(key):
    with _lock:
        if key in _cache:
            return _cache[key]
        import ctranslate2
        import tokenizers
        if not models.installed(key):
            raise RuntimeError(f"The {key} model isn't downloaded yet.")
        for k in list(_cache):            # one model at a time keeps memory low on smaller GPUs
            del _cache[k]
        dev = device()
        ct = {"cuda": "int8_float16", "mps": "int8_float16"}.get(dev, "int8")
        path = str(models.path(key))
        cls = ctranslate2.Generator if key == AI_KEY else ctranslate2.Translator
        obj = None
        for d, c in ((dev, ct), ("mps", "float16"), ("cpu", "int8")):
            if d == "mps" and dev != "mps":
                continue
            try:
                obj = cls(path, device=d, compute_type=c)
                dev = d
                break
            except Exception as e:
                if d == "cpu":
                    raise
                log(f"{key} on {d}/{c} failed ({e}); trying the next option")
        tok = tokenizers.Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        _cache[key] = (obj, tok, dev)
        return _cache[key]


def unload():
    with _lock:
        _cache.clear()


# ------------------------------------------------------------------ translation
def translate(texts, src, tgt, choice="auto", progress=None, on_text=None, cancel=None, hint=None):
    """Translate a list of sentences into tgt (NLLB code). src: NLLB code or None (detect).
    on_text(i, translation) as sentences finish. Returns dict(texts, engine, model, srcs)."""
    dev_gpu = device() == "cuda"
    eng, key, srcs = plan(texts, src, tgt, choice, dev_gpu, hint)
    if progress:
        progress(0.0, f"Loading the {'AI' if eng == 'ai' else 'NLLB-200'} translator …")
    model, tok, dev = _load(key)
    out = [None] * len(texts)
    todo = []
    for i, (t, s) in enumerate(zip(texts, srcs)):
        if not t.strip() or s == tgt or (langs.NLLB_TO_ISO.get(s) == langs.NLLB_TO_ISO.get(tgt) and
                                          s not in ("zho_Hans", "zho_Hant", "yue_Hant")):
            out[i] = t                     # already in the target language
            if on_text:
                on_text(i, t)
        else:
            todo.append(i)
    n = max(len(todo), 1)
    batch = 16 if eng == "nllb" else (8 if dev in ("cuda", "mps") else 2)
    done = 0
    for b in range(0, len(todo), batch):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        idx = todo[b:b + batch]
        res = (_nllb(model, tok, [texts[i] for i in idx], [srcs[i] for i in idx], tgt) if eng == "nllb" else
               _ai_translate(model, tok, texts, idx, tgt))
        for i, r in zip(idx, res):
            out[i] = r
            if on_text:
                on_text(i, r)
        done += len(idx)
        if progress:
            progress(done / n, f"Translating … {done} / {len(todo)} sentences")
    return {"texts": out, "engine": eng, "model": key, "srcs": srcs}


def _nllb(model, tok, texts, srcs, tgt):
    batch = []
    for t, s in zip(texts, srcs):
        ids = tok.encode(t, add_special_tokens=False).tokens[:400]
        batch.append([s] + ids + ["</s>"])
    res = model.translate_batch(batch, target_prefix=[[tgt]] * len(batch), beam_size=4,
                                max_decoding_length=min(512, max(len(x) for x in batch) * 3 + 20),
                                repetition_penalty=1.1)
    out = []
    for r in res:
        toks = r.hypotheses[0][1:]
        out.append(tok.decode([tok.token_to_id(x) for x in toks if tok.token_to_id(x) is not None],
                              skip_special_tokens=True).strip())
    return out


def _chat_tokens(tok, system, user):
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
              f"<|im_start|>assistant\n")
    return [tok.id_to_token(i) for i in tok.encode(prompt, add_special_tokens=False).ids]


_PREFIX = re.compile(r"^(translation|translated text|译文|翻譯|翻译)\s*[:：]\s*", re.I)


def _ai_translate(model, tok, texts, idx, tgt):
    lang = langs.ai_name(tgt)
    system = (f"You are a professional translator. Translate the text the user gives you into {lang}. "
              "Keep the meaning, tone, names and numbers. Translate everything, including slang and mixed-"
              "language words. Output only the translation of that text: no notes, no quotes, no explanations.")
    prompts = []
    for i in idx:
        ctx = [texts[j] for j in range(max(0, i - 2), i) if texts[j].strip()]
        user = ""
        if ctx:
            user += "Earlier lines, for context only (do not translate them):\n" + "\n".join(ctx) + "\n\n"
        user += "Text to translate:\n" + texts[i]
        prompts.append(_chat_tokens(tok, system, user))
    res = model.generate_batch(prompts, max_length=min(1024, max(len(texts[i]) for i in idx) * 3 + 64),
                               sampling_topk=1, include_prompt_in_result=False,
                               end_token=["<|im_end|>", "<|endoftext|>"], repetition_penalty=1.05)
    out = []
    for r in res:
        t = tok.decode(r.sequences_ids[0], skip_special_tokens=True).strip()
        t = _PREFIX.sub("", t).strip().strip('"“”').strip()
        out.append(t)
    return out


# ------------------------------------------------------------------ summaries
DETAIL = {"short": "Keep it short: 2-3 sentence overview and 3-6 key points.",
          "normal": "2-4 sentence overview and 5-10 key points.",
          "detailed": "A detailed summary: 4-6 sentence overview and 8-15 key points with specifics."}


def summarize(text, lang="eng_Latn", detail="normal", times=True, progress=None, on_text=None, cancel=None):
    """Bullet-point summary of a transcript (or any text), written in `lang` (NLLB code)."""
    if progress:
        progress(0.0, "Loading the AI model …")
    model, tok, dev = _load(AI_KEY)
    lang_name = langs.ai_name(lang)
    budget = 12000 if dev in ("cuda", "mps") else 3500          # tokens per part (CPU: smaller parts stay quick)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    parts, cur, cur_n = [], [], 0
    for ln in lines:
        n = len(tok.encode(ln, add_special_tokens=False).ids)
        if cur and cur_n + n > budget:
            parts.append("\n".join(cur))
            cur, cur_n = [], 0
        cur.append(ln)
        cur_n += n
    if cur:
        parts.append("\n".join(cur))
    if not parts:
        raise RuntimeError("There's no text to summarise.")

    if len(parts) > 1:                  # long: notes for each part first, then one summary of the notes
        notes = []
        for i, p in enumerate(parts):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress:
                progress(i / (len(parts) + 1), f"Reading part {i + 1} of {len(parts)} …")
            sys_ = ("You take notes on a part of a transcript. Write concise bullet notes in English of everything "
                    "important: topics, facts, numbers, names, decisions, tasks." +
                    (" Keep the (m:ss) time of key moments at the start of a note." if times else ""))
            lo, hi = i / (len(parts) + 1), (i + 1) / (len(parts) + 1)
            notes.append(_generate(model, tok, sys_, p, 700, cancel=cancel,
                                   on_step=_stepper(progress, lo, hi, 350, f"Reading part {i + 1} of {len(parts)} …")))
        source = "\n".join(notes)
        intro = "These are notes taken from a long transcript, in order."
    else:
        source = parts[0]
        intro = "This is a transcript." if times else "This is a text."
    if progress:
        progress(len(parts) / (len(parts) + 1), "Writing the summary …")
    fmt = ("## Overview\n(sentences)\n\n## Key points\n- ...\n" +
           ("\n## Key moments\n- (m:ss) ...\n" if times else "") +
           "\n## Action items\n- ...   (only if there are tasks, decisions or deadlines; otherwise leave this "
           "section out)")
    system = (f"You summarise transcripts accurately. Write the whole summary in {lang_name}, including the "
              f"headings. {DETAIL.get(detail, DETAIL['normal'])} Only use what is in the text; don't invent "
              "anything. Use this format:\n" + fmt)
    user = (f"{intro}\n\n{source}\n\n---\nNow write the summary. Write it entirely in {lang_name} "
            f"(translate the headings too), even if the text above is in another language.")
    base = len(parts) / (len(parts) + 1) if len(parts) > 1 else 0.05
    src_n = len(tok.encode(source, add_special_tokens=False).ids)
    expect = min({"short": 300, "normal": 550, "detailed": 950}.get(detail, 550),   # typical length in tokens
                 120 + src_n // (3 if detail == "detailed" else 4))               # (shorter for a short text)
    out = _generate(model, tok, system, user, 1500, on_text, cancel,
                    on_step=_stepper(progress, base, 1.0, expect, "Writing the summary …"))
    out = _drop_empty_sections(out)
    if on_text:
        on_text(out)
    return out


_EMPTY = re.compile(r"^\s*[-*•]?\s*(none|n/?a|nil|no action items?|无|無|没有|沒有|tiada|tidak ada|なし|없음)"
                    r"[.。]?\s*$", re.I)


def _drop_empty_sections(text):
    """Remove a heading whose section only says 'None' (the model sometimes keeps Action items anyway)."""
    blocks = re.split(r"(?m)^(?=#{1,3} )", text)
    keep = []
    for b in blocks:
        body = [ln for ln in b.splitlines()[1:] if ln.strip()]
        if b.lstrip().startswith("#") and (not body or all(_EMPTY.match(ln) for ln in body)):
            continue
        keep.append(b.strip())
    return "\n\n".join(k for k in keep if k)


def _stepper(progress, lo, hi, expect, text):
    """Progress while the model writes: moves from lo towards hi as tokens come out (the final length isn't
    known, so it eases in near the end instead of stopping at a fixed point)."""
    if not progress:
        return None

    def step(n):
        f = n / expect
        f = f * 0.9 if f <= 1 else 0.9 + 0.09 * (1 - 1 / f)     # 90 % at the usual length, then creeps to 99 %
        progress(lo + (hi - lo) * min(f, 0.99), text)
    return step


def _generate(model, tok, system, user, max_new, on_text=None, cancel=None, on_step=None):
    prompt = _chat_tokens(tok, system, user)
    ids = []
    last = 0
    for step in model.generate_tokens(prompt, max_length=max_new, sampling_topk=1,
                                      end_token=["<|im_end|>", "<|endoftext|>"], repetition_penalty=1.05):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        ids.append(step.token_id)
        if len(ids) - last >= 8:
            last = len(ids)
            if on_text:
                on_text(tok.decode(ids, skip_special_tokens=True))
            if on_step:
                on_step(len(ids))
    text = tok.decode(ids, skip_special_tokens=True).strip()
    if on_text:
        on_text(text)
    return text


# ------------------------------------------------------------------ chat about a recording
# ctx (built by the window): {"title", "lines": [[start seconds or None, speaker label or "", text], ...],
#                             "speakers": [{"name", "voice"}], "summary": str|None, "translation": str|None}

def _hms(t):
    t = int(max(0, t or 0))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _line(ln, voices=None):
    t, who, text = ln
    v = (voices or {}).get(who)
    who = f"{who} ({v})" if who and v else who
    return (f"[{_hms(t)}] " if t is not None else "") + (f"{who}: " if who else "") + text


def _voices(ctx):
    return {s["name"]: s["voice"] for s in ctx.get("speakers") or [] if s.get("voice")}


def _speaker_block(ctx):
    sp = ctx.get("speakers") or []
    if not sp:
        return ""
    rows = []
    for s in sp:
        v = s.get("voice")
        rows.append(f"- {s['name']}" + (f" ({v} voice)" if v else " (voice not recognised)"))
    block = ("Speakers (detected automatically from their voices; male/female is an estimate):\n" +
             "\n".join(rows) + "\n\n")
    # When each speaker talks: their turns as time ranges, so "when does the woman speak?" is a lookup.
    lines = [ln for ln in ctx.get("lines") or [] if ln[0] is not None and ln[1]]
    if lines:
        turns = {}
        for i, (t, who, _txt) in enumerate(lines):
            nxt = lines[i + 1][0] if i + 1 < len(lines) else None
            cur = turns.setdefault(who, [])
            if cur and cur[-1][2] == i - 1:
                cur[-1][1], cur[-1][2] = nxt if nxt is not None else t, i
            else:
                cur.append([t, nxt if nxt is not None else t, i])
        voices = _voices(ctx)
        block += "When each speaker talks (their turns):\n"
        for who, ts in turns.items():
            parts = [f"[{_hms(a)}]-[{_hms(b)}]" if b - a >= 2 else f"[{_hms(a)}]" for a, b, _i in ts[:200]]
            v = voices.get(who)
            block += f"- {who}{f' ({v})' if v else ''}: " + ", ".join(parts) + (" …" if len(ts) > 200 else "") + "\n"
        block += "\n"
    return block


_WORD = re.compile(r"\w+", re.U)


def _pick_lines(tok, lines, question, budget, voices=None):
    """All lines if they fit; otherwise the lines that best match the question (with their neighbours),
    in their original order. Returns (lines, shortened?)."""
    texts = [_line(ln, voices) for ln in lines]
    sizes = [len(tok.encode(t, add_special_tokens=False).ids) + 1 for t in texts]
    if sum(sizes) <= budget:
        return texts, False
    q = {w.lower() for w in _WORD.findall(question) if len(w) > 2}
    if not q:                                  # short / CJK question: match characters instead
        q = {c for c in question if c.strip() and not c.isascii()}
    order = sorted(range(len(texts)), key=lambda i: (-sum(1 for w in q if w in texts[i].lower()), i))
    keep, used = set(), 0
    for i in order:
        for j in range(max(0, i - 2), min(len(texts), i + 3)):
            if j not in keep and used + sizes[j] <= budget:
                keep.add(j)
                used += sizes[j]
        if used >= budget * 0.95:
            break
    return [texts[i] for i in sorted(keep)], True


def _chat_system(ctx, body, partial):
    has_times = any(ln[0] is not None for ln in ctx.get("lines") or [])
    rules = ("You are a helpful assistant inside dl_transcribe. The user asks about the recording below; answer "
             "from its transcript (and the summary or translation if given). Be accurate and concise. "
             "Answer in the language the user writes in. If the recording doesn't say, say so; don't invent. ")
    if has_times:
        rules += ("When you point to moments, give their times exactly as written in the transcript, in square "
                  "brackets, e.g. [12:34] (the user can click them to play that moment). When asked when or "
                  "where something happens, list every matching moment (group long runs like [1:02]-[1:40]). ")
    if ctx.get("speakers"):
        rules += ("Speakers are labelled by name; male/female comes from their voice. Use those labels when the "
                  "user asks who said something, or about a man or a woman speaking. ")
    if partial:
        rules += "The transcript is long, so only the parts that match the question are included below. "
    doc = f"Recording: {ctx.get('title') or 'recording'}\n\n" + _speaker_block(ctx)
    if ctx.get("summary"):
        doc += "Summary (made earlier):\n" + ctx["summary"].strip() + "\n\n"
    if ctx.get("translation"):
        doc += "Translation (made earlier):\n" + ctx["translation"].strip()[:20000] + "\n\n"
    doc += "Transcript:\n" + "\n".join(body)
    return rules + "\n\n" + doc


def _tokens(tok, text):
    return [tok.id_to_token(i) for i in tok.encode(text, add_special_tokens=False).ids]


def chat(ctx, history, question, on_text=None, cancel=None, progress=None):
    """Answer a question about the recording. history: [(question, answer), ...]. Returns the answer.
    The transcript part of the prompt is cached by the model, so follow-up questions start quickly."""
    if progress:
        progress(0.0, "Loading the AI model …")
    model, tok, dev = _load(AI_KEY)
    budget = 30000 if dev == "cuda" else 9000 if dev == "mps" else 6000
    recent = " ".join(q for q, _a in history[-2:])
    body, partial = _pick_lines(tok, ctx.get("lines") or [], question + " " + recent, budget, _voices(ctx))
    static = _tokens(tok, f"<|im_start|>system\n{_chat_system(ctx, body, partial)}<|im_end|>\n")
    convo = ""
    for q, a in history[-6:]:
        convo += f"<|im_start|>user\n{q}<|im_end|>\n<|im_start|>assistant\n{a}<|im_end|>\n"
    convo += f"<|im_start|>user\n{question}<|im_end|>\n<|im_start|>assistant\n"
    if progress:
        progress(0.1, "Reading the transcript …" if len(static) > 3000 else "Thinking …")
    kw = dict(max_length=1200, sampling_topk=1, repetition_penalty=1.05, end_token=["<|im_end|>", "<|endoftext|>"])
    if partial:      # the picked lines change with every question: nothing worth caching
        steps = model.generate_tokens(static + _tokens(tok, convo), **kw)
    else:
        steps = model.generate_tokens(_tokens(tok, convo), static_prompt=static, cache_static_prompt=True, **kw)
    ids, last = [], 0
    for step in steps:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        ids.append(step.token_id)
        if len(ids) == 1 and progress:
            progress(0.5, "Answering …")
        if len(ids) - last >= 4:
            last = len(ids)
            if on_text:
                on_text(tok.decode(ids, skip_special_tokens=True))
    return tok.decode(ids, skip_special_tokens=True).strip()


def suggestions(ctx, history, n=3):
    """n short follow-up questions the user might ask next (or starter questions when history is empty)."""
    model, tok, dev = _load(AI_KEY)
    budget = 6000 if dev == "cuda" else 2500
    last_q = history[-1][0] if history else ""
    body, _partial = _pick_lines(tok, ctx.get("lines") or [], last_q, budget, _voices(ctx))
    doc = _speaker_block(ctx)
    if ctx.get("summary"):
        doc += "Summary:\n" + ctx["summary"].strip()[:4000] + "\n\n"
    doc += "Transcript (may be shortened):\n" + "\n".join(body)
    if history:
        convo = "\n\n".join(f"User: {q}\nAssistant: {a[:1500]}" for q, a in history[-2:])
        task = (f"Here is the conversation so far:\n{convo}\n\nSuggest {n} short follow-up questions the user could "
                "ask next, to dig deeper into this topic or to understand what it means. ")
        lang = f"Write them in {langs.ai_name(langs.detect(last_q))}, the language of the user's last question. "
    else:
        task = (f"Suggest {n} short, useful questions a user might ask about this recording (main points, "
                "decisions, who said what, when something was mentioned). ")
        lang = "Write them in the main language of the transcript. "
    asked = [q for q, _a in history]
    system = ("You write question suggestions for a chat about a recording. " + task + lang +
              "Each one under 12 words and answerable from the recording" +
              (", and different from these questions already asked: " + " | ".join(asked[-6:]) if asked else "") +
              f". Output exactly {n} lines, one question per line, no numbering, no quotes, nothing else.")
    out = _generate(model, tok, system, doc, 160)
    qs = []
    for ln in out.splitlines():
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip().strip('"“”')
        if ln and len(ln) <= 160 and ln.lower() not in {q.lower() for q in qs + asked}:
            qs.append(ln)
    return qs[:n]


def find_names(ctx):
    """Real names for the 'Speaker N' labels, only where the recording says them (an introduction, or being
    addressed by name). Returns {speaker index: name}; a name is kept only if it's really in the transcript."""
    import json
    model, tok, dev = _load(AI_KEY)
    sp = ctx.get("speakers") or []
    if not sp:
        return {}
    budget = 12000 if dev == "cuda" else 4000
    body, _ = _pick_lines(tok, ctx.get("lines") or [], "name I'm I am my called this is speaking", budget, _voices(ctx))
    labels = ", ".join(s["name"] for s in sp)
    system = ("You read a transcript with speaker labels and find each speaker's real name, ONLY when the "
              "transcript makes it clear: they introduce themselves (\"I'm Sarah\", \"this is Ben\"), or they are "
              "addressed by name and answer. Never guess. If unsure, use null. Answer with JSON only, like "
              "{\"Speaker 1\": \"Ben\", \"Speaker 2\": null}. Use the name as it is said in the transcript.")
    user = f"Speaker labels: {labels}\n\nTranscript:\n" + "\n".join(body)
    out = _generate(model, tok, system, user, 200)
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {}
    try:
        found = json.loads(m.group(0))
    except ValueError:
        return {}
    text = " ".join(ln[2] for ln in ctx.get("lines") or []).lower()
    res = {}
    for i, s in enumerate(sp):
        name = found.get(s["name"])
        if isinstance(name, str):
            name = name.strip().strip(".")
            if 1 < len(name) <= 40 and name.lower() in text and not name.lower().startswith("speaker"):
                res[i] = name
    return res
