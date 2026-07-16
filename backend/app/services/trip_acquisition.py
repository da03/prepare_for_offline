"""Integrate safe public-source acquisition with persisted travel contexts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from . import contexts
from .source_acquisition import SourceAcquisitionOrchestrator
from .source_queries import PublicTripFields
from .source_ranking import RankingContext


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum(value):
    return value.value if hasattr(value, "value") else value


def discover_trip(
    conn: sqlite3.Connection,
    trip_id: str,
    *,
    orchestrator: SourceAcquisitionOrchestrator | None = None,
) -> dict:
    trip = contexts.get(conn, trip_id)
    if not trip:
        raise ValueError("Trip not found")
    brief = trip.get("trip_brief") or {}
    fields = PublicTripFields(
        event=str(brief.get("event") or ""),
        destination=str(brief.get("destination") or ""),
        start_date=brief.get("starts_at") or trip.get("starts_at"),
        end_date=brief.get("ends_at") or trip.get("ends_at"),
        # Only public semantic needs; personal attachment text never enters.
        public_needs=tuple(brief.get("public_needs") or ()),
    )
    known = brief.get("official_domains") or {}
    ranking = RankingContext(
        event_official_domains=frozenset(known.get("event", [])),
        venue_domains=frozenset(known.get("venue", [])),
        airport_domains=frozenset(known.get("airport", [])),
        transit_authority_domains=frozenset(known.get("transit", [])),
        government_domains=frozenset(known.get("government", [])),
        embassy_domains=frozenset(known.get("embassy", [])),
        tourism_domains=frozenset(known.get("tourism", [])),
    )
    service = orchestrator or SourceAcquisitionOrchestrator()
    result = service.acquire(fields, ranking_context=ranking)
    mode_row = conn.execute(
        "SELECT value FROM settings WHERE key='search_mode'"
    ).fetchone()
    try:
        search_mode = json.loads(mode_row["value"]) if mode_row else "automatic"
    except json.JSONDecodeError:
        search_mode = "automatic"
    run_id = f"search-{uuid.uuid4().hex[:14]}"
    completed = _now()
    stats = asdict(result.stats)
    gaps = [
        {
            "code": gap.code.value,
            "message": gap.message,
            "query": gap.query,
            "url": gap.url,
        }
        for gap in result.gaps
    ]
    conn.execute(
        """
        INSERT INTO search_runs (
            search_run_id, context_id, provider, queries, status, stats, gaps,
            created_at, completed_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            trip_id,
            result.stats.provider,
            json.dumps(result.queries, ensure_ascii=False),
            "complete" if result.candidates else "gaps",
            json.dumps(stats, default=_enum, ensure_ascii=False),
            json.dumps(gaps, ensure_ascii=False),
            completed,
            completed,
        ),
    )

    saved = []
    for candidate in result.candidates:
        if search_mode == "official_only" and candidate.quality_tier.value > 2:
            continue
        if not candidate.cache_allowed or not candidate.text.strip():
            continue
        existing = conn.execute(
            "SELECT source_id FROM context_sources WHERE context_id=? AND url=?",
            (trip_id, candidate.url),
        ).fetchone()
        source_id = existing["source_id"] if existing else candidate.source_id
        metadata = {
            "source_role": candidate.source_role.value,
            "consequence_flags": [flag.value for flag in candidate.consequence_flags],
            "rank_score": candidate.rank_score,
            "freshness_state": candidate.freshness.state.value,
            "cache_restriction": candidate.cache_restriction,
            "evidence_origin": candidate.evidence_origin,
            "private": False,
            "topic": candidate.source_role.value,
        }
        values = (
            candidate.title,
            candidate.url,
            candidate.text,
            json.dumps(metadata, ensure_ascii=False),
            candidate.publisher,
            candidate.quality_tier.name.lower(),
            candidate.freshness_class.value,
            candidate.retrieved_at.isoformat(),
            (
                candidate.source_updated_at.isoformat()
                if candidate.source_updated_at
                else None
            ),
            candidate.freshness.expires_at.isoformat(),
            candidate.license,
            completed,
        )
        if existing:
            conn.execute(
                """
                UPDATE context_sources SET title=?, url=?, content=?, metadata=?,
                    publisher=?, quality_tier=?, freshness_class=?, retrieved_at=?,
                    source_updated_at=?, expires_at=?, license=?, enabled=1,
                    updated_at=? WHERE source_id=?
                """,
                (*values, source_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO context_sources (
                    source_id, context_id, title, source_type, url, content,
                    metadata, enabled, created_at, updated_at, publisher,
                    quality_tier, freshness_class, retrieved_at,
                    source_updated_at, expires_at, license
                ) VALUES (?,?,?,'web',?,?,?,1,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_id,
                    trip_id,
                    candidate.title,
                    candidate.url,
                    candidate.text,
                    json.dumps(metadata, ensure_ascii=False),
                    completed,
                    completed,
                    candidate.publisher,
                    candidate.quality_tier.name.lower(),
                    candidate.freshness_class.value,
                    candidate.retrieved_at.isoformat(),
                    (
                        candidate.source_updated_at.isoformat()
                        if candidate.source_updated_at
                        else None
                    ),
                    candidate.freshness.expires_at.isoformat(),
                    candidate.license,
                ),
            )
        saved.append(
            {
                "source_id": source_id,
                "title": candidate.title,
                "publisher": candidate.publisher,
                "url": candidate.url,
                "quality_tier": candidate.quality_tier.name.lower(),
                "freshness_class": candidate.freshness_class.value,
                "freshness": candidate.freshness.state.value,
                "expires_at": candidate.freshness.expires_at.isoformat(),
            }
        )

    brief["suggested_queries"] = list(result.queries)
    conn.execute(
        "UPDATE contexts SET trip_brief=?, search_refreshed_at=?, status='draft', "
        "updated_at=? WHERE context_id=?",
        (json.dumps(brief, ensure_ascii=False), completed, completed, trip_id),
    )
    conn.commit()
    return {
        "search_run_id": run_id,
        "provider": result.stats.provider,
        "queries": list(result.queries),
        "sources": saved,
        "publishers": list(dict.fromkeys(item["publisher"] for item in saved if item["publisher"])),
        "gaps": gaps,
        "stats": stats,
        "refreshed_at": completed,
    }
