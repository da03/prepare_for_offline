from __future__ import annotations


def create_context_with_source(client, name="ML Course"):
    context = client.post(
        "/api/contexts",
        json={
            "name": name,
            "context_type": "course",
            "goal": "Study without a connection",
            "interests": ["schedule"],
            "expected_needs": ["When is the final exam?"],
            "storage_budget_mb": 800,
        },
    ).json()
    source = client.post(
        f"/api/contexts/{context['context_id']}/sources",
        json={
            "title": "Course schedule",
            "content": "The final exam is Friday at 3 PM. Office hours are Tuesday.",
        },
    ).json()
    client.patch("/api/settings", json={"active_context_id": context["context_id"]})
    return context, source


def test_context_source_settings_and_history(client):
    context, source = create_context_with_source(client)

    detail = client.get(f"/api/contexts/{context['context_id']}").json()
    assert detail["name"] == "ML Course"
    assert detail["sources"][0]["source_id"] == source["source_id"]

    response = client.post(
        "/api/command", json={"text": "what are my history conversations?"}
    ).json()
    assert response["kind"] == "ui_action"
    assert response["action"] == "show_history"

    history = client.get("/api/conversations").json()["conversations"]
    assert len(history) == 1
    conversation = client.get(
        f"/api/conversations/{history[0]['conversation_id']}"
    ).json()
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
    ]


def test_commands_are_allowlisted_and_destructive_actions_confirm(client):
    context, _ = create_context_with_source(client)

    switch = client.post(
        "/api/command",
        json={"text": "switch to my ML Course context"},
    ).json()
    assert switch["action"] == "switch_context"
    assert switch["data"]["context"]["context_id"] == context["context_id"]

    preview = client.post(
        "/api/command",
        json={"text": "remove my ML Course context"},
    ).json()
    assert preview["kind"] == "clarification"
    assert preview["requires_confirmation"] is True
    assert client.get(f"/api/contexts/{context['context_id']}").status_code == 200

    deleted = client.post(
        "/api/command",
        json={"text": "remove my ML Course context", "confirmed": True},
    ).json()
    assert deleted["action"] == "delete_context"
    assert client.get(f"/api/contexts/{context['context_id']}").status_code == 404


def test_plan_is_context_scoped_and_editable(client):
    context, source = create_context_with_source(client)
    response = client.post(
        f"/api/contexts/{context['context_id']}/plan",
        json={
            "selected_source_ids": [source["source_id"]],
            "expected_questions": ["When is the final exam?"],
            "compile_expert": False,
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["context_id"] == context["context_id"]
    assert plan["selected_source_ids"] == [source["source_id"]]
    assert plan["expected_questions"] == ["When is the final exam?"]
    assert plan["fits_budget"] is True
    assert "personal context remain on this Mac" in " ".join(
        plan["privacy_disclosures"]
    )


def test_no_context_is_safely_routed_to_builder(client):
    response = client.post(
        "/api/command", json={"text": "What should I remember for tomorrow?"}
    ).json()
    assert response["kind"] == "workflow"
    assert response["action"] == "create_context"


def test_korea_is_an_explicit_template_not_a_hidden_default(client):
    assert client.get("/api/contexts").json()["contexts"] == []
    templates = client.get("/api/templates").json()["templates"]
    assert [template["template_id"] for template in templates] == ["korea"]
    context = client.post(
        "/api/contexts",
        json={"name": "Korea trip", "template_id": "korea"},
    ).json()
    assert context["template_id"] == "korea"
    assert context["context_type"] == "trip"
    assert context["languages"] == ["en", "ko"]


def test_tauri_origin_is_allowed(client):
    response = client.options(
        "/api/contexts",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-app-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_search_key_can_be_configured_locally(client, isolated_home, monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    initial = client.get("/api/settings/search").json()
    assert initial["configured"] is False
    saved = client.put(
        "/api/settings/search",
        json={"api_key": "brave-test-key-123456"},
    ).json()
    assert saved["configured"] is True
    path = isolated_home / "brave_search_api_key"
    assert path.read_text() == "brave-test-key-123456"
    assert path.stat().st_mode & 0o777 == 0o600
    removed = client.delete("/api/settings/search").json()
    assert removed["configured"] is False
