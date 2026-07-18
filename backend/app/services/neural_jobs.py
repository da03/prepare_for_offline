"""Online PAW topic compilation with Standard-first readiness and FT promotion."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import httpx
import programasweights as paw
from programasweights.client import PAWClient

from ..config import get_settings
from ..db import connect
from . import neural_specs, program_registry, program_runtime

_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()
_TRANSIENT_STATUS_CODES = {502, 503, 504}
_FINETUNE_TIMEOUT_SECONDS = 600.0

ProgressCallback = Callable[[float | None, str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update(
    conn: sqlite3.Connection,
    job_id: str,
    state: str,
    progress: int,
    message: str,
    *,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE neural_jobs SET state=?, progress_percent=?, message=?, error=?,
            updated_at=? WHERE job_id=?
        """,
        (state, progress, message, error, _now(), job_id),
    )
    conn.commit()


def _cancelled(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute(
        "SELECT cancel_requested FROM neural_jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    return bool(row and row["cancel_requested"])


def start(prompt: str) -> dict:
    conn = connect()
    try:
        program = program_registry.ensure_topic(conn, prompt)
        job_id = f"njob-{uuid.uuid4().hex[:16]}"
        now = _now()
        conn.execute(
            """
            INSERT INTO neural_jobs (
                job_id, program_key, topic_prompt, state, progress_percent,
                message, created_at, updated_at
            ) VALUES (?,?,?,'queued',0,'Queued',?,?)
            """,
            (job_id, program["program_key"], prompt.strip(), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    thread = threading.Thread(target=_run_standard, args=(job_id,), daemon=True)
    with _lock:
        _threads[job_id] = thread
    thread.start()
    return get(job_id)


def _compile(
    spec: str,
    compiler: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, float, dict]:
    if compiler == "paw-ft-bs48":
        client = PAWClient()
        program_id = None
        for attempt in range(3):
            response = httpx.post(
                f"{client._api_url}/api/v1/compile/async",
                json={"spec": spec, "public": False, "compiler": compiler},
                headers=client._headers(),
                timeout=30.0,
            )
            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt < 2
            ):
                time.sleep(5)
                continue
            response.raise_for_status()
            status = response.json()
            program_id = status.get("program_id")
            break
        if not program_id:
            remote_job_id = status.get("job_id")
            if not remote_job_id:
                raise RuntimeError("PAW compiler returned no job ID")
            deadline = time.monotonic() + _FINETUNE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                state = str(status.get("status") or "queued")
                if on_progress:
                    on_progress(status.get("percent"), state)
                if state == "ready":
                    program_id = status.get("program_id")
                    break
                if state in {"failed", "cancelled"}:
                    raise RuntimeError(
                        status.get("error")
                        or f"PAW finetune job ended with status {state}"
                    )
                time.sleep(1)
                response = httpx.get(
                    f"{client._api_url}/api/v1/compile/{remote_job_id}",
                    headers=client._headers(),
                    timeout=15.0,
                )
                if response.status_code in _TRANSIENT_STATUS_CODES:
                    continue
                response.raise_for_status()
                status = response.json()
            else:
                raise TimeoutError("PAW finetune job timed out after 10 minutes")
    else:
        program = paw.compile(spec, compiler=compiler, public=False)
        program_id = getattr(program, "id", None) or getattr(
            program, "program_id", None
        )
    if not program_id:
        raise RuntimeError("PAW compiler returned no program ID")

    PAWClient().download_paw(program_id)
    score, result = contract_test(program_id, spec)
    return program_id, score, result


def contract_test(program_id: str, spec: str) -> tuple[float, dict]:
    topic = spec.split("knowledge:", 1)[-1].split("\n", 1)[0].strip()
    questions = (
        f"What are the most important things to understand about {topic}?",
        f"What is a common misconception about {topic}?",
        f"Explain {topic} to a curious beginner.",
    )
    rows = []
    passed = 0
    for question in questions:
        try:
            output = program_runtime.run(
                program_id,
                question,
                max_tokens=240,
                timeout_seconds=45,
            ).output.strip()
            lowered = output.casefold()
            ok = bool(output) and not any(
                phrase in lowered
                for phrase in (
                    "i cannot answer",
                    "i can't answer",
                    "i do not know",
                    "i don't know",
                    "unsupported",
                )
            )
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            ok = False
        passed += int(ok)
        rows.append({"question": question, "output": output, "passed": ok})
    return passed / len(questions), {"tests": rows}


def _run_standard(job_id: str) -> None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM neural_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row:
            return
        spec = neural_specs.prepared_topic_spec(row["topic_prompt"])
        _update(conn, job_id, "compiling_standard", 10, "Building initial program")
        if _cancelled(conn, job_id):
            _update(conn, job_id, "cancelled", 0, "Cancelled")
            return
        settings = get_settings()
        program_id, score, result = _compile(spec, settings.compiler_fast)
        if score < 1.0:
            raise RuntimeError("Standard topic program failed its answer contract")
        version = program_registry.add_version(
            conn,
            program_key=row["program_key"],
            program_id=program_id,
            compiler=settings.compiler_fast,
            stage="standard",
            spec=spec,
            contract_score=score,
            contract_result=result,
            activate=False,
        )
        conn.execute(
            "UPDATE neural_jobs SET standard_version_id=? WHERE job_id=?",
            (version["version_id"], job_id),
        )
        conn.execute(
            "UPDATE neural_programs SET status='improving', updated_at=? "
            "WHERE program_key=?",
            (_now(), row["program_key"]),
        )
        conn.commit()
        _update(conn, job_id, "compiling_finetuned", 0, "Improving final program")
        if _cancelled(conn, job_id):
            _update(conn, job_id, "cancelled", 0, "Cancelled")
            return
        _run_finetuned(job_id, spec, score)
    except Exception as exc:
        _update(
            conn,
            job_id,
            "failed",
            0,
            "Could not prepare topic",
            error=str(exc),
        )
        row = conn.execute(
            "SELECT program_key FROM neural_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE neural_programs SET status='failed', updated_at=? "
                "WHERE program_key=? AND active_version_id IS NULL",
                (_now(), row["program_key"]),
            )
            conn.commit()
    finally:
        conn.close()
        with _lock:
            _threads.pop(job_id, None)


def _run_finetuned(job_id: str, spec: str, standard_score: float) -> None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM neural_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row or _cancelled(conn, job_id):
            return
        settings = get_settings()

        def report_progress(percent: float | None, state: str) -> None:
            if _cancelled(conn, job_id):
                return
            progress = (
                max(0, min(99, round(float(percent))))
                if isinstance(percent, (int, float))
                else 0
            )
            message = (
                "Waiting for finetune compiler"
                if state == "queued"
                else "Improving final program"
            )
            _update(
                conn,
                job_id,
                "compiling_finetuned",
                progress,
                message,
            )

        program_id, score, result = _compile(
            spec,
            settings.compiler_final,
            on_progress=report_progress,
        )
        if score < standard_score:
            raise RuntimeError(
                "Finetuned topic program regressed its answer contract"
            )
        if _cancelled(conn, job_id):
            return
        version = program_registry.add_version(
            conn,
            program_key=row["program_key"],
            program_id=program_id,
            compiler=settings.compiler_final,
            stage="finetuned",
            spec=spec,
            contract_score=score,
            contract_result=result,
            activate=True,
        )
        conn.execute(
            "UPDATE neural_jobs SET finetuned_version_id=?, state='ready', "
            "progress_percent=100, message='Ready', error=NULL, updated_at=? "
            "WHERE job_id=?",
            (version["version_id"], _now(), job_id),
        )
        conn.commit()
    except Exception as exc:
        row = conn.execute(
            "SELECT program_key FROM neural_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE neural_programs SET status='failed', updated_at=? "
                "WHERE program_key=? AND active_version_id IS NULL",
                (_now(), row["program_key"]),
            )
        conn.execute(
            "UPDATE neural_jobs SET state='failed', progress_percent=0, "
            "message='Could not finetune topic', error=?, updated_at=? "
            "WHERE job_id=?",
            (str(exc), _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def get(job_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM neural_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def cancel(job_id: str) -> bool:
    conn = connect()
    try:
        updated = conn.execute(
            "UPDATE neural_jobs SET cancel_requested=1, state='cancelled', "
            "message='Cancelled', updated_at=? WHERE job_id=? "
            "AND state NOT IN ('failed','cancelled')",
            (_now(), job_id),
        ).rowcount
        conn.commit()
        return bool(updated)
    finally:
        conn.close()


def recover_startup(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE neural_jobs SET state='failed', error='Interrupted by app restart', "
        "message='Preparation was interrupted', updated_at=? "
        "WHERE state IN ('queued','compiling_standard','compiling_finetuned')",
        (_now(),),
    )
    conn.execute(
        "UPDATE neural_programs SET status=CASE "
        "WHEN active_version_id IS NULL THEN 'failed' ELSE 'ready' END "
        "WHERE status='improving'"
    )
    conn.commit()
