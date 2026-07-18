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
    assert client.get("/api/starters").status_code == 404
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


def test_removed_builtin_programs_are_purged_from_existing_registries(client):
    del client
    conn = connect()
    try:
        now = "2026-07-18T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO neural_programs (
                program_key, role, display_name, built_in, status,
                created_at, updated_at
            ) VALUES ('builtin:old-followup','old_followup','Old rewriter',
                      1,'ready',?,?)
            """,
            (now, now),
        )
        conn.commit()
        program_registry.ensure_builtins(conn)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM neural_programs "
                "WHERE program_key='builtin:old-followup'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_standalone_questions_never_inherit_previous_context(
    client, monkeypatch
):
    queries = []

    def fake_answer_events(conn, query):
        del conn
        queries.append(query)
        yield {
            "type": "final",
            "answer": f"Answer {len(queries)}",
            "program_labels": ["broad"],
        }

    monkeypatch.setattr(
        "app.services.neural_answer_graph.answer_events",
        fake_answer_events,
    )
    first = client.post(
        "/api/ask", json={"text": "What does simida mean?"}
    ).json()
    second = client.post(
        "/api/ask", json={"text": "How do tides work?"}
    ).json()

    assert queries == ["What does simida mean?", "How do tides work?"]
    assert first["conversation_id"] != second["conversation_id"]
    assert first["used_context"] is False
    assert second["used_context"] is False
    history = client.get("/api/conversations").json()["conversations"]
    assert {item["question_count"] for item in history} == {1}


def test_follow_up_requires_an_explicit_answer_anchor(client, monkeypatch):
    queries = []

    def fake_answer_events(conn, query):
        del conn
        queries.append(query)
        answer = (
            "The MRT is usually the easiest way to cover longer distances."
            if len(queries) == 1
            else "Most MRT services end around midnight."
        )
        yield {
            "type": "final",
            "answer": answer,
            "program_labels": ["broad"],
        }

    monkeypatch.setattr(
        "app.services.neural_answer_graph.answer_events",
        fake_answer_events,
    )
    first = client.post(
        "/api/ask",
        json={"text": "What is the easiest way to get around Singapore?"},
    ).json()
    follow_up = client.post(
        "/api/ask",
        json={
            "text": "Does it run late?",
            "reply_to_message_id": first["message_id"],
        },
    ).json()

    assert follow_up["conversation_id"] == first["conversation_id"]
    assert follow_up["used_context"] is True
    assert "PREVIOUS_QUESTION: What is the easiest way" in queries[1]
    assert "PREVIOUS_ANSWER: The MRT is usually the easiest way" in queries[1]
    assert "FOLLOW_UP: Does it run late?" in queries[1]
    context_event = next(
        event for event in follow_up["events"] if event["type"] == "context"
    )
    assert context_event["strategy"] == "structured_context"

    thread = client.get(
        f"/api/conversations/{first['conversation_id']}"
    ).json()
    assert len([row for row in thread["messages"] if row["role"] == "user"]) == 2
    second_question = thread["messages"][2]
    assert (
        second_question["payload"]["reply_to_message_id"]
        == first["message_id"]
    )
    assert client.post(
        "/api/ask",
        json={
            "text": "What about this?",
            "reply_to_message_id": "missing-answer",
        },
    ).status_code == 404


def test_question_export_is_local_deduplicated_and_answer_opt_in(
    client, monkeypatch
):
    from scripts.export_question_candidates import export_questions

    def fake_answer_events(conn, query):
        del conn, query
        yield {
            "type": "final",
            "answer": "The answer remains local.",
            "program_labels": ["broad"],
        }

    monkeypatch.setattr(
        "app.services.neural_answer_graph.answer_events",
        fake_answer_events,
    )
    for _ in range(2):
        assert client.post(
            "/api/ask", json={"text": "Why is the Merlion a fish and lion?"}
        ).status_code == 200

    conn = connect()
    try:
        questions = export_questions(conn)
        with_answers = export_questions(conn, include_answers=True)
    finally:
        conn.close()
    item = next(
        row
        for row in questions
        if row["question"] == "Why is the Merlion a fish and lion?"
    )
    assert item["occurrences"] == 2
    assert item["program_labels"] == ["broad"]
    assert "answer" not in item
    answer_item = next(
        row
        for row in with_answers
        if row["question"] == "Why is the Merlion a fish and lion?"
    )
    assert answer_item["answer"] == "The answer remains local."
    assert "conversation_id" not in answer_item
    assert "message_id" not in answer_item


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
        lambda spec, compiler, **kwargs: (
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


def test_factual_pack_overrides_hallucinating_answer(client, monkeypatch):
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        topic = program_registry.ensure_topic(conn, "South Korea travel")
        program_registry.add_version(
            conn,
            program_key=topic["program_key"],
            program_id="prepared-korea",
            compiler="paw-ft-bs48",
            stage="finetuned",
            spec="South Korea travel specialist",
            contract_score=1.0,
            contract_result={},
            activate=True,
        )
        broad_id = program_registry.active(conn, "broad")["program_id"]
    finally:
        conn.close()

    def fake_run(program_id, text, **kwargs):
        del text, kwargs
        if program_id == broad_id:
            output = "South Korea's major cities are Seoul, Gangnam, and Gimpo."
        else:
            output = "Seoul, Gangnam, Incheon, and Gimpo."
        return ProgramResult(output, 1.0, 100.0, True)

    monkeypatch.setattr("app.services.program_runtime.run", fake_run)
    payload = client.post(
        "/api/ask",
        json={"text": "What are the major cities of South Korea?"},
    ).json()

    assert payload["support"] == "prepared_facts"
    assert payload["program_labels"] == ["country:south-korea"]
    assert "Busan" in payload["answer"] and "Daegu" in payload["answer"]
    assert payload["trace"]["factual_pack"]["pack_key"] == "country:south-korea"


def test_explicit_topic_name_routes_without_paw_matcher(client, monkeypatch):
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        topic = program_registry.ensure_topic(conn, "Singapore")
        program_registry.add_version(
            conn,
            program_key=topic["program_key"],
            program_id="prepared-singapore",
            compiler="paw-ft-bs48",
            stage="finetuned",
            spec="Singapore specialist",
            contract_score=1.0,
            contract_result={},
            activate=True,
        )
        ids = {
            role: program_registry.active(conn, role)["program_id"]
            for role in ("broad", "prepared_matcher")
        }
    finally:
        conn.close()
    calls = []

    def fake_run(program_id, text, **kwargs):
        del text, kwargs
        calls.append(program_id)
        if program_id == ids["prepared_matcher"]:
            raise AssertionError("Explicit topic should bypass PAW matcher")
        output = (
            "A broad answer."
            if program_id == ids["broad"]
            else "The prepared Singapore answer."
        )
        return ProgramResult(output, 1.0, 100.0, True)

    monkeypatch.setattr("app.services.program_runtime.run", fake_run)
    result = client.post(
        "/api/ask",
        json={"text": "What surprises first-time visitors about Singapore?"},
    ).json()
    assert result["answer"] == "The prepared Singapore answer."
    assert result["program_labels"] == [topic["program_key"]]
    assert ids["prepared_matcher"] not in calls
    matcher_trace = result["trace"]["prepared_matcher"][0]
    assert matcher_trace["output"] == "YES (explicit topic mention)"


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

    def fake_compile(spec, compiler, **kwargs):
        del kwargs
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


def test_user_prepared_programs_are_public(monkeypatch):
    standard_calls = []

    class Program:
        id = "public-standard"

    def fake_compile(spec, **kwargs):
        standard_calls.append((spec, kwargs))
        return Program()

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"program_id": "public-finetuned"}

    finetuned_calls = []

    def fake_post(url, **kwargs):
        finetuned_calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(neural_jobs.paw, "compile", fake_compile)
    monkeypatch.setattr(neural_jobs.httpx, "post", fake_post)
    monkeypatch.setattr(
        neural_jobs.PAWClient, "download_paw", lambda self, program_id: None
    )
    monkeypatch.setattr(
        neural_jobs,
        "contract_test",
        lambda program_id, spec: (1.0, {"program_id": program_id, "spec": spec}),
    )

    neural_jobs._compile("public topic spec", "paw-4b-qwen3-0.6b")
    neural_jobs._compile("public topic spec", "paw-ft-bs48")

    assert standard_calls[0][1]["public"] is True
    assert finetuned_calls[0][1]["json"]["public"] is True
    assert finetuned_calls[0][0].endswith("/api/v1/compile/async")


def test_finetuned_compile_reports_live_remote_progress(monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        @staticmethod
        def raise_for_status():
            return None

        def json(self):
            return self.payload

    statuses = iter(
        (
            {"job_id": "remote-job", "status": "compiling", "percent": 37},
            {
                "job_id": "remote-job",
                "status": "ready",
                "percent": 100,
                "program_id": "live-progress-program",
            },
        )
    )
    monkeypatch.setattr(
        neural_jobs.httpx,
        "post",
        lambda *args, **kwargs: Response(
            {"job_id": "remote-job", "status": "queued", "percent": 0}
        ),
    )
    monkeypatch.setattr(
        neural_jobs.httpx,
        "get",
        lambda *args, **kwargs: Response(next(statuses)),
    )
    monkeypatch.setattr(neural_jobs.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        neural_jobs.PAWClient, "download_paw", lambda self, program_id: None
    )
    monkeypatch.setattr(
        neural_jobs,
        "contract_test",
        lambda program_id, spec: (1.0, {"program_id": program_id, "spec": spec}),
    )
    progress = []

    program_id, score, _ = neural_jobs._compile(
        "live progress topic spec",
        "paw-ft-bs48",
        on_progress=lambda percent, state: progress.append((percent, state)),
    )

    assert program_id == "live-progress-program"
    assert score == 1.0
    assert progress == [
        (0, "queued"),
        (37, "compiling"),
        (100, "ready"),
    ]


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


def test_followup_selection_is_leakage_free_and_clears_release_gates(client):
    del client
    from eval.followup_specs import specifications

    eval_root = Path(__file__).resolve().parents[1] / "eval"
    cases = json.loads((eval_root / "followup_cases.json").read_text())
    for spec in specifications().values():
        assert not [
            case["id"]
            for case in cases
            if case["previous_question"] in spec or case["follow_up"] in spec
        ]

    report = json.loads((eval_root / "followup_report.json").read_text())
    assert report["selected_for_product"] == "structured_context"
    assert report["meaningful_dev_lift"] is False
    assert report["rewrite_fidelity_gate"] is False
    manifest = json.loads(Path(program_registry.PROGRAM_MANIFEST).read_text())
    assert "followup" not in manifest["shipping_roles"]
    assert "followup" not in manifest["programs"]


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
