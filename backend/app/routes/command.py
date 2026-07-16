"""Unified natural-language command endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import connect
from ..models import CommandRequest, CommandResponse
from ..security import require_token
from ..services import commands

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/api/command", response_model=CommandResponse)
def command(req: CommandRequest) -> CommandResponse:
    conn = connect()
    try:
        return CommandResponse(**commands.execute(conn, req))
    finally:
        conn.close()
