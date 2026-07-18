"""Offline factual packs: curated, source-backed answers for structured facts.

A prepared PAW adapter over the frozen 0.6B interpreter is not a reliable fact
store; it produced "major cities of South Korea: Seoul, Gangnam, Incheon, Gimpo."
Factual packs are curated JSON knowledge with provenance that answer structured
questions deterministically and offline, taking precedence over the neural
answerer when a curated fact matches. This is the internal factual layer; the
visible UI stays Ask and Prepare.

Built-in packs live beside this module. Optional user packs (built by Prepare in
a later phase) load from ``<home>/factual_packs`` and an env override.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

BUILTIN_DIR = Path(__file__).with_name("factual_packs")
PACK_SCHEMA_VERSION = "factual-pack/v1"
_SPACE_RE = re.compile(r"\s+")
_MIN_SCORE = 1.0
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "is", "are", "was", "were", "do", "does", "did",
        "what", "which", "who", "whom", "in", "on", "at", "for", "to", "and",
        "or", "that", "this", "it", "its", "how", "i", "me", "my", "you", "your",
        "they", "them", "about", "have", "has", "had", "with", "there", "here",
        "s",
    }
)


def _content_tokens(tokens: set[str]) -> set[str]:
    return {token for token in tokens if token not in _STOPWORDS}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    chars = [ch if ch.isalnum() else " " for ch in value]
    return _SPACE_RE.sub(" ", "".join(chars)).strip()


def _contains(haystack_norm: str, needle: str) -> bool:
    needle_norm = _normalize(needle)
    if not needle_norm:
        return False
    return f" {needle_norm} " in f" {haystack_norm} "


def _pack_dirs() -> list[Path]:
    dirs = [BUILTIN_DIR]
    override = os.environ.get("PREPARE_OFFLINE_FACTUAL_PACKS_PATH", "")
    if override:
        dirs.append(Path(override).expanduser())
    home = os.environ.get("PREPARE_OFFLINE_HOME", "")
    if home:
        dirs.append(Path(home).expanduser() / "factual_packs")
    else:
        dirs.append(Path.home() / ".prepare_offline" / "factual_packs")
    return dirs


def _valid_pack(document: Any) -> bool:
    return (
        isinstance(document, dict)
        and document.get("schema_version") == PACK_SCHEMA_VERSION
        and isinstance(document.get("pack_key"), str)
        and isinstance(document.get("facts"), list)
    )


@lru_cache(maxsize=1)
def _load_packs() -> tuple[dict[str, Any], ...]:
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in _pack_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not _valid_pack(document) or document["pack_key"] in seen:
                continue
            seen.add(document["pack_key"])
            packs.append(document)
    return tuple(packs)


def reload_packs() -> None:
    """Drop the cache so newly prepared packs are visible."""

    _load_packs.cache_clear()


def _entity_mentioned(pack: dict[str, Any], question_norm: str) -> bool:
    for entity in pack.get("entities", []):
        names = [entity.get("canonical", ""), *entity.get("aliases", [])]
        if any(_contains(question_norm, name) for name in names if name):
            return True
    return False


def _fact_score(
    fact: dict[str, Any], question_norm: str, question_tokens: set[str], entity: bool
) -> float:
    score = 0.0
    for trigger in fact.get("triggers", []):
        trigger_norm = _normalize(trigger)
        if not trigger_norm:
            continue
        if f" {trigger_norm} " in f" {question_norm} ":
            score = max(score, 2.0 + len(trigger_norm) / 100.0)
            continue
        trigger_content = _content_tokens(set(trigger_norm.split()))
        if not trigger_content:
            continue
        question_content = _content_tokens(question_tokens)
        coverage = len(trigger_content & question_content) / len(trigger_content)
        if entity and coverage >= 0.8:
            jaccard = len(trigger_content & question_content) / len(
                trigger_content | question_content
            )
            score = max(score, 1.0 + jaccard)
    return score


def lookup(question: str) -> dict[str, Any] | None:
    """Return a curated grounded answer for ``question``, or None."""

    question_norm = _normalize(question)
    if not question_norm:
        return None
    question_tokens = set(question_norm.split())
    best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
    for pack in _load_packs():
        entity = _entity_mentioned(pack, question_norm)
        for fact in pack["facts"]:
            score = _fact_score(fact, question_norm, question_tokens, entity)
            if score < _MIN_SCORE:
                continue
            if best is None or score > best[0]:
                best = (score, pack, fact)
    if best is None:
        return None
    _, pack, fact = best
    return {
        "answer": fact["answer"],
        "pack_key": pack["pack_key"],
        "pack_title": pack.get("title", pack["pack_key"]),
        "fact_id": fact.get("id", ""),
        "family": fact.get("family", ""),
        "as_of": fact.get("as_of") or pack.get("as_of"),
        "sources": fact.get("sources") or pack.get("sources", []),
        "support": "prepared_facts",
    }


def available_packs() -> list[dict[str, Any]]:
    return [
        {
            "pack_key": pack["pack_key"],
            "title": pack.get("title", pack["pack_key"]),
            "as_of": pack.get("as_of"),
            "fact_count": len(pack["facts"]),
        }
        for pack in _load_packs()
    ]
