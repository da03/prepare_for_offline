"""Unauthenticated health + dev token bootstrap."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import __version__
from ..config import get_settings

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/api/dev/token")
def dev_token() -> dict:
    settings = get_settings()
    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Not found")
    return {"token": settings.app_token}
