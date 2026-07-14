"""Pack listing, storage info, and runtime memory metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import get_settings
from ..db import connect
from ..security import require_token
from ..services import packs as packs_svc
from ..services.expert_loader import get_loader

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/packs")
def list_packs() -> dict:
    conn = connect()
    try:
        return {"packs": packs_svc.list_packs(conn)}
    finally:
        conn.close()


@router.delete("/api/packs/{pack_id}")
def delete_pack(pack_id: str) -> dict:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM packs WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM documents WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM answer_cards WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM experts WHERE pack_id=?", (pack_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pack not found")
        return {"deleted": pack_id}
    finally:
        conn.close()


@router.get("/api/storage")
def storage() -> dict:
    conn = connect()
    try:
        rows = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS b FROM packs").fetchone()
        return {
            "home": str(get_settings().home),
            "total_bytes": int(rows["b"]),
            "pack_count": int(rows["n"]),
        }
    finally:
        conn.close()


@router.get("/api/metrics")
def metrics() -> dict:
    return {"expert_loader": get_loader().metrics_summary()}
