from __future__ import annotations

import time


def test_online_prepare_offline_answer_history_and_reconnect(
    client, monkeypatch
):
    from app.services import paw_experts

    # The release build downloads this during online preparation; unit tests
    # avoid network and verify the rest of the lifecycle.
    monkeypatch.setattr(paw_experts, "ensure_ui_router_cached", lambda: None)

    context = client.post(
        "/api/contexts",
        json={
            "name": "Conference",
            "context_type": "conference",
            "goal": "Use the schedule offline",
            "expected_needs": ["When is the keynote?"],
            "storage_budget_mb": 800,
        },
    ).json()
    client.patch("/api/settings", json={"active_context_id": context["context_id"]})
    source = client.post(
        f"/api/contexts/{context['context_id']}/sources",
        json={
            "title": "Conference schedule",
            "content": "The keynote starts at 9 AM in Hall A.",
        },
    ).json()

    plan = client.post(
        f"/api/contexts/{context['context_id']}/plan",
        json={
            "selected_source_ids": [source["source_id"]],
            "expected_questions": ["When is the keynote?"],
            "compile_expert": False,
        },
    ).json()
    assert plan["fits_budget"] is True

    started = client.post(
        f"/api/contexts/{context['context_id']}/prepare",
        json={
            "selected_source_ids": [source["source_id"]],
            "expected_questions": ["When is the keynote?"],
            "compile_expert": False,
        },
    ).json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{started['job_id']}").json()
        if job["state"] in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.02)
    assert job["state"] == "ready", job

    answer = client.post(
        "/api/command", json={"text": "When is the keynote?"}
    ).json()
    assert answer["kind"] == "answer"
    assert "9 AM" in answer["answer"]
    assert answer["sources"][0]["title"] == "Conference schedule"
    conversation_id = answer["conversation_id"]

    unknown = client.post(
        "/api/command",
        json={
            "text": "Who won the 2018 World Cup?",
            "conversation_id": conversation_id,
        },
    ).json()
    assert unknown["queued_for_verification"] is True
    queue = client.get("/api/queue").json()["items"]
    assert queue[0]["conversation_id"] == conversation_id
    client.post(
        "/api/command",
        json={
            "text": "Show unresolved answers",
            "conversation_id": conversation_id,
        },
    )

    verified = client.post(
        f"/api/queue/{queue[0]['id']}/verify",
        json={"verified_answer": "France won the 2018 FIFA World Cup."},
    ).json()
    assert verified["changed"] is True
    messages = client.get(
        f"/api/conversations/{conversation_id}"
    ).json()["messages"]
    unresolved = [
        message
        for message in messages
        if message["payload"].get("action") == "show_unresolved"
    ][-1]
    assert unresolved["content"] == "All listed answers have been reviewed."
    assert unresolved["payload"]["data"]["items"][0]["status"] == "verified"
    assert not any(message["kind"] == "verification" for message in messages)
