"""Offline chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import connect
from ..models import ChatRequest, ChatResponse
from ..security import require_token
from ..services import answerer
from ..services.packs import KOREA_PACK_ID

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    pack_id = req.pack_id or KOREA_PACK_ID
    conn = connect()
    try:
        result = answerer.answer_question(conn, pack_id, req.question)
    finally:
        conn.close()
    return ChatResponse(**result)
