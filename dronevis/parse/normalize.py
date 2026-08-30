"""Text normalisation shared by the threat, geo and direction matchers.

We keep Cyrillic intact (no transliteration) but fold the noise that varies
between channels: emoji, bullet glyphs, Russian ``ё``/``ъ``, fancy quotes,
repeated whitespace. Ukrainian and Russian are handled together because the
four channels mix them freely.
"""

from __future__ import annotations

import re
import unicodedata

# Glyphs channels use as list bullets / status markers.
_BULLETS = "•·—–‣▪️▫️◦●○»«▶️➡️⤵️🅿️🔄🚀🏍🛸✈️💥🔥📍🚨⚠️❗️‼️"
_EMOJI_RE = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF" "\U0000FE00-\U0000FE0F" "\U000024C2" "]+",
    flags=re.UNICODE,
)
_WS_RE = re.compile(r"[ \t   ]+")
_MULTIDOT_RE = re.compile(r"\.{2,}")


def strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub(" ", text)


def normalize(text: str, *, keep_case: bool = False) -> str:
    """Canonical form for display / line splitting. Lower-cased unless
    ``keep_case``. Keeps the Ukrainian/Russian alphabets distinct."""
    text = unicodedata.normalize("NFKC", text)
    text = strip_emoji(text)
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = text.replace("“", '"').replace("”", '"')
    for b in _BULLETS:
        text = text.replace(b, " ")
    text = text.replace("/", " / ")
    text = _MULTIDOT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    if not keep_case:
        text = text.lower()
    return text.strip()


# Ukrainian <-> Russian letters that carry no distinguishing information for
# our keyword and toponym matching. Folding them lets one pattern cover both
# languages: "балістика"/"балистика" -> "балистика".
_FOLD_MAP = str.maketrans({
    "і": "и", "ї": "и", "ы": "и",
    "є": "е", "э": "е",
    "ґ": "г",
    "'": "", "ʼ": "", "ъ": "", "ь": "",
})


def fold(text: str) -> str:
    """Aggressive form used by every matcher (threats, geo, directions).

    Lower-cased, emoji-free, and with the uk/ru-only letters collapsed so a
    single vocabulary list covers both languages and most typos.
    """
    return normalize(text).translate(_FOLD_MAP)


_HARD_SPLIT = re.compile(r"[\r\n]+|🅿️|🔄|➡️|⤵️|•|●|▪️?|;| \+ | та (?=\d)")
# soft splitters used only inside a long run-on line (one channel writes whole
# situation reports as a single space-separated paragraph)
_SOFT_SPLIT = re.compile(
    r"\s{2,}"                      # collapsed newline -> double space
    r"|(?<=[.!?])\s+"              # sentence end
    r"|,\s+(?=\d)"                 # ", 2 реактивних ..." -> new item
    r"|\s+(?=курс\w*\s+(?:на|в)\b)"  # "... курсом на ..." restart
)


def split_lines(text: str) -> list[str]:
    """Break a post into candidate observation lines.

    Channels put one object per line / bullet / sentence. One channel writes
    long single-paragraph reports, so long chunks get a second, softer split.
    """
    out: list[str] = []
    for chunk in _HARD_SPLIT.split(text):
        chunk = (chunk or "").strip(" -–—:；;")
        if not chunk:
            continue
        if len(chunk) > 130:
            out.extend(s.strip() for s in _SOFT_SPLIT.split(chunk) if s and s.strip())
        else:
            out.append(chunk)
    return out or ([text.strip()] if text.strip() else [])


_COUNT_RE = re.compile(
    r"(?:^|\s)(\d{1,3})\s*(?:x|х|шт|штук|од|einheiten)?\.?(?=\s|$|[а-яіїєґ])", re.I
)


def extract_count(line: str) -> int | None:
    """Leading quantity: '2х', '3 шахеди', '1x Shahed'."""
    m = _COUNT_RE.search(" " + line.strip())
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if 1 <= n <= 200 else None
