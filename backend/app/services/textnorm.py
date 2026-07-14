"""Text normalization + character trigrams for phonetic/fuzzy retrieval.

The motivating case is phonetic input: a traveler hears "simida" and types it,
but the canonical entry is "seumnida" / "-습니다". Exact lexical search misses
this. We bridge the gap two ways:
  1. A light phonetic fold that collapses common romanization variation.
  2. Character trigrams, so partial overlap still scores.

Curated corpus entries additionally carry explicit mishearing aliases, which
are indexed here, so common cases match strongly while the PAW resolver
generalizes to unseen mishearings by proposing canonical forms to search.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text


# Ordered romanization folds applied to latin-script tokens. These are
# deliberately conservative approximations of Korean romanization variance.
_FOLDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"eu"), "u"),      # seu -> su
    (re.compile(r"eo"), "o"),      # seo -> so
    (re.compile(r"oo"), "u"),
    (re.compile(r"ee"), "i"),
    (re.compile(r"ph"), "p"),
    (re.compile(r"([a-z])\1"), r"\1"),  # collapse doubled letters
    (re.compile(r"mn"), "n"),      # seumnida <-> seunida <-> simida-ish
]


def phonetic_fold(token: str) -> str:
    t = token
    for pat, rep in _FOLDS:
        t = pat.sub(rep, t)
    return t


def fold_text(text: str) -> str:
    norm = normalize(text)
    out = []
    for tok in norm.split(" "):
        if tok.isascii():
            out.append(phonetic_fold(tok))
        else:
            out.append(tok)
    return " ".join(out)


# Backwards-compatible alias.
_fold_text = fold_text


def trigrams(text: str, *, fold: bool = True) -> set[str]:
    base = _fold_text(text) if fold else normalize(text)
    grams: set[str] = set()
    for token in base.split(" "):
        if not token:
            continue
        padded = f"  {token} "
        for i in range(len(padded) - 2):
            grams.add(padded[i : i + 3])
    return grams
