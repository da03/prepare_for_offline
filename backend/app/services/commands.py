"""Natural-language command orchestration.

Common UI intents are deterministic and instant. A single reusable PAW router
is a lazy fuzzy fallback for paraphrases. Every returned action is validated
against the allowlisted registry below; model output is never executed as code.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..models import CommandRequest, SettingsUpdate
from . import answerer, contexts, conversations, paw_experts, preferences


@dataclass
class Intent:
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = "deterministic"


ACTION_REGISTRY = {
    "show_history",
    "new_conversation",
    "switch_context",
    "create_context",
    "prepare_context",
    "add_source",
    "show_context_status",
    "show_settings",
    "show_unresolved",
    "show_storage",
    "delete_context",
    "answer_question",
}


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s-]", " ", text.lower()).split())


def deterministic_intent(text: str) -> Intent | None:
    t = _normalized(text)
    if (
        t in {"history", "show history", "open history"}
        or
        "conversation history" in t
        or "chat history" in t
        or re.search(r"\b(discussed|talked|asked)\b.*\b(before|earlier|previously)\b", t)
        or re.search(r"\b(history|old|previous|past)\b.*\b(chat|conversation)s?\b", t)
        or re.search(r"\b(chat|conversation)s?\b.*\b(history|old|previous|past)\b", t)
    ):
        return Intent("show_history")
    if re.search(r"\b(new|fresh|start)\b.*\b(chat|conversation)\b", t):
        return Intent("new_conversation")
    if re.search(r"\b(delete|remove)\b.*\b(context|space|pack)\b", t):
        return Intent("delete_context", {"target": _extract_target(t)})
    if re.search(r"\b(switch|use|open|change)\b.*\b(context|space|pack)\b", t):
        return Intent("switch_context", {"target": _extract_target(t)})
    if (
        re.search(r"\b(create|new)\b.*\b(context|space|offline pack)\b", t)
        or t.startswith("prepare me for ")
        or t.startswith("set me up for ")
    ):
        return Intent("create_context", {"prompt": text.strip()})
    if (
        re.search(r"\b(add|attach|include|import)\b.*\b(source|file|document|pdf|page|material|notes?)\b", t)
        or t in {"add source", "attach file"}
    ):
        return Intent("add_source")
    if re.search(r"\b(setting|settings|preference|preferences|privacy|appearance|theme)\b", t):
        return Intent("show_settings")
    if re.search(r"\b(unresolved|unverified|pending|verify|verification)\b", t):
        return Intent("show_unresolved")
    if (
        re.search(r"\b(storage|disk|space|size)\b", t)
        and re.search(r"\b(use|using|used|left|much|large|size|storage)\b", t)
    ):
        return Intent("show_storage")
    if (
        re.search(r"\b(status|ready|readiness|progress)\b", t)
        and re.search(r"\b(context|offline|pack|preparation|it)\b", t)
    ):
        return Intent("show_context_status")
    if (
        t in {"prepare", "prepare offline", "build offline context"}
        or
        re.search(r"\b(prepare|build|compile|download|make)\b", t)
        and re.search(r"\b(context|pack|offline|it|this)\b", t)
    ):
        return Intent("prepare_context")
    return None


def _extract_target(text: str) -> str:
    cleaned = re.sub(
        r"\b(switch|use|open|change|delete|remove|to|my|the|context|space|pack)\b",
        " ",
        text,
    )
    return " ".join(cleaned.split())


def _draft_context(prompt: str) -> dict:
    normalized = _normalized(prompt)
    context_type = "custom"
    for candidate, words in {
        "trip": ("trip", "travel", "vacation", "flight"),
        "conference": ("conference", "convention", "summit"),
        "course": ("course", "class", "study", "school"),
        "project": ("project", "work"),
        "emergency": ("emergency", "disaster", "evacuation"),
    }.items():
        if any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in words):
            context_type = candidate
            break
    name = re.sub(
        r"^(prepare me for|set me up for|create (a |an )?(new )?(context|space) (for )?)",
        "",
        prompt.strip(),
        flags=re.IGNORECASE,
    ).strip(" .") or "New offline context"
    return {
        "name": name[:120],
        "context_type": context_type,
        "goal": prompt.strip(),
        "languages": [],
        "interests": [],
        "expected_needs": [],
        "storage_budget_mb": 1200,
        "privacy_mode": "local_only",
        "preparation_quality": "fast",
    }


def route_intent(conn: sqlite3.Connection, text: str) -> Intent:
    direct = deterministic_intent(text)
    if direct:
        return direct
    normalized = _normalized(text)
    if text.rstrip().endswith("?") or re.match(
        r"^(what|how|why|when|where|who|which|can|could|does|do|is|are|should)\b",
        normalized,
    ):
        return Intent("answer_question")
    fuzzy = paw_experts.run_ui_router(conn, text)
    if fuzzy in ACTION_REGISTRY:
        return Intent(fuzzy, source="paw")
    return Intent("answer_question")


def _active_context_id(conn: sqlite3.Connection, requested: str | None) -> str | None:
    if requested and contexts.get(conn, requested):
        return requested
    settings = preferences.get_all(conn)
    active = settings.get("active_context_id")
    if active and contexts.get(conn, str(active)):
        return str(active)
    all_contexts = contexts.list_all(conn)
    return all_contexts[0]["context_id"] if len(all_contexts) == 1 else None


def _match_context(conn: sqlite3.Connection, target: str | None) -> tuple[dict | None, list[dict]]:
    all_contexts = contexts.list_all(conn)
    if not all_contexts:
        return None, []
    if not target:
        return (all_contexts[0], []) if len(all_contexts) == 1 else (None, all_contexts)
    norm = _normalized(target)
    exact = [
        c for c in all_contexts
        if norm == _normalized(c["name"]) or norm in _normalized(c["name"])
    ]
    if len(exact) == 1:
        return exact[0], []
    names = {_normalized(c["name"]): c for c in all_contexts}
    close = difflib.get_close_matches(norm, list(names), n=3, cutoff=0.45)
    if len(close) == 1:
        return names[close[0]], []
    candidates = [names[name] for name in close] or all_contexts
    return None, candidates


def _persist_action(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    kind: str,
    message: str,
    action: str,
    data: dict | None = None,
) -> dict:
    return conversations.add_message(
        conn,
        conversation_id,
        role="assistant",
        kind=kind,
        content=message,
        payload={"action": action, "data": data or {}},
    )


def _base_response(kind: str, conversation_id: str, **kwargs) -> dict:
    return {
        "kind": kind,
        "conversation_id": conversation_id,
        "message_id": None,
        "message": "",
        "action": None,
        "arguments": {},
        "data": {},
        "requires_confirmation": False,
        "answer": None,
        "support": None,
        "answer_mode": None,
        "sources": [],
        "stale": False,
        "queued_for_verification": False,
        **kwargs,
    }


def execute(conn: sqlite3.Connection, req: CommandRequest) -> dict:
    context_id = _active_context_id(conn, req.context_id)
    intent = route_intent(conn, req.text)

    if intent.action == "new_conversation":
        conversation = conversations.create(conn, context_id)
        message = _persist_action(
            conn,
            conversation["conversation_id"],
            kind="ui_action",
            message="Started a new conversation.",
            action="new_conversation",
        )
        return _base_response(
            "ui_action",
            conversation["conversation_id"],
            message_id=message["message_id"],
            message=message["content"],
            action="new_conversation",
        )

    conversation = conversations.ensure(conn, req.conversation_id, context_id)
    user_message = conversations.add_message(
        conn, conversation["conversation_id"], role="user", content=req.text
    )
    conversation_id = conversation["conversation_id"]

    if intent.action == "show_history":
        history = conversations.list_all(conn)
        message = "Here are your recent conversations."
        saved = _persist_action(
            conn, conversation_id, kind="ui_action", message=message,
            action=intent.action, data={"conversations": history},
        )
        return _base_response(
            "ui_action", conversation_id, message_id=saved["message_id"],
            message=message, action=intent.action,
            data={"conversations": history},
        )

    if intent.action == "show_settings":
        data = preferences.get_all(conn)
        saved = _persist_action(
            conn, conversation_id, kind="ui_action",
            message="Opened settings.", action=intent.action, data=data,
        )
        return _base_response(
            "ui_action", conversation_id, message_id=saved["message_id"],
            message=saved["content"], action=intent.action, data=data,
        )

    if intent.action == "show_storage":
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS b FROM packs"
        ).fetchone()
        data = {"pack_count": int(row["n"]), "total_bytes": int(row["b"])}
        message = (
            f"Your offline contexts use {data['total_bytes'] / (1024 * 1024):.1f} MB."
        )
        saved = _persist_action(
            conn, conversation_id, kind="ui_action", message=message,
            action=intent.action, data=data,
        )
        return _base_response(
            "ui_action", conversation_id, message_id=saved["message_id"],
            message=message, action=intent.action, data=data,
        )

    if intent.action == "show_unresolved":
        rows = conn.execute(
            "SELECT * FROM question_queue WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
        items = [
            {
                "id": row["id"],
                "question": row["question"],
                "offline_answer": row["offline_answer"],
                "offline_support": row["offline_support"],
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        message = (
            f"{len(items)} answer{'s' if len(items) != 1 else ''} still "
            f"{'need' if len(items) != 1 else 'needs'} verification."
        )
        saved = _persist_action(
            conn, conversation_id, kind="ui_action", message=message,
            action=intent.action, data={"items": items},
        )
        return _base_response(
            "ui_action", conversation_id, message_id=saved["message_id"],
            message=message, action=intent.action, data={"items": items},
        )

    if intent.action in {"create_context", "add_source"}:
        if intent.action == "add_source" and not context_id:
            message = "Create a context first, then add sources to it."
            saved = _persist_action(
                conn,
                conversation_id,
                kind="workflow",
                message=message,
                action="create_context",
            )
            return _base_response(
                "workflow",
                conversation_id,
                message_id=saved["message_id"],
                message=message,
                action="create_context",
            )
        message = (
            "Tell me what you need offline, then review the editable context."
            if intent.action == "create_context"
            else "Choose a file, paste text, or save the current web page."
        )
        data = (
            {
                "prompt": intent.arguments.get("prompt", req.text),
                "draft": _draft_context(intent.arguments.get("prompt", req.text)),
            }
            if intent.action == "create_context"
            else {}
        )
        saved = _persist_action(
            conn, conversation_id, kind="workflow", message=message,
            action=intent.action, data=data,
        )
        return _base_response(
            "workflow", conversation_id, message_id=saved["message_id"],
            message=message, action=intent.action, data=data,
        )

    if intent.action in {"switch_context", "delete_context"}:
        matched, candidates = _match_context(conn, intent.arguments.get("target"))
        if not matched:
            data = {"candidates": candidates}
            message = (
                "Which context do you mean?"
                if candidates else "You do not have any contexts yet."
            )
            saved = _persist_action(
                conn, conversation_id, kind="clarification", message=message,
                action=intent.action, data=data,
            )
            return _base_response(
                "clarification", conversation_id, message_id=saved["message_id"],
                message=message, action=intent.action, data=data,
            )
        if intent.action == "delete_context" and not req.confirmed:
            message = f"Delete “{matched['name']}” and all of its local data?"
            data = {"context": matched}
            saved = _persist_action(
                conn, conversation_id, kind="clarification", message=message,
                action=intent.action, data=data,
            )
            return _base_response(
                "clarification", conversation_id, message_id=saved["message_id"],
                message=message, action=intent.action, data=data,
                requires_confirmation=True,
                arguments={"target": matched["context_id"]},
            )
        if intent.action == "delete_context":
            contexts.delete(conn, matched["context_id"])
            message = f"Deleted “{matched['name']}”."
        else:
            preferences.update(
                conn,
                SettingsUpdate(active_context_id=matched["context_id"]),
            )
            conn.execute(
                "UPDATE conversations SET context_id=? WHERE conversation_id=?",
                (matched["context_id"], conversation_id),
            )
            conn.commit()
            context_id = matched["context_id"]
            message = f"Switched to “{matched['name']}”."
        saved = _persist_action(
            conn, conversation_id, kind="ui_action", message=message,
            action=intent.action, data={"context": matched},
        )
        return _base_response(
            "ui_action", conversation_id, message_id=saved["message_id"],
            message=message, action=intent.action, data={"context": matched},
        )

    if intent.action in {"prepare_context", "show_context_status"}:
        context = contexts.get(conn, context_id) if context_id else None
        if not context:
            message = "Create a context first so I know what to prepare."
            saved = _persist_action(
                conn, conversation_id, kind="workflow", message=message,
                action="create_context",
            )
            return _base_response(
                "workflow", conversation_id, message_id=saved["message_id"],
                message=message, action="create_context",
            )
        action = intent.action
        message = (
            f"Review what will be prepared for “{context['name']}”."
            if action == "prepare_context"
            else f"“{context['name']}” is {context['status']}."
        )
        kind = "workflow" if action == "prepare_context" else "ui_action"
        saved = _persist_action(
            conn, conversation_id, kind=kind, message=message,
            action=action, data={"context": context},
        )
        return _base_response(
            kind, conversation_id, message_id=saved["message_id"],
            message=message, action=action, data={"context": context},
        )

    # Default: grounded answer against the active context's current pack.
    context = contexts.get(conn, context_id) if context_id else None
    pack_id = context["active_pack_id"] if context else None
    if not pack_id:
        message = "Create and prepare a context before asking offline questions."
        saved = _persist_action(
            conn, conversation_id, kind="workflow", message=message,
            action="create_context",
        )
        return _base_response(
            "workflow", conversation_id, message_id=saved["message_id"],
            message=message, action="create_context",
        )
    result = answerer.answer_question(
        conn,
        pack_id,
        req.text,
        conversation_id=conversation_id,
        message_id=user_message["message_id"],
    )
    assistant = conversations.add_message(
        conn,
        conversation_id,
        role="assistant",
        kind="answer",
        content=result["answer"],
        payload={
            "support": result["support"],
            "answer_mode": result["answer_mode"],
            "stale": result["stale"],
            "queued_for_verification": result["queued_for_verification"],
        },
        sources=result["sources"],
        pack_id=pack_id,
    )
    return _base_response(
        "answer",
        conversation_id,
        message_id=assistant["message_id"],
        message=result["answer"],
        answer=result["answer"],
        support=result["support"],
        answer_mode=result["answer_mode"],
        sources=result["sources"],
        stale=result["stale"],
        queued_for_verification=result["queued_for_verification"],
    )
