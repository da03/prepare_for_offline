"""Preparation endpoints: start a job, poll status, stream progress via SSE."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..security import require_token
from ..services import jobs, planner
from ..services.packs import KOREA_PACK_ID

router = APIRouter(dependencies=[Depends(require_token)])


class PrepareRequest(BaseModel):
    destination: str = "South Korea"
    interests: list[str] | None = None
    storage_budget_mb: int = 1200
    compile_expert: bool = True
    finalize: bool = False
    allow_online_synth: bool = False


@router.post("/api/plan")
def preview_plan(req: PrepareRequest) -> dict:
    """Preview the PackPlan (capabilities, topics, size/time estimates) without
    building anything - so the budget's effect is visible before committing."""
    p = planner.plan(
        destination=req.destination,
        interests=req.interests,
        storage_budget_mb=req.storage_budget_mb,
        finalize=req.finalize,
        allow_online_synth=req.allow_online_synth,
    )
    return p.to_dict()


@router.post("/api/prepare")
def prepare(req: PrepareRequest) -> dict:
    plan = {
        "destination": req.destination,
        "interests": req.interests,
        "storage_budget_mb": req.storage_budget_mb,
        "compile_expert": req.compile_expert,
        "finalize": req.finalize,
        "allow_online_synth": req.allow_online_synth,
    }
    job_id = jobs.start_job(KOREA_PACK_ID, plan)
    return {"job_id": job_id, "pack_id": KOREA_PACK_ID}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    async def stream():
        last = -1
        terminal = {"ready", "failed", "cancelled"}
        while True:
            job = jobs.get_job(job_id)
            if job is None:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                return
            progress = job["progress"]
            while last + 1 < len(progress):
                last += 1
                yield f"data: {json.dumps(progress[last])}\n\n"
            if job["state"] in terminal:
                yield f"data: {json.dumps({'state': job['state'], 'done': True})}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")
