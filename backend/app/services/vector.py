"""Optional hybrid/vector retrieval (Phase 4, eval-gated).

Per the plan and the evaluation results, vector retrieval is NOT enabled by
default: on the current pack, FTS5 + trigram + deterministic Tier-2 rendering
already reaches 100% grounded accuracy, so adding an embedding model would only
cost memory and startup time without improving answers.

This module exists so hybrid retrieval can be switched on if a future, larger
corpus shows a need. It uses llama.cpp embeddings (no torch) over a GGUF
embedding model configured via PREPARE_OFFLINE_EMBED_GGUF, and reranks the
lexical/trigram candidates by cosine similarity. If no embedding model is
configured it is a no-op and the caller keeps the lexical ranking.
"""

from __future__ import annotations

import math
import os
import threading

_llm = None
_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.environ.get("PREPARE_OFFLINE_EMBED_GGUF"))


def _ensure_model():
    global _llm
    if _llm is not None:
        return _llm
    with _lock:
        if _llm is not None:
            return _llm
        path = os.environ.get("PREPARE_OFFLINE_EMBED_GGUF")
        if not path:
            return None
        from llama_cpp import Llama

        _llm = Llama(model_path=path, embedding=True, n_ctx=2048, verbose=False)
        return _llm


def _embed(text: str) -> list[float] | None:
    llm = _ensure_model()
    if llm is None:
        return None
    out = llm.create_embedding(text)
    return out["data"][0]["embedding"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rerank(query: str, candidates: list, *, weight: float = 0.4) -> list:
    """Blend a cosine-similarity signal into candidate scores. No-op unless an
    embedding model is configured."""
    if not enabled() or not candidates:
        return candidates
    q = _embed(query)
    if q is None:
        return candidates
    for c in candidates:
        emb = _embed(f"{c.title} {c.text}")
        if emb is None:
            continue
        sim = _cosine(q, emb)
        c.score = (1 - weight) * c.score + weight * sim
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
