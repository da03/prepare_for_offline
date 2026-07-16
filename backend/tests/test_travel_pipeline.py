from __future__ import annotations

import json

from app.models import ContextCreate, ContextSourceCreate


def test_one_sentence_trip_parser():
    from app.services.trip_parser import parse

    trip = parse("I'm going to ICML 2026 in Seoul with my kids")
    assert trip.event == "ICML 2026"
    assert trip.destination == "Seoul"
    assert trip.languages == ["en", "ko"]
    assert "child-friendly options" in trip.traveler_needs
    assert trip.blocking_question is None
    assert any("official" in query for query in trip.suggested_queries)


def test_trip_parse_api_creates_editable_trip_and_private_attachment(client):
    response = client.post(
        "/api/trips/parse",
        json={
            "text": "I'm going to ICML 2026 in Seoul",
            "attachments": [
                {
                    "name": "My itinerary.txt",
                    "content": "Hotel check-in is 3 PM.",
                    "media_type": "text/plain",
                }
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    trip = payload["trip"]
    assert trip["trip_brief"]["event"] == "ICML 2026"
    assert trip["trip_brief"]["destination"] == "Seoul"
    assert trip["sources"][0]["metadata"]["private"] is True
    assert payload["blocking_question"] is None


def test_bounded_followup_rewrite(isolated_home):
    from app.db import connect, init_db
    from app.services import conversations, followups

    init_db()
    conn = connect()
    conversation = conversations.create(conn)
    conversations.add_message(
        conn,
        conversation["conversation_id"],
        role="user",
        content="How do I reach the conference venue?",
    )
    rewritten = followups.rewrite(
        conn, conversation["conversation_id"], "What about Sunday?"
    )
    assert rewritten["used_context"] is True
    assert "conference venue" in rewritten["query"]
    reset = followups.rewrite(
        conn,
        conversation["conversation_id"],
        "What about Sunday?",
        new_topic=True,
    )
    assert reset["used_context"] is False
    conn.close()


def test_program_tree_routes_and_progressively_answers(isolated_home, monkeypatch):
    from app.db import connect, init_db
    from app.services import contexts, jobs, paw_experts, travel_pipeline

    monkeypatch.setattr(paw_experts, "ensure_ui_router_cached", lambda: None)
    monkeypatch.setattr(paw_experts, "ensure_global_programs_cached", lambda: None)
    monkeypatch.setattr(paw_experts, "run_global", lambda *args, **kwargs: None)
    init_db()
    conn = connect()
    context = contexts.create(
        conn,
        ContextCreate(
            name="ICML 2026",
            context_type="conference",
            goal="Attend ICML in Seoul",
            languages=["en", "ko"],
            interests=["event schedule and venue", "arrival and transit"],
            expected_needs=["Where is the keynote?"],
            storage_budget_mb=800,
        ),
    )
    brief = {
        "event": "ICML 2026",
        "destination": "Seoul",
        "coverage": ["event schedule and venue", "arrival and transit"],
    }
    conn.execute(
        "UPDATE contexts SET trip_brief=? WHERE context_id=?",
        (json.dumps(brief), context["context_id"]),
    )
    source = contexts.add_source(
        conn,
        context["context_id"],
        ContextSourceCreate(
            title="ICML schedule",
            content="The keynote is at 9 AM in Hall A. Workshops are in Hall B.",
            metadata={"topic": "event", "publisher": "ICML official"},
        ),
    )
    raw = {
        "context_id": context["context_id"],
        "selected_source_ids": [source["source_id"]],
        "expected_questions": ["Where is the keynote?"],
        "compile_expert": False,
        "cache_ui_router": False,
        "optimize": False,
    }
    job_id = jobs.create_job(context["context_id"], raw)
    jobs._run(job_id, raw)
    trip = contexts.get(conn, context["context_id"])
    events = list(
        travel_pipeline.stream_answer(
            trip["active_pack_id"], "Where is the keynote?", trip
        )
    )
    assert events[0]["type"] in {"answer_update", "route"}
    assert any(event["type"] == "branch_complete" for event in events)
    final = [event for event in events if event["type"] in {"final", "abstain"}][-1]
    assert final["type"] == "final"
    assert "Hall A" in final["result"]["answer"]
    assert final["result"]["sources"]
    conn.close()
