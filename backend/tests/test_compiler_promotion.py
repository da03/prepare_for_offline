from __future__ import annotations

from datetime import datetime, timezone

from app.models import ContextCreate


def test_background_finetune_promotes_only_non_regression(isolated_home, monkeypatch):
    from app.db import connect, init_db
    from app.services import contexts, jobs, paw_experts

    init_db()
    conn = connect()
    trip = contexts.create(conn, ContextCreate(name="Trip", context_type="trip"))
    conn.execute(
        "INSERT INTO packs (pack_id,title,manifest,ready,size_bytes,created_at,"
        "context_id,version,is_current) VALUES ('pack-1','Trip','{}',1,0,?,?,1,1)",
        (datetime.now(timezone.utc).isoformat(), trip["context_id"]),
    )
    conn.commit()
    activated = []

    def fake_compile(*args, **kwargs):
        return {
            "version_id": "pv-final",
            "role": kwargs["role"],
            "program_id": "final-program",
            "compiler": "paw-ft-bs48",
            "stage": "finetuned",
            "score": 0.9,
            "metrics": {},
        }

    monkeypatch.setattr(paw_experts, "compile_expert_version", fake_compile)
    monkeypatch.setattr(
        paw_experts, "activate_version", lambda _conn, version: activated.append(version)
    )
    conn.close()

    jobs._optimize_pack(
        trip["context_id"],
        "pack-1",
        {"heard_expression_resolver": {"score": 0.8}},
    )
    conn = connect()
    assert contexts.get(conn, trip["context_id"])["optimization_status"] == "optimized"
    assert activated[0]["program_id"] == "final-program"
    conn.close()


def test_background_finetune_keeps_fast_version_on_regression(isolated_home, monkeypatch):
    from app.db import connect, init_db
    from app.services import contexts, jobs, paw_experts

    init_db()
    conn = connect()
    trip = contexts.create(conn, ContextCreate(name="Trip", context_type="trip"))
    conn.execute(
        "INSERT INTO packs (pack_id,title,manifest,ready,size_bytes,created_at,"
        "context_id,version,is_current) VALUES ('pack-1','Trip','{}',1,0,?,?,1,1)",
        (datetime.now(timezone.utc).isoformat(), trip["context_id"]),
    )
    conn.commit()
    monkeypatch.setattr(
        paw_experts,
        "compile_expert_version",
        lambda *args, **kwargs: {
            "version_id": "pv-final",
            "role": kwargs["role"],
            "program_id": "worse-program",
            "compiler": "paw-ft-bs48",
            "stage": "finetuned",
            "score": 0.4,
            "metrics": {},
        },
    )
    activated = []
    monkeypatch.setattr(
        paw_experts, "activate_version", lambda _conn, version: activated.append(version)
    )
    conn.close()

    jobs._optimize_pack(
        trip["context_id"],
        "pack-1",
        {"heard_expression_resolver": {"score": 0.8}},
    )
    conn = connect()
    assert contexts.get(conn, trip["context_id"])["optimization_status"] == "fast_active"
    assert activated == []
    conn.close()


def test_background_finetune_retries_client_timeout(isolated_home, monkeypatch):
    from app.db import connect, init_db
    from app.services import contexts, jobs, paw_experts

    init_db()
    conn = connect()
    trip = contexts.create(conn, ContextCreate(name="Trip", context_type="trip"))
    conn.execute(
        "INSERT INTO packs (pack_id,title,manifest,ready,size_bytes,created_at,"
        "context_id,version,is_current) VALUES ('pack-1','Trip','{}',1,0,?,?,1,1)",
        (datetime.now(timezone.utc).isoformat(), trip["context_id"]),
    )
    conn.commit()
    attempts = 0

    def flaky_compile(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("read timeout")
        return {
            "version_id": "pv-final",
            "role": kwargs["role"],
            "program_id": "final-program",
            "compiler": "paw-ft-bs48",
            "stage": "finetuned",
            "score": 1.0,
            "metrics": {},
        }

    monkeypatch.setattr(paw_experts, "compile_expert_version", flaky_compile)
    monkeypatch.setattr(paw_experts, "activate_version", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs.time, "sleep", lambda *_: None)
    conn.close()
    jobs._optimize_pack(
        trip["context_id"],
        "pack-1",
        {"heard_expression_resolver": {"score": 1.0}},
    )
    assert attempts == 2
    conn = connect()
    assert contexts.get(conn, trip["context_id"])["optimization_status"] == "optimized"
    conn.close()
