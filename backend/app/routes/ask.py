"""Progressive PAW-program-only Ask API."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..db import connect
from ..models import AskRequest
from ..security import require_token
from ..services import (
    conversations,
    followups,
    neural_answer_graph,
    program_registry,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _session(req: AskRequest) -> tuple[dict, dict]:
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        conversation = conversations.ensure(conn, req.conversation_id)
        rewritten = followups.rewrite(
            conn,
            conversation["conversation_id"],
            req.text,
            new_topic=req.new_topic,
            max_prior_turns=3,
        )
        conversations.add_message(
            conn,
            conversation["conversation_id"],
            role="user",
            content=req.text,
            kind="text",
            payload={
                "standalone_query": rewritten["query"],
                "used_context": rewritten["used_context"],
                "previous_question": rewritten["previous_question"],
            },
        )
        return conversation, rewritten
    finally:
        conn.close()


def _events(req: AskRequest, session=None) -> Iterator[dict]:
    conversation, rewritten = session or _session(req)
    if rewritten["used_context"]:
        yield {"type": "context", "used_context": True}

    conn = connect()
    final = None
    try:
        for event in neural_answer_graph.answer_events(conn, rewritten["query"]):
            if event["type"] == "final":
                final = event
                assistant = conversations.add_message(
                    conn,
                    conversation["conversation_id"],
                    role="assistant",
                    kind="answer",
                    content=event["answer"],
                    payload={
                        "answer_mode": "neural_program",
                        "program_labels": event.get("program_labels", []),
                        "refined": event.get("refined", False),
                        "used_context": rewritten["used_context"],
                        "new_topic": req.new_topic,
                        "trace": event.get("trace", {}),
                    },
                )
                event = {
                    **event,
                    "conversation_id": conversation["conversation_id"],
                    "message_id": assistant["message_id"],
                    "used_context": rewritten["used_context"],
                }
            yield event
    finally:
        conn.close()
    if final is None:
        raise RuntimeError("PAW answer graph produced no final answer")


def _sse(req: AskRequest, session):
    for event in _events(req, session):
        name = event.get("type", "message")
        yield f"event: {name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/api/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    return StreamingResponse(
        _sse(req, _session(req)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/ask")
def ask(req: AskRequest) -> dict:
    last = None
    events = []
    for event in _events(req, _session(req)):
        events.append(event)
        if event.get("type") == "final":
            last = event
    if last is None:
        raise HTTPException(status_code=500, detail="No final PAW answer")
    return {**last, "events": events}
