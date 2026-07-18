"""Persistent registry for built-in and user-prepared PAW programs."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import neural_specs

PROGRAM_MANIFEST = Path(__file__).with_name("neural_programs.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _specifications() -> dict[str, str]:
    return {
        "broad": neural_specs.BROAD_QA_SPEC,
        "router": neural_specs.TOPK_ROUTER_SPEC,
        "aggregator": neural_specs.AGGREGATOR_SPEC,
        "critic": neural_specs.CRITIC_SPEC,
        "revision": neural_specs.REVISION_SPEC,
        "followup": neural_specs.FOLLOWUP_SPEC,
        "prepared_matcher": neural_specs.PREPARED_MATCHER_SPEC,
        "language_intent": neural_specs.LANGUAGE_INTENT_SPEC,
        "heard_expression": neural_specs.HEARD_EXPRESSION_SPEC,
        "translation": neural_specs.TRANSLATION_SPEC,
        **{
            f"subject:{name}": spec
            for name, spec in neural_specs.SUBJECT_SPECS.items()
        },
    }


def ensure_builtins(conn: sqlite3.Connection) -> None:
    if not PROGRAM_MANIFEST.exists():
        return
    document = json.loads(PROGRAM_MANIFEST.read_text())
    specs = _specifications()
    now = _now()
    for role, stages in document.get("programs", {}).items():
        if role not in specs or "standard" not in stages:
            continue
        for value in stages.values():
            if value["spec_sha256"] != neural_specs.spec_sha256(specs[role]):
                raise RuntimeError(
                    f"Compiled PAW program {role} does not match its frozen spec"
                )
        selected_stage = "finetuned" if "finetuned" in stages else "standard"
        selected = stages[selected_stage]
        key = f"builtin:{role}"
        version_id = f"{key}:{selected_stage}:{selected['program_id']}"
        conn.execute(
            """
            INSERT INTO neural_programs (
                program_key, role, display_name, built_in, active_version_id,
                status, created_at, updated_at
            ) VALUES (?,?,?,1,?,'ready',?,?)
            ON CONFLICT(program_key) DO UPDATE SET
                active_version_id=excluded.active_version_id,
                status='ready', updated_at=excluded.updated_at
            """,
            (key, role, _display_name(role), version_id, now, now),
        )
        for stage, value in stages.items():
            stage_version_id = f"{key}:{stage}:{value['program_id']}"
            conn.execute(
                """
                INSERT OR IGNORE INTO neural_program_versions (
                    version_id, program_key, program_id, compiler, stage, spec,
                    spec_sha256, contract_score, contract_result, status, created_at
                ) VALUES (?,?,?,?,?,?,?,1.0,'{}','ready',?)
                """,
                (
                    stage_version_id,
                    key,
                    value["program_id"],
                    value["compiler"],
                    stage,
                    specs[role],
                    value["spec_sha256"],
                    now,
                ),
            )
        if selected_stage == "finetuned":
            conn.execute(
                "UPDATE neural_programs SET active_version_id=? WHERE program_key=?",
                (version_id, key),
            )
    conn.commit()


def _display_name(role: str) -> str:
    if role.startswith("subject:"):
        return role.split(":", 1)[1].replace("_", " ").title()
    return {
        "broad": "General knowledge",
        "router": "Topic router",
        "aggregator": "Answer composer",
        "critic": "Answer critic",
        "revision": "Answer reviser",
        "followup": "Follow-up rewriter",
        "prepared_matcher": "Prepared topic matcher",
        "language_intent": "Language intent classifier",
        "heard_expression": "Heard expression interpreter",
        "translation": "Translation helper",
    }.get(role, role.replace("_", " ").title())


def active(conn: sqlite3.Connection, role: str) -> dict | None:
    row = conn.execute(
        """
        SELECT p.*, v.program_id, v.compiler, v.stage, v.spec, v.spec_sha256
        FROM neural_programs p
        JOIN neural_program_versions v ON v.version_id=p.active_version_id
        WHERE p.role=? AND p.status='ready'
        ORDER BY p.built_in DESC, p.updated_at DESC
        LIMIT 1
        """,
        (role,),
    ).fetchone()
    return dict(row) if row else None


def prepared(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*, v.program_id, v.compiler, v.stage, v.contract_score
        FROM neural_programs p
        LEFT JOIN neural_program_versions v ON v.version_id=p.active_version_id
        WHERE p.built_in=0 AND p.role='prepared_topic'
        ORDER BY p.updated_at DESC
        """
    ).fetchall()
    return [
        {
            "program_key": row["program_key"],
            "topic": row["topic_prompt"],
            "name": row["display_name"],
            "status": row["status"],
            "program_id": row["program_id"],
            "compiler": row["compiler"],
            "stage": row["stage"],
            "contract_score": row["contract_score"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def prepared_for_router(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return prepared(conn)[:limit]


def get(conn: sqlite3.Connection, program_key: str) -> dict | None:
    row = conn.execute(
        """
        SELECT p.*, v.program_id, v.compiler, v.stage, v.spec, v.spec_sha256,
               v.contract_score, v.contract_result
        FROM neural_programs p
        LEFT JOIN neural_program_versions v ON v.version_id=p.active_version_id
        WHERE p.program_key=?
        """,
        (program_key,),
    ).fetchone()
    return dict(row) if row else None


def topic_key(prompt: str) -> str:
    normalized = re.sub(r"\s+", " ", prompt.strip()).casefold()
    return f"topic:{hashlib.sha256(normalized.encode()).hexdigest()[:16]}"


def ensure_topic(conn: sqlite3.Connection, prompt: str) -> dict:
    key = topic_key(prompt)
    now = _now()
    display = " ".join(prompt.strip().split())[:80]
    conn.execute(
        """
        INSERT INTO neural_programs (
            program_key, role, topic_prompt, display_name, built_in, status,
            created_at, updated_at
        ) VALUES (?,'prepared_topic',?,?,0,'preparing',?,?)
        ON CONFLICT(program_key) DO UPDATE SET
            topic_prompt=excluded.topic_prompt, display_name=excluded.display_name,
            status='preparing', updated_at=excluded.updated_at
        """,
        (key, prompt.strip(), display, now, now),
    )
    conn.commit()
    return get(conn, key)


def add_version(
    conn: sqlite3.Connection,
    *,
    program_key: str,
    program_id: str,
    compiler: str,
    stage: str,
    spec: str,
    contract_score: float,
    contract_result: dict,
    activate: bool,
) -> dict:
    version_id = f"npv-{uuid.uuid4().hex[:16]}"
    now = _now()
    conn.execute(
        """
        INSERT INTO neural_program_versions (
            version_id, program_key, program_id, compiler, stage, spec,
            spec_sha256, contract_score, contract_result, status, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,'ready',?)
        """,
        (
            version_id,
            program_key,
            program_id,
            compiler,
            stage,
            spec,
            neural_specs.spec_sha256(spec),
            contract_score,
            json.dumps(contract_result, ensure_ascii=False),
            now,
        ),
    )
    if activate:
        conn.execute(
            "UPDATE neural_programs SET active_version_id=?, status='ready', "
            "updated_at=? WHERE program_key=?",
            (version_id, now, program_key),
        )
    conn.commit()
    return {
        "version_id": version_id,
        "program_key": program_key,
        "program_id": program_id,
        "compiler": compiler,
        "stage": stage,
        "contract_score": contract_score,
    }


def remove(conn: sqlite3.Connection, program_key: str) -> bool:
    deleted = conn.execute(
        "DELETE FROM neural_programs WHERE program_key=? AND built_in=0",
        (program_key,),
    ).rowcount
    conn.commit()
    return bool(deleted)


def rollback(conn: sqlite3.Connection, program_key: str) -> dict | None:
    program = get(conn, program_key)
    if not program or program["built_in"]:
        return None
    row = conn.execute(
        """
        SELECT * FROM neural_program_versions
        WHERE program_key=? AND version_id!=? AND status='ready'
        ORDER BY created_at DESC LIMIT 1
        """,
        (program_key, program["active_version_id"]),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE neural_programs SET active_version_id=?, status='ready', "
        "updated_at=? WHERE program_key=?",
        (row["version_id"], _now(), program_key),
    )
    conn.commit()
    return get(conn, program_key)
