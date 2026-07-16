from __future__ import annotations

from app.services import commands


def test_common_intents_do_not_need_a_model():
    cases = {
        "what are my history conversations?": "show_history",
        "start a fresh chat": "new_conversation",
        "change my privacy preferences": "show_settings",
        "how much disk space is this using?": "show_storage",
        "which questions still need verification?": "show_unresolved",
        "make this context available offline": "prepare_context",
    }
    for text, expected in cases.items():
        intent = commands.deterministic_intent(text)
        assert intent is not None
        assert intent.action == expected


def test_invalid_paw_output_falls_back_to_answer(monkeypatch, isolated_home):
    from app.db import connect, init_db

    init_db()
    conn = connect()
    monkeypatch.setattr(commands.paw_experts, "run_ui_router", lambda *_: "run_shell")
    intent = commands.route_intent(conn, "do the strange thing")
    assert intent.action == "answer_question"
    conn.close()
