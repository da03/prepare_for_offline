"""Verify-later queue.

Offline, uncertain questions are queued. When back online, /verify records a
fresh answer and reports whether it CHANGED from the offline answer, so the
user sees what was corrected rather than a silent overwrite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connect
from ..security import require_token

router = APIRouter(dependencies=[Depends(require_token)])


class VerifyRequest(BaseModel):
    verified_answer: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/queue")
def list_queue() -> dict:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM question_queue ORDER BY created_at DESC"
        ).fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "question": r["question"],
                "offline_answer": r["offline_answer"],
                "offline_support": r["offline_support"],
                "offline_sources": json.loads(r["offline_sources"]),
                "status": r["status"],
                "verified_answer": r["verified_answer"],
                "changed": None if r["changed"] is None else bool(r["changed"]),
                "created_at": r["created_at"],
                "verified_at": r["verified_at"],
            })
        return {"items": items}
    finally:
        conn.close()


@router.post("/api/queue/{item_id}/verify")
def verify(item_id: int, req: VerifyRequest) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM question_queue WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Queue item not found")
        offline = (row["offline_answer"] or "").strip()
        changed = offline != req.verified_answer.strip()
        conn.execute(
            "UPDATE question_queue SET verified_answer=?, changed=?, status='verified', "
            "verified_at=? WHERE id=?",
            (req.verified_answer, 1 if changed else 0, _now(), item_id),
        )
        conn.commit()
        return {
            "id": item_id,
            "changed": changed,
            "offline_answer": row["offline_answer"],
            "verified_answer": req.verified_answer,
        }
    finally:
        conn.close()
