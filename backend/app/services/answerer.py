"""The offline answer graph: exactly one final author per path.

    route -> resolve phonetic candidates -> answer card? -> retrieve
          -> one evidence answerer -> abstain + queue if insufficient

Token budgeting is deliberate: we rank chunks, cap per-source length, and cap
the number of sources so the structured input fits the ~2048-token window
alongside the compiled prefix and reserved output tokens.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone

from . import paw_experts, retrieval, router, support


def _paw_gates_enabled() -> bool:
    return os.environ.get("PREPARE_OFFLINE_USE_PAW_GATES", "0") == "1"

MAX_SOURCES = 3
MAX_SNIPPET_CHARS = 480
RETRIEVAL_FLOOR = 0.28
# Only feed the answerer sources within this fraction of the top score, so a
# single strong match is not diluted by weaker, near-miss context.
RELEVANCE_BAND = 0.9
# A single Tier-2 (structured fact / dictionary) source at least this strong is
# rendered deterministically instead of trusting the 0.6B model to paraphrase
# it. Safe because RETRIEVAL_FLOOR already gates out unanswerable queries
# (which score ~0.15). The model is reserved for multi-source synthesis.
STRONG_SINGLE = 0.4
UNSUPPORTED = "UNSUPPORTED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snippet(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_SNIPPET_CHARS else text[:MAX_SNIPPET_CHARS] + " ..."


def _build_prompt(question: str, cands: list[retrieval.Candidate]) -> str:
    lines = [f"QUESTION:\n{question}\n"]
    for i, c in enumerate(cands):
        label = chr(ord("A") + i)
        lines.append(f"SOURCE {label} [{c.source_id}]:\n{_snippet(c.text)}\n")
    lines.append(
        "Answer the question in one or two sentences using the sources above. "
        f"Only if none of the sources are relevant, reply exactly {UNSUPPORTED}."
    )
    return "\n".join(lines)


def _relevant(cands: list[retrieval.Candidate]) -> list[retrieval.Candidate]:
    if not cands:
        return []
    top = cands[0].score
    return [c for c in cands if c.score >= top * RELEVANCE_BAND][:MAX_SOURCES]


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _best_sentences(query: str, text: str, k: int = 2) -> str | None:
    """Extract the sentence(s) in a raw passage that best match the query's
    content tokens. Used for Tier-3 clips so page Q&A does not depend on the
    tiny model composing an answer."""
    from .textnorm import fold_text, phonetic_fold

    toks = [phonetic_fold(t) for t in retrieval._strip_stopwords(query).split() if len(t) >= 4]
    if not toks:
        return None
    scored: list[tuple[int, str]] = []
    for sent in _SENT_SPLIT.split(text):
        s = sent.strip()
        if not s:
            continue
        hay = fold_text(s)
        score = sum(1 for t in toks if t in hay)
        if score:
            scored.append((score, s))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for _, s in scored[:k]]
    return " ".join(top)


def _lexical_grounded(query: str, cand: retrieval.Candidate) -> bool:
    """Guard against spurious single-token collisions (e.g. 'who won the world
    cup' matching the currency 'won'). Require query content tokens to actually
    appear in the source text/title/aliases, with phonetic folding so that
    mishearings still match."""
    from .textnorm import fold_text, normalize, phonetic_fold

    aliases = " ".join(cand.meta.get("aliases", [])) if cand.meta else ""
    hay = fold_text(f"{cand.title} {cand.text} {aliases}")
    toks = [phonetic_fold(t) for t in retrieval._strip_stopwords(query).split() if len(t) >= 4]
    if not toks:
        q = phonetic_fold(normalize(query).replace(" ", ""))
        return bool(q) and q in hay.replace(" ", "")
    matches = sum(1 for t in toks if t in hay)
    required = 1 if len(toks) <= 1 else 2
    return matches >= required


def _enqueue(conn: sqlite3.Connection, pack_id: str | None, question: str,
             answer: str | None, support_level: str | None,
             sources: list[str], conversation_id: str | None = None,
             message_id: str | None = None) -> None:
    import json

    conn.execute(
        "INSERT INTO question_queue (pack_id, question, offline_answer, "
        "offline_support, offline_sources, status, created_at, conversation_id, "
        "message_id) VALUES (?,?,?,?,?, 'pending', ?,?,?)",
        (pack_id, question, answer, support_level,
         json.dumps(sources, ensure_ascii=False), _now(), conversation_id, message_id),
    )
    conn.commit()


def _source_refs(cands: list[retrieval.Candidate]) -> list[dict]:
    return [
        {
            "source_id": c.source_id,
            "title": c.title,
            "snippet": _snippet(c.text)[:200],
            "publisher": c.meta.get("publisher"),
            "as_of": c.as_of or c.meta.get("retrieved_at"),
            "freshness_class": c.meta.get("freshness_class"),
            "freshness": c.meta.get("freshness_state")
            or c.meta.get("freshness_class"),
            "expires_at": c.meta.get("expires_at"),
        }
        for c in cands
    ]


def _source_refs_for_ids(
    conn: sqlite3.Connection, pack_id: str, source_ids: list[str]
) -> list[dict]:
    refs: list[dict] = []
    for source_id in source_ids:
        row = conn.execute(
            "SELECT title, text, as_of, meta FROM documents WHERE pack_id=? AND source_id=? "
            "LIMIT 1",
            (pack_id, source_id),
        ).fetchone()
        meta = {}
        if row:
            try:
                import json

                meta = json.loads(row["meta"])
            except (json.JSONDecodeError, TypeError):
                meta = {}
        refs.append(
            {
                "source_id": source_id,
                "title": row["title"] if row else source_id,
                "snippet": _snippet(row["text"])[:200] if row else "",
                "publisher": meta.get("publisher"),
                "as_of": (row["as_of"] if row else None) or meta.get("retrieved_at"),
                "freshness_class": meta.get("freshness_class"),
                "freshness": meta.get("freshness_state")
                or meta.get("freshness_class"),
                "expires_at": meta.get("expires_at"),
            }
        )
    return refs


def answer_candidates(
    question: str, candidates: list[retrieval.Candidate]
) -> dict:
    """Write one grounded answer from already-routed candidates."""
    from .interpreter import get_interpreter

    if not candidates:
        return {
            "answer": "I could not find this in your offline trip.",
            "support": "low",
            "answer_mode": "abstained",
            "sources": [],
            "stale": False,
        }
    candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
    best = candidates[0]
    used = _relevant(candidates)
    if (
        len(used) == 1
        and used[0].tier == 2
        and used[0].score >= STRONG_SINGLE
        and _lexical_grounded(question, used[0])
    ):
        item = used[0]
        return {
            "answer": item.text.strip(),
            "support": "high" if item.score >= 0.7 else "medium",
            "answer_mode": "structured_fact",
            "sources": _source_refs(used),
            "stale": not item.stable,
        }
    if used and used[0].tier == 3 and _lexical_grounded(question, used[0]):
        extracted = _best_sentences(question, used[0].text)
        if extracted:
            return {
                "answer": extracted,
                "support": "high" if used[0].score >= 0.7 else "medium",
                "answer_mode": "structured_fact",
                "sources": _source_refs(used[:1]),
                "stale": not used[0].stable,
            }
    prompt = _build_prompt(question, used)
    interpreter = get_interpreter()
    raw = interpreter.answer(prompt) if interpreter.is_available() else UNSUPPORTED
    if not raw or raw.strip().upper().startswith("UNSUP"):
        return {
            "answer": "The offline sources do not clearly answer this.",
            "support": "low",
            "answer_mode": "abstained",
            "sources": _source_refs(used),
            "stale": any(not item.stable for item in used),
        }
    return {
        "answer": raw,
        "support": support.support_for_generated(best),
        "answer_mode": "generated_from_local_sources",
        "sources": _source_refs(used),
        "stale": any(not item.stable for item in used),
    }


def answer_question(
    conn: sqlite3.Connection,
    pack_id: str,
    question: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    enqueue: bool = True,
) -> dict:
    from .interpreter import get_interpreter

    r = router.route(question)

    extra_terms: list[str] = []
    if r.use_resolver:
        extra_terms = paw_experts.resolve_query_candidates(conn, pack_id, question)

    # Tier 1: precomputed answer card.
    card = retrieval.match_answer_card(conn, pack_id, question, extra_terms=extra_terms)
    if card is not None:
        return {
            "answer": card["answer"],
            "support": support.support_for_card(),
            "answer_mode": "answer_card",
            "sources": _source_refs_for_ids(conn, pack_id, card["sources"]),
            "stale": not card["stable"],
            "queued_for_verification": False,
            "expert_used": "heard_expression_resolver" if extra_terms else None,
            "debug": {"extra_terms": extra_terms, "card_score": round(card["score"], 3)},
        }

    # Tier 2/3: retrieve evidence.
    cands = retrieval.search(conn, pack_id, question, extra_terms=extra_terms,
                             limit=MAX_SOURCES)
    best = cands[0] if cands else None

    if best is None or best.score < RETRIEVAL_FLOOR:
        if enqueue:
            _enqueue(
                conn,
                pack_id,
                question,
                None,
                "low",
                [],
                conversation_id,
                message_id,
            )
        return {
            "answer": "I could not find this in your offline pack. I've saved "
                      "the question to verify when you're back online.",
            "support": "low",
            "answer_mode": "abstained",
            "sources": [],
            "stale": False,
            "queued_for_verification": enqueue,
            "expert_used": "heard_expression_resolver" if extra_terms else None,
            "debug": {"extra_terms": extra_terms,
                      "best_score": round(best.score, 3) if best else None},
        }

    used = _relevant(cands)

    # Deterministic rendering for a single strong Tier-2 (dictionary/fact) hit:
    # most reliable, and avoids depending on the tiny model to restate a fact.
    if (len(used) == 1 and used[0].tier == 2 and used[0].score >= STRONG_SINGLE
            and _lexical_grounded(question, used[0])):
        c = used[0]
        return {
            "answer": c.text.strip(),
            "support": "high" if c.score >= 0.7 else "medium",
            "answer_mode": "structured_fact",
            "sources": _source_refs(used),
            "stale": not c.stable,
            "queued_for_verification": False,
            "expert_used": "heard_expression_resolver" if extra_terms else None,
            "debug": {"extra_terms": extra_terms, "best_score": round(c.score, 3)},
        }

    # Tier-3 raw clips (e.g. saved web pages): extract the best-matching
    # sentence(s) deterministically rather than trusting the model on long text.
    if used and used[0].tier == 3 and _lexical_grounded(question, used[0]):
        extracted = _best_sentences(question, used[0].text)
        if extracted:
            return {
                "answer": extracted,
                "support": "high" if used[0].score >= 0.7 else "medium",
                "answer_mode": "structured_fact",
                "sources": _source_refs(used[:1]),
                "stale": not used[0].stable,
                "queued_for_verification": False,
                "expert_used": "heard_expression_resolver" if extra_terms else None,
                "debug": {"extra_terms": extra_terms, "best_score": round(used[0].score, 3)},
            }

    # Single evidence-grounded author, fed only the strongest sources.
    prompt = _build_prompt(question, used)
    interp = get_interpreter()
    raw = interp.answer(prompt) if interp.is_available() else UNSUPPORTED
    abstained = (not raw) or raw.strip().upper().startswith("UNSUP")

    # Opt-in PAW sufficiency gate (bounded memory; off by default). When the
    # model produced an answer, a compiled sufficiency classifier can veto it.
    if not abstained and _paw_gates_enabled():
        sources_text = " ".join(_snippet(c.text) for c in used)
        verdict = paw_experts.run_sufficiency(conn, pack_id, question, sources_text)
        if verdict is False:
            abstained = True

    if abstained:
        if enqueue:
            _enqueue(conn, pack_id, question, None, "low",
                     [c.source_id for c in used], conversation_id, message_id)
        return {
            "answer": "The offline sources don't clearly answer this. Saved to "
                      "verify when you're back online.",
            "support": "low",
            "answer_mode": "abstained",
            "sources": _source_refs(used),
            "stale": any(not c.stable for c in used),
            "queued_for_verification": enqueue,
            "expert_used": "heard_expression_resolver" if extra_terms else None,
            "debug": {"extra_terms": extra_terms,
                      "best_score": round(best.score, 3)},
        }

    level = support.support_for_generated(best)
    stale = any(not c.stable for c in used)
    queue = support.should_queue(level, abstained=False)
    if queue and enqueue:
        _enqueue(conn, pack_id, question, raw, level,
                 [c.source_id for c in used], conversation_id, message_id)

    return {
        "answer": raw,
        "support": level,
        "answer_mode": "generated_from_local_sources",
        "sources": _source_refs(used),
        "stale": stale,
        "queued_for_verification": queue and enqueue,
        "expert_used": "heard_expression_resolver" if extra_terms else None,
        "debug": {"extra_terms": extra_terms, "best_score": round(best.score, 3)},
    }
