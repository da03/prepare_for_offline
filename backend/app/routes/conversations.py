"""Conversation history CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import connect
from ..models import ConversationCreate, ConversationUpdate
from ..security import require_token
from ..services import conversations

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/conversations")
def list_conversations(
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    conn = connect()
    try:
        return {
            "conversations": conversations.list_all(
                conn, query=q, limit=limit
            )
        }
    finally:
        conn.close()


@router.post("/api/conversations")
def create_conversation(req: ConversationCreate) -> dict:
    conn = connect()
    try:
        return conversations.create(conn, req.title)
    finally:
        conn.close()


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    conn = connect()
    try:
        result = conversations.get(conn, conversation_id)
        if not result:
            raise HTTPException(status_code=404, detail="Conversation not found")
        result["messages"] = conversations.messages(conn, conversation_id)
        return result
    finally:
        conn.close()


@router.patch("/api/conversations/{conversation_id}")
def update_conversation(conversation_id: str, req: ConversationUpdate) -> dict:
    conn = connect()
    try:
        result = conversations.update_title(conn, conversation_id, req.title)
        if not result:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return result
    finally:
        conn.close()


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    conn = connect()
    try:
        if not conversations.delete(conn, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"deleted": conversation_id}
    finally:
        conn.close()
