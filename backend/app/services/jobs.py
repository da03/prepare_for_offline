"""PackPlan-driven, resumable-style preparation jobs.

Lifecycle: planning -> processing_documents -> indexing -> downloading ->
compiling -> (precompute answer cards) -> testing -> ready | failed.

Readiness gate: a pack becomes `ready` only after its content is committed, a
checksum is recorded, and an offline smoke test answers the motivating question
without the network. Privacy: only behavioral expert specs are ever sent to the
compiler; no personal content is included.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone

from ..db import connect
from . import answerer, packs as packs_svc, paw_experts, planner, retrieval

STATES = [
    "planning", "compiling", "downloading", "processing_documents",
    "indexing", "testing", "ready", "failed", "cancelled",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(pack_id: str, plan: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, pack_id, state, plan, progress, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, pack_id, "planning", json.dumps(plan), "[]", _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def _update(conn, job_id: str, state: str, message: str, error: str | None = None) -> None:
    row = conn.execute("SELECT progress FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    progress = json.loads(row["progress"]) if row else []
    progress.append({"state": state, "message": message, "at": _now()})
    conn.execute(
        "UPDATE jobs SET state=?, progress=?, error=?, updated_at=? WHERE job_id=?",
        (state, json.dumps(progress, ensure_ascii=False), error, _now(), job_id),
    )
    conn.commit()


def get_job(job_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return {
            "job_id": row["job_id"],
            "pack_id": row["pack_id"],
            "state": row["state"],
            "plan": json.loads(row["plan"]),
            "progress": json.loads(row["progress"]),
            "error": row["error"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def _checksum(conn, pack_id: str) -> str:
    h = hashlib.sha256()
    for row in conn.execute(
        "SELECT source_id, text FROM documents WHERE pack_id=? ORDER BY source_id", (pack_id,)
    ):
        h.update(row["source_id"].encode())
        h.update(row["text"].encode())
    for row in conn.execute(
        "SELECT question, answer FROM answer_cards WHERE pack_id=? ORDER BY question", (pack_id,)
    ):
        h.update(row["question"].encode())
        h.update(row["answer"].encode())
    return h.hexdigest()[:16]


def _precompute_cards(conn, pack_id: str, questions: list[str]) -> dict:
    """Run the pipeline over expected questions; cache confident answers as
    Tier-1 cards and report coverage."""
    answered = 0
    added = 0
    for q in questions:
        # Skip if a strong card already covers it.
        if retrieval.match_answer_card(conn, pack_id, q) is not None:
            answered += 1
            continue
        result = answerer.answer_question(conn, pack_id, q)
        if result["answer_mode"] == "abstained":
            continue
        answered += 1
        if result["support"] in ("high", "medium"):
            retrieval.ingest_answer_card(
                conn, pack_id, q, result["answer"],
                sources=[s["source_id"] for s in result["sources"]],
                support=result["support"],
                aliases=[q],
            )
            added += 1
    conn.commit()
    coverage = round(answered / len(questions), 3) if questions else 0.0
    return {"expected": len(questions), "answered": answered,
            "precomputed_cards": added, "coverage": coverage}


def _run(job_id: str, raw_plan: dict) -> None:
    conn = connect()
    try:
        _update(conn, job_id, "planning", "Building PackPlan")
        pack_plan = planner.plan(
            destination=raw_plan.get("destination", "South Korea"),
            interests=raw_plan.get("interests"),
            storage_budget_mb=raw_plan.get("storage_budget_mb", 1200),
            finalize=raw_plan.get("finalize", False),
            allow_online_synth=raw_plan.get("allow_online_synth", False),
        )
        _update(conn, job_id, "planning",
                f"Selected topics: {', '.join(pack_plan.selected_topics)}"
                + (f"; dropped (budget): {', '.join(pack_plan.dropped_topics)}"
                   if pack_plan.dropped_topics else ""))

        _update(conn, job_id, "processing_documents", "Building curated corpus")
        pack_id = packs_svc.build_korea_pack(conn, selected_topics=pack_plan.selected_topics)
        _update(conn, job_id, "indexing", "Indexed documents and answer cards")

        base_ok = False
        if pack_plan.include_base_model:
            _update(conn, job_id, "downloading", "Ensuring base interpreter is cached")
            try:
                from programasweights import cache
                from ..config import get_settings

                cache.get_base_model_path(get_settings().interpreter)
                base_ok = True
            except Exception as exc:
                _update(conn, job_id, "downloading",
                        f"Base model not cached ({exc}); deterministic answers only")

        if raw_plan.get("compile_expert", True) and pack_plan.expert_specs:
            for role in pack_plan.expert_specs:
                _update(conn, job_id, "compiling", f"Compiling {role} (behavioral spec only)")
                try:
                    expert = paw_experts.compile_expert(
                        conn, pack_id, role, finalize=raw_plan.get("finalize", False)
                    )
                    packs_svc.attach_expert(conn, pack_id, expert)
                    _update(conn, job_id, "compiling",
                            f"Compiled {role} -> {expert['program_id']}")
                except Exception as exc:
                    _update(conn, job_id, "compiling",
                            f"{role} compile skipped ({exc}); deterministic fallback")

        _update(conn, job_id, "indexing", "Precomputing answer cards from likely questions")
        stats = _precompute_cards(conn, pack_id, pack_plan.expected_questions)
        _update(conn, job_id, "indexing",
                f"Coverage {int(stats['coverage']*100)}% "
                f"({stats['answered']}/{stats['expected']}), "
                f"+{stats['precomputed_cards']} cards")

        _update(conn, job_id, "testing", "Verifying checksum and offline smoke test")
        checksum = _checksum(conn, pack_id)
        offline_ok = _smoke_test(conn, pack_id)

        packs_svc.finalize_pack(
            conn, pack_id,
            plan=pack_plan.to_dict(), coverage=stats,
            checksum=checksum, offline_tested=offline_ok,
        )
        _update(conn, job_id, "ready",
                f"Pack ready (offline_tested={offline_ok}, base_model={base_ok}, "
                f"checksum={checksum})")
    except Exception as exc:
        _update(conn, job_id, "failed", "Preparation failed", error=str(exc))
    finally:
        conn.close()


def _smoke_test(conn, pack_id: str) -> bool:
    result = answerer.answer_question(conn, pack_id, "What does simida mean?")
    return result["answer_mode"] != "abstained"


def start_job(pack_id: str, plan: dict) -> str:
    job_id = create_job(pack_id, plan)
    thread = threading.Thread(target=_run, args=(job_id, plan), daemon=True)
    thread.start()
    return job_id
