"""Context and source CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import connect
from ..models import ContextCreate, ContextSourceCreate, ContextSourceUpdate, ContextUpdate
from ..security import require_token
from ..services import contexts

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/contexts")
def list_contexts() -> dict:
    conn = connect()
    try:
        return {"contexts": contexts.list_all(conn)}
    finally:
        conn.close()


@router.post("/api/contexts")
def create_context(req: ContextCreate) -> dict:
    conn = connect()
    try:
        return contexts.create(conn, req)
    finally:
        conn.close()


@router.get("/api/contexts/{context_id}")
def get_context(context_id: str) -> dict:
    conn = connect()
    try:
        result = contexts.get(conn, context_id)
        if not result:
            raise HTTPException(status_code=404, detail="Context not found")
        result["sources"] = contexts.list_sources(conn, context_id)
        result["packs"] = [
            {
                "pack_id": row["pack_id"],
                "version": int(row["version"]),
                "ready": bool(row["ready"]),
                "is_current": bool(row["is_current"]),
                "size_bytes": int(row["size_bytes"]),
                "created_at": row["created_at"],
            }
            for row in conn.execute(
                "SELECT * FROM packs WHERE context_id=? ORDER BY version DESC",
                (context_id,),
            )
        ]
        return result
    finally:
        conn.close()


@router.patch("/api/contexts/{context_id}")
def update_context(context_id: str, req: ContextUpdate) -> dict:
    conn = connect()
    try:
        result = contexts.update(conn, context_id, req)
        if not result:
            raise HTTPException(status_code=404, detail="Context not found")
        return result
    finally:
        conn.close()


@router.delete("/api/contexts/{context_id}")
def delete_context(context_id: str) -> dict:
    conn = connect()
    try:
        if not contexts.delete(conn, context_id):
            raise HTTPException(status_code=404, detail="Context not found")
        return {"deleted": context_id}
    finally:
        conn.close()


@router.get("/api/contexts/{context_id}/sources")
def list_sources(context_id: str) -> dict:
    conn = connect()
    try:
        if not contexts.get(conn, context_id):
            raise HTTPException(status_code=404, detail="Context not found")
        return {"sources": contexts.list_sources(conn, context_id)}
    finally:
        conn.close()


@router.post("/api/contexts/{context_id}/sources")
def add_source(context_id: str, req: ContextSourceCreate) -> dict:
    conn = connect()
    try:
        result = contexts.add_source(conn, context_id, req)
        if not result:
            raise HTTPException(status_code=404, detail="Context not found")
        return result
    finally:
        conn.close()


@router.patch("/api/sources/{source_id}")
def update_source(source_id: str, req: ContextSourceUpdate) -> dict:
    conn = connect()
    try:
        result = contexts.update_source(conn, source_id, req)
        if not result:
            raise HTTPException(status_code=404, detail="Source not found")
        return result
    finally:
        conn.close()


@router.delete("/api/sources/{source_id}")
def delete_source(source_id: str) -> dict:
    conn = connect()
    try:
        if not contexts.delete_source(conn, source_id):
            raise HTTPException(status_code=404, detail="Source not found")
        return {"deleted": source_id}
    finally:
        conn.close()
