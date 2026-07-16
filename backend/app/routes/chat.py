"""Offline chat endpoint."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from ..db import connect
from ..models import ChatRequest, ChatResponse
from ..security import require_token
from ..services import answerer, contexts, conversations

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    conn = connect()
    try:
        context_id = req.context_id
        if not context_id:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='active_context_id'"
            ).fetchone()
            if row:
                try:
                    context_id = json.loads(row["value"])
                except json.JSONDecodeError:
                    context_id = None

        pack_id = req.pack_id
        if not pack_id and context_id:
            context = contexts.get(conn, context_id)
            pack_id = context["active_pack_id"] if context else None
        if not pack_id:
            raise HTTPException(
                status_code=409,
                detail="No prepared context is active. Create or prepare a context first.",
            )

        conversation = conversations.ensure(conn, req.conversation_id, context_id)
        user_message = conversations.add_message(
            conn,
            conversation["conversation_id"],
            role="user",
            content=req.question,
            pack_id=pack_id,
        )
        result = answerer.answer_question(
            conn,
            pack_id,
            req.question,
            conversation_id=conversation["conversation_id"],
            message_id=user_message["message_id"],
        )
        assistant_message = conversations.add_message(
            conn,
            conversation["conversation_id"],
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
        result["conversation_id"] = conversation["conversation_id"]
        result["message_id"] = assistant_message["message_id"]
    finally:
        conn.close()
    return ChatResponse(**result)
