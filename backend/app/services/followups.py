"""Explicit, bounded context for answer-anchored follow-up questions."""

from __future__ import annotations

MAX_PREVIOUS_QUESTION_CHARS = 1_200
MAX_PREVIOUS_ANSWER_CHARS = 1_800


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def _clip(value: str, limit: int) -> str:
    clean = _clean(value)
    if len(clean) <= limit:
        return clean
    return f"{clean[: limit - 1].rstrip()}…"


def standalone(text: str) -> dict:
    return {
        "query": _clean(text),
        "used_context": False,
        "previous_question": None,
        "previous_answer": None,
        "strategy": "standalone",
    }


def rewrite(
    text: str,
    *,
    previous_question: str,
    previous_answer: str,
) -> dict:
    clean = _clean(text)
    question = _clip(previous_question, MAX_PREVIOUS_QUESTION_CHARS)
    answer = _clip(previous_answer, MAX_PREVIOUS_ANSWER_CHARS)
    query = (
        "Use the immediate context below to answer only the follow-up.\n"
        f"PREVIOUS_QUESTION: {question}\n"
        f"PREVIOUS_ANSWER: {answer}\n"
        f"FOLLOW_UP: {clean}"
    )
    return {
        "query": query,
        "used_context": True,
        "previous_question": question,
        "previous_answer": answer,
        "strategy": "structured_context",
    }
