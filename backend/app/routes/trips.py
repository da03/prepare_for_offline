"""Travel-first aliases and one-sentence preparation."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from ..db import connect
from ..models import (
    ContextCreate,
    ContextSourceCreate,
    ContextUpdate,
    SettingsUpdate,
    TripParseRequest,
    TripPrepareRequest,
    TripUpdate,
)
from ..security import require_token
from ..services import (
    attachment_text,
    contexts,
    jobs,
    planner,
    preferences,
    trip_acquisition,
    trip_parser,
)
from .prepare import ContextPrepareRequest, prepare_context

router = APIRouter(dependencies=[Depends(require_token)])


def _starter_questions(brief: dict) -> list[str]:
    destination = brief.get("destination") or "my destination"
    event = brief.get("event")
    questions = [
        f"How do I get from the airport to {destination}?",
        "What emergency numbers should I save?",
        "What payment and tipping customs should I know?",
    ]
    if event:
        questions.insert(0, f"When and where is {event}?")
    if len(brief.get("languages", [])) > 1:
        questions.append("What useful local phrases should I know?")
    return questions[:5]


def _detail(conn, context_id: str) -> dict:
    trip = contexts.get(conn, context_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip["sources"] = contexts.list_sources(conn, context_id)
    trip["packs"] = [
        dict(row)
        for row in conn.execute(
            "SELECT pack_id, version, ready, is_current, size_bytes, created_at "
            "FROM packs WHERE context_id=? ORDER BY version DESC",
            (context_id,),
        )
    ]
    brief = trip.get("trip_brief") or {}
    trip.update(
        {
            "trip_id": trip["context_id"],
            "event": brief.get("event") or trip["name"],
            "destination": brief.get("destination") or "",
            "dates": {
                "start": brief.get("starts_at") or trip.get("starts_at"),
                "end": brief.get("ends_at") or trip.get("ends_at"),
            },
            "needs": brief.get("traveler_needs") or trip.get("expected_needs", []),
            "ready_offline": bool(
                trip.get("active_pack_id") and trip.get("status") == "ready"
            ),
            "coverage": {
                "semantic_coverage": brief.get("coverage", []),
                "sources": trip["sources"],
                "source_publishers": list(
                    dict.fromkeys(
                        source.get("publisher") or source.get("title")
                        for source in trip["sources"]
                        if source.get("publisher") or source.get("title")
                    )
                ),
                "freshness": trip.get("search_refreshed_at"),
            },
        }
    )
    return trip


@router.get("/api/trips")
def list_trips() -> dict:
    conn = connect()
    try:
        return {
            "trips": [
                _detail(conn, context["context_id"])
                for context in contexts.list_all(conn)
                if context["context_type"] in {"trip", "conference"}
            ]
        }
    finally:
        conn.close()


@router.post("/api/trips/parse")
def parse_trip(req: TripParseRequest) -> dict:
    parsed = trip_parser.parse(req.text)
    conn = connect()
    try:
        trip = contexts.create(
            conn,
            ContextCreate(
                name=parsed.name,
                context_type="conference" if parsed.event else "trip",
                goal=req.text,
                starts_at=parsed.starts_at,
                ends_at=parsed.ends_at,
                languages=parsed.languages,
                interests=parsed.coverage,
                expected_needs=parsed.traveler_needs,
                privacy_mode="local_only",
                preparation_quality="fast",
            ),
        )
        starters = _starter_questions(parsed.to_dict())
        app_settings = preferences.get_all(conn)
        search_enabled = app_settings.get("search_mode", "automatic") != "off"
        conn.execute(
            "UPDATE contexts SET trip_brief=?, suggested_questions=?, "
            "search_enabled=?, updated_at=? WHERE context_id=?",
            (
                json.dumps(parsed.to_dict(), ensure_ascii=False),
                json.dumps(starters, ensure_ascii=False),
                1 if search_enabled else 0,
                trip["updated_at"],
                trip["context_id"],
            ),
        )
        contexts.add_source(
            conn,
            trip["context_id"],
            ContextSourceCreate(
                title="Trip brief",
                source_type="structured",
                content=(
                    f"Event: {parsed.event or 'not specified'}\n"
                    f"Destination: {parsed.destination or 'not specified'}\n"
                    f"Travel request: {req.text}"
                ),
                metadata={
                    "private": True,
                    "topic": "itinerary",
                    "stable": True,
                },
            ),
        )
        for attachment in req.attachments:
            try:
                extracted, extraction_meta = attachment_text.extract(
                    name=attachment.name,
                    content=attachment.content,
                    media_type=attachment.media_type,
                    encoding=attachment.encoding,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not read {attachment.name}: {exc}",
                ) from exc
            contexts.add_source(
                conn,
                trip["context_id"],
                ContextSourceCreate(
                    title=attachment.name,
                    source_type="file",
                    content=extracted,
                    metadata={
                        "media_type": attachment.media_type,
                        "size_bytes": attachment.size_bytes,
                        **extraction_meta,
                        "private": True,
                        "topic": "itinerary",
                    },
                ),
            )
        preferences.update(
            conn,
            SettingsUpdate(active_context_id=trip["context_id"]),
        )
        detail = _detail(conn, trip["context_id"])
        return {
            "trip": detail,
            "blocking_question": parsed.blocking_question,
            "suggested_queries": parsed.suggested_queries,
            "coverage": {
                "semantic_coverage": parsed.coverage,
                "sources": detail["sources"],
                "source_publishers": [],
                "privacy": "Personal attachments remain on this Mac.",
            },
        }
    finally:
        conn.close()


@router.get("/api/trips/{trip_id}")
def get_trip(trip_id: str) -> dict:
    conn = connect()
    try:
        return _detail(conn, trip_id)
    finally:
        conn.close()


@router.patch("/api/trips/{trip_id}")
def update_trip(trip_id: str, req: TripUpdate) -> dict:
    conn = connect()
    try:
        trip = contexts.get(conn, trip_id)
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found")
        brief = dict(trip.get("trip_brief") or {})
        changes = req.model_dump(exclude_unset=True)
        context_changes = {}
        for key in ("name", "starts_at", "ends_at", "languages"):
            if key in changes:
                context_changes[key] = changes[key]
        if "traveler_needs" in changes:
            context_changes["expected_needs"] = changes["traveler_needs"]
        if "needs" in changes:
            context_changes["expected_needs"] = changes["needs"]
            changes["traveler_needs"] = changes.pop("needs")
        if "dates" in changes:
            dates = changes.pop("dates") or {}
            context_changes["starts_at"] = dates.get("start")
            context_changes["ends_at"] = dates.get("end")
            changes["starts_at"] = dates.get("start")
            changes["ends_at"] = dates.get("end")
        if context_changes:
            contexts.update(conn, trip_id, ContextUpdate(**context_changes))
        brief.update(changes)
        if "search_enabled" in changes:
            conn.execute(
                "UPDATE contexts SET search_enabled=? WHERE context_id=?",
                (1 if changes["search_enabled"] else 0, trip_id),
            )
        starters = _starter_questions(brief)
        conn.execute(
            "UPDATE contexts SET trip_brief=?, suggested_questions=? WHERE context_id=?",
            (
                json.dumps(brief, ensure_ascii=False),
                json.dumps(starters, ensure_ascii=False),
                trip_id,
            ),
        )
        conn.commit()
        return _detail(conn, trip_id)
    finally:
        conn.close()


@router.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str) -> dict:
    conn = connect()
    try:
        if not contexts.delete(conn, trip_id):
            raise HTTPException(status_code=404, detail="Trip not found")
        return {"deleted": trip_id}
    finally:
        conn.close()


@router.get("/api/trips/{trip_id}/starters")
def starters(trip_id: str) -> dict:
    conn = connect()
    try:
        trip = contexts.get(conn, trip_id)
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found")
        return {"questions": trip.get("suggested_questions", [])}
    finally:
        conn.close()


@router.post("/api/trips/{trip_id}/discover")
def discover_trip_sources(trip_id: str) -> dict:
    conn = connect()
    try:
        trip = contexts.get(conn, trip_id)
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found")
        search_mode = preferences.get_all(conn).get("search_mode", "automatic")
        if not trip.get("search_enabled", True) or search_mode == "off":
            return {
                "trip": _detail(conn, trip_id),
                "sources": [],
                "publishers": [],
                "gaps": [{"code": "search_disabled", "message": "Public search is off."}],
            }
        discovery = trip_acquisition.discover_trip(conn, trip_id)
        detail = _detail(conn, trip_id)
        plan = planner.plan_context(
            detail, contexts.list_sources(conn, trip_id)
        ).to_dict()
        coverage = dict(detail.get("coverage") or {})
        coverage.update(
            {
                "semantic_coverage": plan["coverage"],
                "source_publishers": discovery["publishers"],
                "sources": detail["sources"],
                "freshness": discovery["refreshed_at"],
                "estimated_size_bytes": plan["storage_estimate_bytes"],
                "preparation_time_estimate_s": plan[
                    "preparation_time_estimate_s"
                ],
                "privacy": " ".join(plan["privacy_disclosures"]),
            }
        )
        return {"trip": detail, "coverage": coverage, **discovery}
    finally:
        conn.close()


@router.get("/api/trips/{trip_id}/search/latest")
def latest_search_run(trip_id: str) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM search_runs WHERE context_id=? ORDER BY created_at DESC LIMIT 1",
            (trip_id,),
        ).fetchone()
        if not row:
            return {"search_run": None}
        return {
            "search_run": {
                "search_run_id": row["search_run_id"],
                "provider": row["provider"],
                "queries": json.loads(row["queries"]),
                "status": row["status"],
                "stats": json.loads(row["stats"]),
                "gaps": json.loads(row["gaps"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
        }
    finally:
        conn.close()


@router.post("/api/trips/{trip_id}/prepare")
def prepare_trip(trip_id: str, req: TripPrepareRequest) -> dict:
    result = prepare_context(
        trip_id,
        ContextPrepareRequest(
            selected_source_ids=req.source_ids,
            optimize=req.optimize,
            discover=req.discover,
        ),
    )
    return {"job_id": result["job_id"], "trip_id": trip_id}


@router.post("/api/trips/{trip_id}/optimization/rollback")
def rollback_trip_optimization(trip_id: str) -> dict:
    if not jobs.rollback_optimization(trip_id):
        raise HTTPException(status_code=409, detail="No fast program version to restore")
    return {"trip_id": trip_id, "optimization_status": "rolled_back"}


@router.post("/api/trips/{trip_id}/optimization")
def resume_trip_optimization(trip_id: str) -> dict:
    if not jobs.resume_optimization(trip_id):
        raise HTTPException(status_code=409, detail="No fast program version to optimize")
    return {"trip_id": trip_id, "optimization_status": "queued"}
