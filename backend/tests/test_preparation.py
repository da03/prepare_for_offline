from __future__ import annotations

from app.models import ContextCreate, ContextSourceCreate


def test_existing_korea_pack_migrates_to_explicit_example(isolated_home):
    from app.db import _SCHEMA, connect, init_db

    conn = connect()
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO packs (pack_id,title,manifest,ready,size_bytes,created_at) "
        "VALUES ('korea-language','Korea','{}',1,10,'2026-01-01')"
    )
    conn.commit()
    conn.close()

    init_db()
    conn = connect()
    context = conn.execute(
        "SELECT * FROM contexts WHERE context_id='example-korea'"
    ).fetchone()
    assert context is not None
    assert context["template_id"] == "korea"
    assert context["active_pack_id"] == "korea-language"
    setting = conn.execute(
        "SELECT value FROM settings WHERE key='active_context_id'"
    ).fetchone()
    assert setting["value"] == '"example-korea"'
    conn.close()


def test_generic_preparation_is_versioned_and_atomic(isolated_home):
    from app.db import connect, init_db
    from app.models import CommandRequest
    from app.services import commands, contexts, jobs

    init_db()
    conn = connect()
    context = contexts.create(
        conn,
        ContextCreate(
            name="Conference",
            context_type="conference",
            goal="Use my schedule offline",
            expected_needs=["When is the keynote?"],
            storage_budget_mb=800,
        ),
    )
    source = contexts.add_source(
        conn,
        context["context_id"],
        ContextSourceCreate(
            title="Conference schedule",
            content="The keynote starts at 9 AM in Hall A.",
        ),
    )
    raw = {
        "context_id": context["context_id"],
        "selected_source_ids": [source["source_id"]],
        "expected_questions": ["When is the keynote?"],
        "compile_expert": False,
        "cache_ui_router": False,
    }
    first_id = jobs.create_job(context["context_id"], raw)
    jobs._run(first_id, raw)
    assert jobs.get_job(first_id)["state"] == "ready"
    first_pack = contexts.get(conn, context["context_id"])["active_pack_id"]

    second_id = jobs.create_job(context["context_id"], raw)
    jobs._run(second_id, raw)
    assert jobs.get_job(second_id)["state"] == "ready"
    active = contexts.get(conn, context["context_id"])["active_pack_id"]
    assert active != first_pack
    packs = conn.execute(
        "SELECT version, is_current, ready FROM packs WHERE context_id=? "
        "ORDER BY version",
        (context["context_id"],),
    ).fetchall()
    assert [(row["version"], row["is_current"], row["ready"]) for row in packs] == [
        (1, 0, 1),
        (2, 1, 1),
    ]
    answer = commands.execute(
        conn, CommandRequest(text="When is the keynote?")
    )
    assert answer["kind"] == "answer"
    assert "9 AM" in answer["answer"]
    conn.close()


def test_cancel_is_requested_for_running_job(isolated_home):
    from app.db import connect, init_db
    from app.services import contexts, jobs

    init_db()
    conn = connect()
    context = contexts.create(conn, ContextCreate(name="Project"))
    job_id = jobs.create_job(
        context["context_id"], {"context_id": context["context_id"]}
    )
    assert jobs.cancel_job(job_id) is True
    row = conn.execute(
        "SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    assert row["cancel_requested"] == 1
    conn.close()
