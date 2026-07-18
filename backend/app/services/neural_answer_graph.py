"""Broad + top-k specialist PAW graph with one final PAW aggregation."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator

from . import program_registry, program_runtime

MAX_SPECIALISTS = 3
MAX_CANDIDATE_CHARS = 1200


def _explicit_topic_mention(topic: str, question: str) -> bool:
    normalized_topic = " ".join(topic.casefold().split())
    normalized_question = " ".join(question.casefold().split())
    if not normalized_topic:
        return False
    return bool(
        re.search(
            rf"(?<!\w){re.escape(normalized_topic)}(?!\w)",
            normalized_question,
        )
    )


def _run(program: dict, text: str, *, max_tokens: int = 320) -> dict:
    result = program_runtime.run(
        program["program_id"],
        text,
        max_tokens=max_tokens,
        timeout_seconds=45,
    )
    return {
        "role": program["role"],
        "program_key": program["program_key"],
        "program_id": program["program_id"],
        "output": result.output,
        "elapsed_ms": result.elapsed_ms,
        "peak_rss_mb": result.peak_rss_mb,
        "isolated": result.isolated,
    }


def _matching_prepared(
    conn: sqlite3.Connection, question: str
) -> tuple[list[str], list[dict]]:
    prepared_labels = []
    matcher_results = []
    matcher = program_registry.active(conn, "prepared_matcher")
    for program in program_registry.prepared_for_router(conn):
        if _explicit_topic_mention(program["topic"], question):
            matcher_results.append(
                {
                    "program_key": program["program_key"],
                    "output": "YES (explicit topic mention)",
                    "elapsed_ms": 0.0,
                    "peak_rss_mb": 0.0,
                }
            )
            prepared_labels.append(program["program_key"])
            continue
        if matcher:
            match = _run(
                matcher,
                f"TOPIC: {program['topic']}\nQUESTION: {question}",
                max_tokens=8,
            )
            matcher_results.append(
                {
                    "program_key": program["program_key"],
                    "output": match["output"],
                    "elapsed_ms": match["elapsed_ms"],
                    "peak_rss_mb": match["peak_rss_mb"],
                }
            )
            if match["output"].strip().casefold().startswith("yes"):
                prepared_labels.append(program["program_key"])
    return prepared_labels[:MAX_SPECIALISTS], matcher_results


def _candidate_program(conn: sqlite3.Connection, label: str) -> dict | None:
    program = program_registry.get(conn, label)
    if program and program["role"] == "prepared_topic" and program["status"] == "ready":
        return program
    return None


def _language_candidate(
    conn: sqlite3.Connection, question: str
) -> tuple[dict | None, dict | None]:
    lowered = question.casefold()
    cues = (
        "say ",
        "translate",
        " mean",
        "means",
        "heard",
        "hearing",
        "word",
        "phrase",
    )
    if not any(cue in lowered for cue in cues):
        return None, None
    intent = program_registry.active(conn, "language_intent")
    if not intent:
        return None, None
    intent_result = _run(intent, question, max_tokens=8)
    label = intent_result["output"].strip().upper()
    role = {
        "HEARD_EXPRESSION": "heard_expression",
        "TRANSLATION": "translation",
    }.get(label)
    if not role:
        return None, intent_result
    program = program_registry.active(conn, role)
    if not program:
        return None, intent_result
    return _run(program, question), intent_result


def _aggregate(question: str, candidates: list[dict], aggregator: dict) -> dict:
    lines = [f"QUESTION: {question}"]
    for candidate in candidates:
        label = candidate["role"].replace("subject:", "")
        output = candidate["output"][:MAX_CANDIDATE_CHARS]
        lines.append(f"CANDIDATE {label}: {output}")
    return _run(aggregator, "\n".join(lines), max_tokens=360)


def answer_events(conn: sqlite3.Connection, question: str) -> Iterator[dict]:
    broad = program_registry.active(conn, "broad")
    if not broad:
        raise RuntimeError("Broad PAW answerer is not installed")
    broad_result = _run(broad, question)
    yield {
        "type": "answer_update",
        "stage": "broad",
        "answer": broad_result["output"],
        "status": "Thinking…",
    }

    labels, matcher_results = _matching_prepared(conn, question)
    yield {"type": "route", "labels": labels}
    prepared_candidates = []
    for label in labels:
        program = _candidate_program(conn, label)
        if not program:
            continue
        yield {"type": "specialist_started", "label": label}
        try:
            candidate = _run(program, question)
        except Exception as exc:
            yield {
                "type": "specialist_failed",
                "label": label,
                "error": str(exc),
            }
            continue
        prepared_candidates.append(candidate)
        yield {"type": "specialist_complete", "label": label}

    language_result = None
    language_intent_result = None
    if not prepared_candidates:
        language_result, language_intent_result = _language_candidate(
            conn, question
        )

    aggregator = program_registry.active(conn, "aggregator")
    final_result = broad_result
    aggregate_result = None
    if len(prepared_candidates) == 1:
        final_result = prepared_candidates[0]
    elif aggregator and len(prepared_candidates) > 1:
        try:
            aggregate_result = _aggregate(
                question,
                prepared_candidates,
                aggregator,
            )
            final_result = aggregate_result
        except Exception:
            final_result = prepared_candidates[0]
    elif len(prepared_candidates) > 1:
        final_result = prepared_candidates[0]
    elif language_result:
        final_result = language_result

    critic_result = None
    revision_result = None
    if os.environ.get("PFO_USE_CRITIC") == "1":
        critic = program_registry.active(conn, "critic")
        revision = program_registry.active(conn, "revision")
        if critic and revision:
            critic_result = _run(
                critic,
                f"QUESTION: {question}\nANSWER: {final_result['output']}",
                max_tokens=120,
            )
            if critic_result["output"].casefold().startswith("revise:"):
                revision_result = _run(
                    revision,
                    f"QUESTION: {question}\n"
                    f"ANSWER: {final_result['output']}\n"
                    f"CRITIC: {critic_result['output']}",
                    max_tokens=360,
                )
                final_result = revision_result

    trace = {
        "route": labels,
        "programs": [
            {
                "role": candidate["role"],
                "program_key": candidate["program_key"],
                "program_id": candidate["program_id"],
                "elapsed_ms": candidate["elapsed_ms"],
                "peak_rss_mb": candidate["peak_rss_mb"],
            }
            for candidate in [
                broad_result,
                *prepared_candidates,
                *([language_result] if language_result else []),
            ]
        ],
        "language_intent": language_intent_result,
        "prepared_matcher": matcher_results,
        "aggregator": {
            key: aggregate_result.get(key)
            for key in ("program_id", "elapsed_ms", "peak_rss_mb")
        }
        if aggregate_result
        else None,
        "critic": critic_result,
        "revision": revision_result,
    }
    yield {
        "type": "final",
        "answer": final_result["output"],
        "program_labels": labels
        + ([language_result["role"]] if language_result else []),
        "trace": trace,
        "refined": final_result["output"] != broad_result["output"],
    }
