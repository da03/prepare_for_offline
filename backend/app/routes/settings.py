"""Product preference API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import connect
from ..models import SettingsUpdate
from ..security import require_token
from ..services import preferences, search_credentials

router = APIRouter(dependencies=[Depends(require_token)])


class SearchKeyRequest(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)


@router.get("/api/settings")
def get_settings() -> dict:
    conn = connect()
    try:
        return preferences.get_all(conn)
    finally:
        conn.close()


@router.patch("/api/settings")
def update_settings(req: SettingsUpdate) -> dict:
    conn = connect()
    try:
        if req.active_context_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM contexts WHERE context_id=?",
                (req.active_context_id,),
            ).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Context not found")
        return preferences.update(conn, req)
    finally:
        conn.close()


@router.get("/api/settings/search")
def search_status() -> dict:
    return search_credentials.status()


@router.put("/api/settings/search")
def save_search_key(req: SearchKeyRequest) -> dict:
    search_credentials.set_key(req.api_key)
    return search_credentials.status()


@router.delete("/api/settings/search")
def delete_search_key() -> dict:
    if search_credentials.status()["managed_by_environment"]:
        raise HTTPException(
            status_code=409, detail="Search key is managed by the environment"
        )
    search_credentials.delete_key()
    return search_credentials.status()
