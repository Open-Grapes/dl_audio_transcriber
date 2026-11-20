"""Hanyu Pinyin with tone marks for Chinese text (pypinyin, MIT), shown under Chinese lines when "Pinyin" is
ticked. One syllable per character; punctuation, numbers and English words are kept."""

import re

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_PUNCT = {"，": ",", "。": ".", "！": "!", "？": "?", "、": ",", "；": ";", "：": ":", "（": "(", "）": ")",
          "“": '"', "”": '"', "‘": "'", "’": "'", "《": "«", "》": "»", "…": "…", "——": "—", "「": '"', "」": '"'}


def available():
    try:
        import pypinyin  # noqa: F401
        return True
    except Exception:
        return False


def has_han(text):
    return bool(text) and HAN.search(text) is not None


def line(text):
    """'保护自己，远离Ah Choo。' -> 'bǎo hù zì jǐ, yuǎn lí Ah Choo.' (one syllable per character, so it lines up
    with the characters; heteronyms like 银行 / 行走 are read by word)."""
    if not has_han(text):
        return ""
    try:
        from pypinyin import Style, lazy_pinyin
    except Exception:
        return ""
    out = ""
    for run in re.findall(r"[㐀-䶿一-鿿豈-﫿]+|[^㐀-䶿一-鿿豈-﫿]+",
                          text):
        if has_han(run):
            syl = " ".join(lazy_pinyin(run, style=Style.TONE))
            if out and not out.endswith((" ", "(", "«", '"', "'")):
                out += " "
            out += syl
            continue
        t = "".join(_PUNCT.get(c, c) for c in run).strip()
        if not t:
            continue
        # punctuation and closing quotes stick to the syllable before them
        while t and (t[0] in ",.!?;:)»…" or (t[0] in "\"'" and out.count(t[0]) % 2 == 1)):
            out = out.rstrip() + t[0] + " "
            t = t[1:].strip()
        if not t:
            continue
        out += (" " if out and not out.endswith(" ") else "") + t
    return re.sub(r"\s+", " ", out).strip()
