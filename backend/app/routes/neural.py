"""PAW Offline program preparation and registry APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import connect
from ..security import require_token
from ..services import neural_jobs, program_registry

router = APIRouter(dependencies=[Depends(require_token)])

STARTERS = [
    "What does simida mean?",
    "How did geography shape Japan's political history?",
    "Did the Treaty of Versailles directly start World War II?",
    "Why are sunsets red?",
]


class PrepareProgramRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=1200)


@router.get("/api/neural/status")
def status() -> dict:
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        builtins = conn.execute(
            "SELECT COUNT(*) AS n FROM neural_programs "
            "WHERE built_in=1 AND status='ready'"
        ).fetchone()["n"]
        return {
            "ready": bool(builtins),
            "built_in_program_count": int(builtins),
            "prepared_programs": program_registry.prepared(conn),
        }
    finally:
        conn.close()


@router.get("/api/programs")
def programs() -> dict:
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        return {"programs": program_registry.prepared(conn)}
    finally:
        conn.close()


@router.post("/api/programs/prepare")
def prepare_program(req: PrepareProgramRequest) -> dict:
    job = neural_jobs.start(req.prompt)
    if not job:
        raise HTTPException(status_code=500, detail="Could not start preparation")
    return job


@router.delete("/api/programs/{program_key:path}")
def remove_program(program_key: str) -> dict:
    conn = connect()
    try:
        if not program_registry.remove(conn, program_key):
            raise HTTPException(status_code=404, detail="Prepared topic not found")
        return {"deleted": True}
    finally:
        conn.close()


@router.post("/api/programs/{program_key:path}/rollback")
def rollback_program(program_key: str) -> dict:
    conn = connect()
    try:
        result = program_registry.rollback(conn, program_key)
        if not result:
            raise HTTPException(
                status_code=409, detail="No previous program version"
            )
        return result
    finally:
        conn.close()


@router.get("/api/neural/jobs/{job_id}")
def job(job_id: str) -> dict:
    value = neural_jobs.get(job_id)
    if not value:
        raise HTTPException(status_code=404, detail="Preparation job not found")
    return value


@router.post("/api/neural/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if not neural_jobs.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")
    return {"cancelled": True}


@router.get("/api/starters")
def starters() -> dict:
    return {
        "starters": [
            {"id": f"starter-{index}", "text": question}
            for index, question in enumerate(STARTERS, start=1)
        ]
    }
