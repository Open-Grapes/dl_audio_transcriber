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
    return "cuda" if cuda.gpu()[0] else "cpu"


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
        ct = "int8_float16" if dev == "cuda" else "int8"
        path = str(models.path(key))
        cls = ctranslate2.Generator if key == AI_KEY else ctranslate2.Translator
        try:
            obj = cls(path, device=dev, compute_type=ct)
        except Exception as e:
            if dev != "cuda":
                raise
            log(f"{key} on GPU failed ({e}); using the CPU")
            dev = "cpu"
            obj = cls(path, device=dev, compute_type="int8")
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
    batch = 16 if eng == "nllb" else (8 if dev == "cuda" else 2)
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
    budget = 12000 if dev == "cuda" else 3500          # tokens per part (CPU: smaller parts stay quick)
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
            notes.append(_generate(model, tok, sys_, p, 700))
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
    out = _generate(model, tok, system, user, 1500, on_text, cancel)
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


def _generate(model, tok, system, user, max_new, on_text=None, cancel=None):
    prompt = _chat_tokens(tok, system, user)
    ids = []
    last = 0
    for step in model.generate_tokens(prompt, max_length=max_new, sampling_topk=1,
                                      end_token=["<|im_end|>", "<|endoftext|>"], repetition_penalty=1.05):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        ids.append(step.token_id)
        if on_text and len(ids) - last >= 8:
            last = len(ids)
            on_text(tok.decode(ids, skip_special_tokens=True))
    text = tok.decode(ids, skip_special_tokens=True).strip()
    if on_text:
        on_text(text)
    return text
