"""Preparation endpoints: start a job, poll status, stream progress via SSE."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..db import connect
from ..security import require_token
from ..services import contexts, jobs, planner, templates

router = APIRouter(dependencies=[Depends(require_token)])


class PrepareRequest(BaseModel):
    context_id: str
    selected_source_ids: list[str] | None = None
    selected_capabilities: list[str] | None = None
    selected_topics: list[str] | None = None
    expected_questions: list[str] | None = None
    compile_expert: bool = True
    finalize: bool | None = None
    allow_online_synth: bool | None = None
    optimize: bool = True
    discover: bool = True


class ContextPrepareRequest(BaseModel):
    selected_source_ids: list[str] | None = None
    selected_capabilities: list[str] | None = None
    selected_topics: list[str] | None = None
    expected_questions: list[str] | None = None
    compile_expert: bool = True
    finalize: bool | None = None
    allow_online_synth: bool | None = None
    optimize: bool = True
    discover: bool = True


def _preview(context_id: str, req: ContextPrepareRequest) -> dict:
    conn = connect()
    try:
        context = contexts.get(conn, context_id)
        if not context:
            raise HTTPException(status_code=404, detail="Context not found")
        sources = contexts.list_sources(conn, context_id)
        return planner.plan_context(
            context,
            sources,
            selected_source_ids=req.selected_source_ids,
            selected_capabilities=req.selected_capabilities,
            selected_topics=req.selected_topics,
            expected_questions=req.expected_questions,
            finalize=req.finalize,
            allow_online_synth=req.allow_online_synth,
        ).to_dict()
    finally:
        conn.close()


@router.get("/api/templates")
def list_templates() -> dict:
    return {"templates": templates.list_templates()}


@router.post("/api/contexts/{context_id}/plan")
def preview_context_plan(context_id: str, req: ContextPrepareRequest) -> dict:
    return _preview(context_id, req)


@router.post("/api/contexts/{context_id}/prepare")
def prepare_context(context_id: str, req: ContextPrepareRequest) -> dict:
    # Validate and freeze the editable preview before starting.
    plan = _preview(context_id, req)
    job_id = jobs.start_job(
        context_id,
        {
            "selected_source_ids": plan["selected_source_ids"],
            "selected_capabilities": plan["selected_capabilities"],
            "selected_topics": plan["selected_topics"],
            "expected_questions": plan["expected_questions"],
            "compile_expert": req.compile_expert,
            "finalize": req.finalize,
            "allow_online_synth": req.allow_online_synth,
            "optimize": req.optimize,
            "discover": req.discover,
        },
    )
    return {"job_id": job_id, "context_id": context_id}


@router.post("/api/plan")
def preview_plan(req: PrepareRequest) -> dict:
    """Compatibility alias for clients not yet using the context-scoped route."""
    return _preview(
        req.context_id,
        ContextPrepareRequest(
            selected_source_ids=req.selected_source_ids,
            selected_capabilities=req.selected_capabilities,
            selected_topics=req.selected_topics,
            expected_questions=req.expected_questions,
            compile_expert=req.compile_expert,
            finalize=req.finalize,
            allow_online_synth=req.allow_online_synth,
            optimize=req.optimize,
            discover=req.discover,
        ),
    )


@router.post("/api/prepare")
def prepare(req: PrepareRequest) -> dict:
    return prepare_context(
        req.context_id,
        ContextPrepareRequest(
            selected_source_ids=req.selected_source_ids,
            selected_capabilities=req.selected_capabilities,
            selected_topics=req.selected_topics,
            expected_questions=req.expected_questions,
            compile_expert=req.compile_expert,
            finalize=req.finalize,
            allow_online_synth=req.allow_online_synth,
            optimize=req.optimize,
            discover=req.discover,
        ),
    )


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if not jobs.cancel_job(job_id):
        raise HTTPException(
            status_code=409, detail="Job cannot be cancelled or does not exist"
        )
    return {"job_id": job_id, "cancel_requested": True}


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
