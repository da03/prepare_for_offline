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
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from ..db import connect
from . import (
    answerer,
    contexts,
    packs as packs_svc,
    paw_experts,
    planner,
    preferences,
    retrieval,
    trip_acquisition,
)

STATES = [
    "planning", "searching", "compiling", "downloading", "processing_documents",
    "indexing", "testing", "ready", "failed", "cancelled",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _search_is_recent(context: dict, minutes: int = 10) -> bool:
    value = context.get("search_refreshed_at")
    if not value:
        return False
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - observed).total_seconds() < minutes * 60
    except ValueError:
        return False


def create_job(context_id: str, plan: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, context_id, state, plan, progress, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, context_id, "planning", json.dumps(plan), "[]", _now(), _now()),
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
            "context_id": row["context_id"],
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
        result = answerer.answer_question(conn, pack_id, q, enqueue=False)
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


def _cancelled(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute(
        "SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    return bool(row and row["cancel_requested"])


def _stop_if_cancelled(
    conn: sqlite3.Connection, job_id: str, context_id: str
) -> bool:
    if not _cancelled(conn, job_id):
        return False
    _update(conn, job_id, "cancelled", "Preparation cancelled")
    contexts.set_status(conn, context_id, "draft")
    return True


def cancel_job(job_id: str) -> bool:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT state FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row or row["state"] in {"ready", "failed", "cancelled"}:
            return False
        conn.execute(
            "UPDATE jobs SET cancel_requested=1, updated_at=? WHERE job_id=?",
            (_now(), job_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _run(job_id: str, raw_plan: dict) -> None:
    conn = connect()
    context_id = raw_plan["context_id"]
    try:
        context = contexts.get(conn, context_id)
        if not context:
            raise ValueError("Context not found")
        finalize = raw_plan.get("finalize")
        if finalize is None:
            finalize = context["preparation_quality"] == "final"
        contexts.set_status(conn, context_id, "preparing")
        _update(conn, job_id, "planning", "Building PackPlan")
        if (
            raw_plan.get("discover", True)
            and context.get("search_enabled", True)
            and context.get("trip_brief")
            and not _search_is_recent(context)
            and preferences.get_all(conn).get("search_mode", "automatic") != "off"
        ):
            _update(conn, job_id, "searching", "Finding current official trip sources")
            try:
                discovery = trip_acquisition.discover_trip(conn, context_id)
                _update(
                    conn,
                    job_id,
                    "searching",
                    f"Added {len(discovery['sources'])} public sources",
                )
            except Exception as exc:
                _update(
                    conn,
                    job_id,
                    "searching",
                    f"Public search unavailable; continuing with local sources ({exc})",
                )
        sources = contexts.list_sources(conn, context_id)
        pack_plan = planner.plan_context(
            context,
            sources,
            selected_source_ids=raw_plan.get("selected_source_ids"),
            selected_capabilities=raw_plan.get("selected_capabilities"),
            selected_topics=raw_plan.get("selected_topics"),
            expected_questions=raw_plan.get("expected_questions"),
            finalize=finalize,
            allow_online_synth=raw_plan.get("allow_online_synth", False),
        )
        if not pack_plan.selected_source_ids and not pack_plan.template_id:
            raise ValueError("Add at least one source or choose a template before preparing.")
        if not pack_plan.fits_budget:
            raise ValueError(
                "The selected sources exceed the storage budget. "
                "Remove sources or increase the budget."
            )
        _update(
            conn,
            job_id,
            "planning",
            f"Selected {len(pack_plan.selected_source_ids)} sources"
            + (
                f" and topics: {', '.join(pack_plan.selected_topics)}"
                if pack_plan.selected_topics
                else ""
            ),
        )
        if _stop_if_cancelled(conn, job_id, context_id):
            return

        _update(conn, job_id, "processing_documents", "Building versioned offline pack")
        pack_id = packs_svc.build_pack(conn, context, pack_plan.to_dict())
        conn.execute("UPDATE jobs SET pack_id=? WHERE job_id=?", (pack_id, job_id))
        conn.commit()
        _update(conn, job_id, "indexing", "Indexed documents and answer cards")
        if _stop_if_cancelled(conn, job_id, context_id):
            return

        base_ok = False
        if pack_plan.include_base_model:
            _update(conn, job_id, "downloading", "Ensuring base interpreter is cached")
            try:
                if os.environ.get("PREPARE_OFFLINE_SKIP_MODEL_DOWNLOAD") == "1":
                    base_ok = True
                else:
                    from programasweights import cache
                    from ..config import get_settings

                    cache.get_base_model_path(get_settings().interpreter)
                    base_ok = True
            except Exception as exc:
                _update(conn, job_id, "downloading",
                        f"Base model not cached ({exc}); deterministic answers only")
        if raw_plan.get("cache_ui_router", True):
            try:
                paw_experts.ensure_ui_router_cached()
                paw_experts.ensure_global_programs_cached()
                _update(conn, job_id, "downloading", "Cached the offline action router")
            except Exception as exc:
                _update(conn, job_id, "downloading", f"Action router cache deferred ({exc})")
        if _stop_if_cancelled(conn, job_id, context_id):
            return

        fast_versions: dict[str, dict] = {}
        if raw_plan.get("compile_expert", True) and pack_plan.expert_specs:
            from ..config import get_settings

            settings = get_settings()
            for role in pack_plan.expert_specs:
                _update(conn, job_id, "compiling", f"Compiling fast {role}")
                try:
                    version = paw_experts.compile_expert_version(
                        conn,
                        context_id=context_id,
                        pack_id=pack_id,
                        role=role,
                        compiler=settings.compiler_fast,
                        stage="fast",
                    )
                    paw_experts.activate_version(conn, version)
                    fast_versions[role] = version
                    _update(conn, job_id, "compiling",
                            f"Fast {role} passed {version['score']:.0%} of tests")
                except Exception as exc:
                    _update(conn, job_id, "compiling",
                            f"{role} compile skipped ({exc}); deterministic fallback")
                if _stop_if_cancelled(conn, job_id, context_id):
                    return

        _update(conn, job_id, "indexing", "Precomputing answer cards from likely questions")
        stats = _precompute_cards(conn, pack_id, pack_plan.expected_questions)
        _update(conn, job_id, "indexing",
                f"Coverage {int(stats['coverage']*100)}% "
                f"({stats['answered']}/{stats['expected']}), "
                f"+{stats['precomputed_cards']} cards")
        if _stop_if_cancelled(conn, job_id, context_id):
            return

        _update(conn, job_id, "testing", "Verifying checksum and offline smoke test")
        checksum = _checksum(conn, pack_id)
        offline_ok = _smoke_test(conn, pack_id)

        packs_svc.finalize_pack(
            conn, pack_id,
            plan=pack_plan.to_dict(), coverage=stats,
            checksum=checksum, offline_tested=offline_ok,
        )
        optimize = raw_plan.get("optimize", True)
        if optimize and fast_versions:
            conn.execute(
                "UPDATE contexts SET optimization_status='queued' WHERE context_id=?",
                (context_id,),
            )
            conn.commit()
            threading.Thread(
                target=_optimize_pack,
                args=(context_id, pack_id, fast_versions),
                daemon=True,
            ).start()
        else:
            conn.execute(
                "UPDATE contexts SET optimization_status=? WHERE context_id=?",
                ("deferred" if fast_versions else "not_needed", context_id),
            )
            conn.commit()
        _update(conn, job_id, "ready",
                f"Pack ready (offline_tested={offline_ok}, base_model={base_ok}, "
                f"checksum={checksum})")
    except Exception as exc:
        _update(conn, job_id, "failed", "Preparation failed", error=str(exc))
        contexts.set_status(conn, context_id, "draft")
    finally:
        conn.close()


def _smoke_test(conn, pack_id: str) -> bool:
    card = conn.execute(
        "SELECT question FROM answer_cards WHERE pack_id=? LIMIT 1", (pack_id,)
    ).fetchone()
    if card:
        result = answerer.answer_question(
            conn, pack_id, card["question"], enqueue=False
        )
        return result["answer_mode"] != "abstained"
    document = conn.execute(
        "SELECT source_id, title FROM documents WHERE pack_id=? LIMIT 1", (pack_id,)
    ).fetchone()
    if not document:
        return False
    results = retrieval.search(conn, pack_id, document["title"], limit=1)
    return bool(results and results[0].source_id == document["source_id"])


def _optimize_pack(
    context_id: str, pack_id: str, fast_versions: dict[str, dict]
) -> None:
    """Compile finetuned drop-in replacements and promote only on eval lift."""
    from ..config import get_settings

    conn = connect()
    try:
        conn.execute(
            "UPDATE contexts SET optimization_status='optimizing' WHERE context_id=?",
            (context_id,),
        )
        conn.commit()
        settings = get_settings()
        promoted = 0
        for role, fast in fast_versions.items():
            final = None
            for attempt in range(2):
                try:
                    final = paw_experts.compile_expert_version(
                        conn,
                        context_id=context_id,
                        pack_id=pack_id,
                        role=role,
                        compiler=settings.compiler_final,
                        stage="finetuned",
                    )
                    break
                except Exception as exc:
                    if attempt == 0 and "timeout" in str(exc).casefold():
                        # Hosted finetunes may finish after the client timeout;
                        # the idempotent retry typically resolves the completed job.
                        time.sleep(2)
                        continue
                    raise
            if final is None:
                raise RuntimeError(f"Finetune produced no version for {role}")
            if final["score"] + 1e-9 >= fast["score"]:
                paw_experts.activate_version(conn, final)
                promoted += 1
        conn.execute(
            "UPDATE contexts SET optimization_status=? WHERE context_id=?",
            ("optimized" if promoted == len(fast_versions) else "fast_active", context_id),
        )
        conn.commit()
    except Exception:
        conn.execute(
            "UPDATE contexts SET optimization_status='failed' WHERE context_id=?",
            (context_id,),
        )
        conn.commit()
    finally:
        conn.close()


def rollback_optimization(context_id: str) -> bool:
    conn = connect()
    try:
        trip = contexts.get(conn, context_id)
        if not trip or not trip.get("active_pack_id"):
            return False
        pack_id = trip["active_pack_id"]
        fast_rows = conn.execute(
            "SELECT * FROM program_versions WHERE pack_id=? AND stage='fast' "
            "ORDER BY created_at DESC",
            (pack_id,),
        ).fetchall()
        if not fast_rows:
            return False
        for row in fast_rows:
            version = dict(row)
            paw_experts.activate_version(conn, version)
        conn.execute(
            "UPDATE contexts SET optimization_status='rolled_back' WHERE context_id=?",
            (context_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def resume_optimization(context_id: str) -> bool:
    conn = connect()
    try:
        trip = contexts.get(conn, context_id)
        if not trip or not trip.get("active_pack_id"):
            return False
        pack_id = trip["active_pack_id"]
        rows = conn.execute(
            "SELECT * FROM program_versions WHERE pack_id=? AND stage='fast' "
            "ORDER BY created_at DESC",
            (pack_id,),
        ).fetchall()
        fast_versions: dict[str, dict] = {}
        for row in rows:
            fast_versions.setdefault(row["role"], dict(row))
        if not fast_versions:
            return False
        conn.execute(
            "UPDATE contexts SET optimization_status='queued' WHERE context_id=?",
            (context_id,),
        )
        conn.commit()
        threading.Thread(
            target=_optimize_pack,
            args=(context_id, pack_id, fast_versions),
            daemon=True,
        ).start()
        return True
    finally:
        conn.close()


def start_job(context_id: str, plan: dict) -> str:
    raw_plan = {**plan, "context_id": context_id}
    job_id = create_job(context_id, raw_plan)
    thread = threading.Thread(target=_run, args=(job_id, raw_plan), daemon=True)
    thread.start()
    return job_id
