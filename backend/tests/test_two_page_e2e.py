from __future__ import annotations

import time


def test_one_sentence_prepare_then_bounded_ask(client, monkeypatch):
    from app.services import paw_experts

    monkeypatch.setattr(paw_experts, "ensure_ui_router_cached", lambda: None)
    monkeypatch.setattr(paw_experts, "ensure_global_programs_cached", lambda: None)
    monkeypatch.setattr(paw_experts, "run_global", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        paw_experts,
        "compile_expert_version",
        lambda *args, **kwargs: {
            "version_id": "pv-test-fast",
            "role": kwargs["role"],
            "program_id": "test-fast",
            "compiler": "standard",
            "stage": "fast",
            "score": 1.0,
            "metrics": {},
        },
    )
    monkeypatch.setattr(paw_experts, "activate_version", lambda *args, **kwargs: None)

    parsed = client.post(
        "/api/trips/parse",
        json={
            "text": "I'm going to ICML 2026 in Seoul",
            "attachments": [
                {
                    "name": "ICML schedule",
                    "content": (
                        "The keynote is at 9 AM in Hall A. "
                        "Workshops are in Hall B on Sunday."
                    ),
                }
            ],
        },
    ).json()
    trip = parsed["trip"]
    trip_id = trip["trip_id"]
    assert trip["destination"] == "Seoul"
    assert parsed["blocking_question"] is None

    discovery = client.post(f"/api/trips/{trip_id}/discover").json()
    # Search is optional: without a configured key the gap is visible and
    # preparation continues using local attachments.
    assert "gaps" in discovery

    sources = client.get(f"/api/trips/{trip_id}").json()["sources"]
    started = client.post(
        f"/api/trips/{trip_id}/prepare",
        json={
            "source_ids": [source["source_id"] for source in sources],
            "optimize": False,
            "discover": False,
        },
    ).json()
    for _ in range(1000):
        job = client.get(f"/api/jobs/{started['job_id']}").json()
        if job["state"] in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert job["state"] == "ready", job

    first = client.post(
        "/api/ask",
        json={"trip_id": trip_id, "text": "Where is the keynote?"},
    ).json()
    assert first["type"] == "final"
    assert "Hall A" in first["answer"]
    assert first["sources"]

    second = client.post(
        "/api/ask",
        json={
            "trip_id": trip_id,
            "conversation_id": first["conversation_id"],
            "text": "What about the workshops?",
        },
    ).json()
    assert second["type"] == "final"
    assert "Hall B" in second["answer"]
    assert any(event.get("type") == "context" for event in second["events"])

    stream = client.post(
        "/api/ask/stream",
        json={"trip_id": trip_id, "text": "How do I get there?"},
    )
    assert stream.status_code == 200
    assert "event: route" in stream.text
    assert "event: final" in stream.text or "event: abstain" in stream.text
