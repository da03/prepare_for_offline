"""Progressive travel Ask API."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..db import connect
from ..models import AskRequest
from ..security import require_token
from ..services import (
    answerer,
    contexts,
    conversations,
    followups,
    preferences,
    travel_pipeline,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _session(req: AskRequest) -> tuple[dict, dict, dict, dict]:
    conn = connect()
    try:
        trip = contexts.get(conn, req.trip_id)
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found")
        pack_id = trip.get("active_pack_id")
        if not pack_id:
            raise HTTPException(
                status_code=409, detail="Prepare this trip before asking offline questions."
            )
        conversation = conversations.ensure(conn, req.conversation_id, req.trip_id)
        history_window = int(
            preferences.get_all(conn).get("ask_history_window", 3)
        )
        rewritten = followups.rewrite(
            conn,
            conversation["conversation_id"],
            req.text,
            new_topic=req.new_topic,
            max_prior_turns=history_window,
        )
        user_message = conversations.add_message(
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
            pack_id=pack_id,
        )
        return trip, conversation, user_message, rewritten
    finally:
        conn.close()


def _events(req: AskRequest, session=None) -> Iterator[dict]:
    trip, conversation, user_message, rewritten = session or _session(req)
    pack_id = trip["active_pack_id"]
    if rewritten["used_context"]:
        yield {
            "type": "context",
            "used_context": True,
            "previous_question": rewritten["previous_question"],
        }
    final_event = None
    for event in travel_pipeline.stream_answer(pack_id, rewritten["query"], trip):
        if event["type"] in {"final", "abstain"}:
            final_event = event
            answer = event["result"]
            conn = connect()
            try:
                if event["type"] == "abstain":
                    answerer._enqueue(
                        conn,
                        pack_id,
                        req.text,
                        None,
                        "low",
                        [],
                        conversation["conversation_id"],
                        user_message["message_id"],
                    )
                assistant = conversations.add_message(
                    conn,
                    conversation["conversation_id"],
                    role="assistant",
                    kind="answer",
                    content=answer["answer"],
                    payload={
                        "support": answer["support"],
                        "answer_mode": answer["answer_mode"],
                        "stale": answer["stale"],
                        "queued_for_verification": event["type"] == "abstain",
                        "refined": event.get("refined", False),
                        "merge": event.get("merge"),
                        "branches": event.get("branches", []),
                        "used_context": rewritten["used_context"],
                        "new_topic": req.new_topic,
                        "freshness": next(
                            (
                                source.get("freshness")
                                or source.get("as_of")
                                for source in answer["sources"]
                                if source.get("freshness") or source.get("as_of")
                            ),
                            None,
                        ),
                    },
                    sources=answer["sources"],
                    pack_id=pack_id,
                )
            finally:
                conn.close()
            event = {
                **event,
                "conversation_id": conversation["conversation_id"],
                "message_id": assistant["message_id"],
                "used_context": rewritten["used_context"],
            }
        yield event
    if final_event is None:
        result = {
            "answer": "I could not complete that answer from the offline trip.",
            "support": "low",
            "answer_mode": "abstained",
            "sources": [],
            "stale": False,
        }
        yield {
            "type": "abstain",
            "answer": result["answer"],
            "result": result,
            "conversation_id": conversation["conversation_id"],
        }


def _sse(req: AskRequest, session):
    for event in _events(req, session):
        event_name = event.get("type", "message")
        yield f"event: {event_name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/api/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    session = _session(req)
    return StreamingResponse(
        _sse(req, session),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/ask")
def ask(req: AskRequest) -> dict:
    session = _session(req)
    last = None
    events = []
    for event in _events(req, session):
        events.append(event)
        if event.get("type") in {"final", "abstain"}:
            last = event
    if last is None:
        raise HTTPException(status_code=500, detail="No final answer event")
    return {**last, "events": events}
