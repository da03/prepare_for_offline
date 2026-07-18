from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.db import _migration_8, connect
from app.services import neural_jobs, neural_specs, program_registry
from app.services.program_runtime import ProgramResult


def test_fresh_install_contains_no_source_or_retrieval_tables(client):
    assert client.get("/api/neural/status").status_code == 200
    conn = connect()
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert not {
        "documents",
        "answer_cards",
        "context_sources",
        "knowledge_layers",
        "search_runs",
    } & tables


def test_ask_runs_broad_all_matching_prepared_programs_and_aggregator(
    client, monkeypatch
):
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        first = program_registry.ensure_topic(conn, "Japanese geography")
        second = program_registry.ensure_topic(conn, "Japanese political history")
        program_registry.add_version(
            conn,
            program_key=first["program_key"],
            program_id="prepared-geography",
            compiler="paw-ft-bs48",
            stage="finetuned",
            spec="Japanese geography specialist",
            contract_score=1.0,
            contract_result={},
            activate=True,
        )
        program_registry.add_version(
            conn,
            program_key=second["program_key"],
            program_id="prepared-politics",
            compiler="paw-ft-bs48",
            stage="finetuned",
            spec="Japanese political history specialist",
            contract_score=1.0,
            contract_result={},
            activate=True,
        )
        ids = {
            role: program_registry.active(conn, role)["program_id"]
            for role in (
                "broad",
                "aggregator",
                "prepared_matcher",
            )
        }
        expected_labels = [first["program_key"], second["program_key"]]
    finally:
        conn.close()
    calls = []

    def fake_run(program_id, text, **kwargs):
        del kwargs
        calls.append(program_id)
        if program_id == ids["prepared_matcher"]:
            output = "YES"
        elif program_id == ids["aggregator"]:
            output = "Geography shaped the constraints of Japanese political history."
        elif program_id == ids["broad"]:
            output = "Japan's geography affected its political development."
        else:
            output = "A relevant specialist answer."
        return ProgramResult(output, 1.0, 100.0, True)

    monkeypatch.setattr("app.services.program_runtime.run", fake_run)
    response = client.post(
        "/api/ask",
        json={"text": "How did Japan's geography shape its political history?"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["program_labels"]) == set(expected_labels)
    assert payload["answer"].startswith("Geography shaped")
    assert ids["broad"] in calls
    assert "prepared-geography" in calls
    assert "prepared-politics" in calls
    assert ids["aggregator"] in calls
    assert "sources" not in payload


def test_prepare_compiles_program_metadata_without_sources(client, monkeypatch):
    monkeypatch.setattr(
        neural_jobs,
        "_compile",
        lambda spec, compiler: (
            "prepared-program-id",
            1.0,
            {"tests": [{"passed": True}], "spec": spec, "compiler": compiler},
        ),
    )
    started = client.post(
        "/api/programs/prepare",
        json={"prompt": "Ottoman history"},
    )
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = client.get(f"/api/neural/jobs/{job_id}").json()
        if job["state"] in {"ready", "failed"}:
            break
        time.sleep(0.02)
    assert job["state"] == "ready"
    programs = client.get("/api/programs").json()["programs"]
    assert programs[0]["topic"] == "Ottoman history"
    assert programs[0]["program_id"] == "prepared-program-id"
    assert "sources" not in programs[0]
    assert client.delete(
        f"/api/programs/{programs[0]['program_key']}"
    ).status_code == 200


def test_one_prepared_match_bypasses_broad_aggregation(client, monkeypatch):
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        topic = program_registry.ensure_topic(conn, "Ottoman history")
        program_registry.add_version(
            conn,
            program_key=topic["program_key"],
            program_id="prepared-ottoman",
            compiler="paw-ft-bs48",
            stage="finetuned",
            spec="Ottoman history specialist",
            contract_score=1.0,
            contract_result={},
            activate=True,
        )
        ids = {
            role: program_registry.active(conn, role)["program_id"]
            for role in ("broad", "aggregator", "prepared_matcher")
        }
    finally:
        conn.close()
    calls = []

    def fake_run(program_id, text, **kwargs):
        del text, kwargs
        calls.append(program_id)
        if program_id == ids["prepared_matcher"]:
            output = "YES"
        elif program_id == ids["broad"]:
            output = "A weaker broad answer."
        elif program_id == "prepared-ottoman":
            output = "The stronger prepared answer."
        else:
            raise AssertionError("Aggregator should not run for one match")
        return ProgramResult(output, 1.0, 100.0, True)

    monkeypatch.setattr("app.services.program_runtime.run", fake_run)
    response = client.post(
        "/api/ask",
        json={"text": "Why were the Janissaries important?"},
    )
    assert response.status_code == 200
    assert response.json()["answer"] == "The stronger prepared answer."
    assert ids["aggregator"] not in calls


def test_translation_and_heard_expression_use_distinct_programs(
    client, monkeypatch
):
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        ids = {
            role: program_registry.active(conn, role)["program_id"]
            for role in (
                "broad",
                "language_intent",
                "translation",
                "heard_expression",
            )
        }
    finally:
        conn.close()

    def fake_run(program_id, text, **kwargs):
        del kwargs
        if program_id == ids["broad"]:
            output = "Broad draft"
        elif program_id == ids["language_intent"]:
            output = (
                "TRANSLATION"
                if "say thank you" in text.casefold()
                else "HEARD_EXPRESSION"
            )
        elif program_id == ids["translation"]:
            output = "감사합니다 (gamsahamnida) — polite thank you."
        elif program_id == ids["heard_expression"]:
            output = "-습니다 (-seumnida) is a formal-polite sentence ending."
        else:
            raise AssertionError(program_id)
        return ProgramResult(output, 1.0, 100.0, True)

    monkeypatch.setattr("app.services.program_runtime.run", fake_run)
    translated = client.post(
        "/api/ask",
        json={"text": "How do I say thank you in Korean?"},
    ).json()
    assert "감사합니다" in translated["answer"]
    assert translated["program_labels"] == ["translation"]

    heard = client.post(
        "/api/ask",
        json={"text": "What does simida mean?"},
    ).json()
    assert "seumnida" in heard["answer"]
    assert heard["program_labels"] == ["heard_expression"]


def test_standard_and_finetuned_compile_the_identical_topic_spec(
    client, monkeypatch
):
    calls = []

    def fake_compile(spec, compiler):
        calls.append((spec, compiler))
        return (
            f"program-{len(calls)}",
            1.0,
            {"tests": [{"passed": True}]},
        )

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            del daemon
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(neural_jobs, "_compile", fake_compile)
    monkeypatch.setattr(neural_jobs.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(neural_jobs, "_lock", DummyLock())
    response = client.post(
        "/api/programs/prepare",
        json={"prompt": "Korean language for travel"},
    )
    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert neural_specs.spec_sha256(calls[0][0]) == neural_specs.spec_sha256(
        calls[1][0]
    )
    assert calls[0][1] == "paw-4b-qwen3-0.6b"
    assert calls[1][1] == "paw-ft-bs48"
    programs = client.get("/api/programs").json()["programs"]
    assert programs[0]["stage"] == "finetuned"
    rolled_back = client.post(
        f"/api/programs/{programs[0]['program_key']}/rollback"
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["stage"] == "standard"


def test_manifest_programs_match_frozen_spec_hashes(client):
    del client
    manifest = json.loads(
        Path(program_registry.PROGRAM_MANIFEST).read_text()
    )["programs"]
    specs = {
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
    for role, spec in specs.items():
        assert manifest[role]["standard"]["spec_sha256"] == (
            neural_specs.spec_sha256(spec)
        )


def test_selected_specs_do_not_contain_their_held_out_questions(client):
    del client
    from eval.universal_qa.runner import load_benchmark

    assert "Input:" not in neural_specs.BROAD_QA_SPEC
    shipping_specs = "\n".join(
        (
            neural_specs.BROAD_QA_SPEC,
            neural_specs.AGGREGATOR_SPEC,
            neural_specs.FOLLOWUP_SPEC,
            neural_specs.PREPARED_MATCHER_SPEC,
            neural_specs.LANGUAGE_INTENT_SPEC,
            neural_specs.HEARD_EXPRESSION_SPEC,
            neural_specs.TRANSLATION_SPEC,
        )
    )
    benchmark = load_benchmark()
    for split in ("dev", "test"):
        assert not [
            item["id"]
            for item in benchmark[split]["questions"]
            if item["question"] in shipping_specs
        ]

    language_cases = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "eval"
            / "language_generalization.json"
        ).read_text()
    )
    assert not [
        case["question"]
        for case in language_cases["intent"]
        if case["question"] in neural_specs.LANGUAGE_INTENT_SPEC
    ]
    assert not [
        case["input"]
        for case in language_cases["heard_expression"]
        if case["input"] in neural_specs.HEARD_EXPRESSION_SPEC
    ]
    assert not [
        case["input"]
        for case in language_cases["translation"]
        if case["input"] in neural_specs.TRANSLATION_SPEC
    ]


def test_neural_migration_purges_source_text_but_preserves_conversations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        now = "2026-07-17T00:00:00+00:00"
        conn.executescript(
            """
            CREATE TABLE experts (
                pack_id TEXT, role TEXT, program_id TEXT, compiler TEXT, spec TEXT
            );
            CREATE TABLE contexts (
                context_id TEXT PRIMARY KEY, name TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE context_sources (
                source_id TEXT PRIMARY KEY, context_id TEXT, title TEXT,
                content TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY, context_id TEXT, title TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT,
                kind TEXT DEFAULT 'text', content TEXT, payload TEXT,
                sources TEXT, pack_id TEXT, created_at TEXT
            );
            CREATE TABLE settings (
                key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO contexts VALUES ('legacy-context','Legacy',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO context_sources VALUES "
            "('legacy-source','legacy-context','Private','secret text',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO conversations VALUES "
            "('legacy-conversation','legacy-context','Keep me',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO messages VALUES "
            "('legacy-message','legacy-conversation','assistant','text','answer',"
            "'{}','[{\"source_id\":\"legacy-source\"}]',NULL,?)",
            (now,),
        )
        conn.commit()
        _migration_8(conn)
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "context_sources" not in tables
        assert "documents" not in tables
        conversation = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id='legacy-conversation'"
        ).fetchone()
        assert conversation is not None
        assert "context_id" not in conversation.keys()
        message_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(messages)")
        }
        assert "sources" not in message_columns
    finally:
        conn.close()
