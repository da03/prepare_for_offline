"""Editable contexts and their local sources.

A context is user intent/configuration. A pack is a prepared, immutable
artifact/version derived from that context.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models import ContextCreate, ContextSourceCreate, ContextSourceUpdate, ContextUpdate
from . import templates


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _context_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "context_id": row["context_id"],
        "name": row["name"],
        "context_type": row["context_type"],
        "goal": row["goal"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "languages": _loads(row["languages"], []),
        "interests": _loads(row["interests"], []),
        "expected_needs": _loads(row["expected_needs"], []),
        "storage_budget_mb": int(row["storage_budget_mb"]),
        "privacy_mode": row["privacy_mode"],
        "preparation_quality": row["preparation_quality"],
        "active_pack_id": row["active_pack_id"],
        "template_id": row["template_id"],
        "status": row["status"],
        "trip_brief": _loads(row["trip_brief"], {}) if "trip_brief" in row.keys() else {},
        "suggested_questions": (
            _loads(row["suggested_questions"], [])
            if "suggested_questions" in row.keys()
            else []
        ),
        "prepared_at": row["prepared_at"] if "prepared_at" in row.keys() else None,
        "optimization_status": (
            row["optimization_status"]
            if "optimization_status" in row.keys()
            else "idle"
        ),
        "search_enabled": (
            bool(row["search_enabled"]) if "search_enabled" in row.keys() else True
        ),
        "search_refreshed_at": (
            row["search_refreshed_at"]
            if "search_refreshed_at" in row.keys()
            else None
        ),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create(conn: sqlite3.Connection, data: ContextCreate) -> dict:
    context_id = f"ctx-{uuid.uuid4().hex[:12]}"
    now = _now()
    template = templates.get(data.template_id)
    context_type = (
        template["context_type"]
        if template and data.context_type == "custom"
        else data.context_type
    )
    languages = data.languages or (template.get("languages", []) if template else [])
    interests = data.interests or (template.get("interests", []) if template else [])
    conn.execute(
        """
        INSERT INTO contexts (
            context_id, name, context_type, goal, starts_at, ends_at, languages,
            interests, expected_needs, storage_budget_mb, privacy_mode,
            preparation_quality, template_id, status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?)
        """,
        (
            context_id,
            data.name,
            context_type,
            data.goal,
            data.starts_at,
            data.ends_at,
            json.dumps(languages, ensure_ascii=False),
            json.dumps(interests, ensure_ascii=False),
            json.dumps(data.expected_needs, ensure_ascii=False),
            data.storage_budget_mb,
            data.privacy_mode,
            data.preparation_quality,
            data.template_id,
            now,
            now,
        ),
    )
    conn.commit()
    return get(conn, context_id)


def get(conn: sqlite3.Connection, context_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM contexts WHERE context_id=?", (context_id,)
    ).fetchone()
    return _context_dict(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[dict]:
    return [
        _context_dict(row)
        for row in conn.execute("SELECT * FROM contexts ORDER BY updated_at DESC")
    ]


def update(conn: sqlite3.Connection, context_id: str, data: ContextUpdate) -> dict | None:
    existing = get(conn, context_id)
    if not existing:
        return None
    changes = data.model_dump(exclude_unset=True)
    json_fields = {"languages", "interests", "expected_needs"}
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in changes.items():
        assignments.append(f"{key}=?")
        values.append(json.dumps(value, ensure_ascii=False) if key in json_fields else value)
    if assignments:
        assignments.append("status=?")
        values.append("draft")
        assignments.append("updated_at=?")
        values.append(_now())
        values.append(context_id)
        conn.execute(
            f"UPDATE contexts SET {', '.join(assignments)} WHERE context_id=?",
            values,
        )
        conn.commit()
    return get(conn, context_id)


def delete(conn: sqlite3.Connection, context_id: str) -> bool:
    pack_ids = [
        row["pack_id"]
        for row in conn.execute("SELECT pack_id FROM packs WHERE context_id=?", (context_id,))
    ]
    for pack_id in pack_ids:
        conn.execute("DELETE FROM doc_grams WHERE doc_id IN "
                     "(SELECT doc_id FROM documents WHERE pack_id=?)", (pack_id,))
        conn.execute("DELETE FROM documents_fts WHERE rowid IN "
                     "(SELECT doc_id FROM documents WHERE pack_id=?)", (pack_id,))
        conn.execute("DELETE FROM documents WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM card_grams WHERE card_id IN "
                     "(SELECT card_id FROM answer_cards WHERE pack_id=?)", (pack_id,))
        conn.execute("DELETE FROM answer_cards WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM experts WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM question_queue WHERE pack_id=?", (pack_id,))
        conn.execute("DELETE FROM packs WHERE pack_id=?", (pack_id,))
    deleted = conn.execute(
        "DELETE FROM contexts WHERE context_id=?", (context_id,)
    ).rowcount > 0
    active = conn.execute(
        "SELECT value FROM settings WHERE key='active_context_id'"
    ).fetchone()
    if active and _loads(active["value"], None) == context_id:
        conn.execute(
            "UPDATE settings SET value='null', updated_at=? "
            "WHERE key='active_context_id'",
            (_now(),),
        )
    conn.commit()
    return deleted


def set_active_pack(
    conn: sqlite3.Connection, context_id: str, pack_id: str, status: str = "ready"
) -> None:
    conn.execute(
        "UPDATE contexts SET active_pack_id=?, status=?, updated_at=? WHERE context_id=?",
        (pack_id, status, _now(), context_id),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, context_id: str, status: str) -> None:
    conn.execute(
        "UPDATE contexts SET status=?, updated_at=? WHERE context_id=?",
        (status, _now(), context_id),
    )
    conn.commit()


def _source_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "context_id": row["context_id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "url": row["url"],
        "local_path": row["local_path"],
        "content": row["content"],
        "metadata": _loads(row["metadata"], {}),
        "publisher": row["publisher"] if "publisher" in row.keys() else None,
        "quality_tier": row["quality_tier"] if "quality_tier" in row.keys() else None,
        "freshness_class": (
            row["freshness_class"] if "freshness_class" in row.keys() else None
        ),
        "retrieved_at": row["retrieved_at"] if "retrieved_at" in row.keys() else None,
        "source_updated_at": (
            row["source_updated_at"] if "source_updated_at" in row.keys() else None
        ),
        "expires_at": row["expires_at"] if "expires_at" in row.keys() else None,
        "license": row["license"] if "license" in row.keys() else None,
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def add_source(
    conn: sqlite3.Connection, context_id: str, data: ContextSourceCreate
) -> dict | None:
    if not get(conn, context_id):
        return None
    source_id = f"src-{uuid.uuid4().hex[:12]}"
    now = _now()
    conn.execute(
        """
        INSERT INTO context_sources (
            source_id, context_id, title, source_type, url, local_path, content,
            metadata, enabled, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
        """,
        (
            source_id,
            context_id,
            data.title,
            data.source_type,
            data.url,
            data.local_path,
            data.content,
            json.dumps(data.metadata, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE contexts SET status='draft', updated_at=? WHERE context_id=?",
        (now, context_id),
    )
    conn.commit()
    return get_source(conn, source_id)


def get_source(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM context_sources WHERE source_id=?", (source_id,)
    ).fetchone()
    return _source_dict(row) if row else None


def list_sources(conn: sqlite3.Connection, context_id: str) -> list[dict]:
    return [
        _source_dict(row)
        for row in conn.execute(
            "SELECT * FROM context_sources WHERE context_id=? ORDER BY created_at DESC",
            (context_id,),
        )
    ]


def update_source(
    conn: sqlite3.Connection, source_id: str, data: ContextSourceUpdate
) -> dict | None:
    existing = get_source(conn, source_id)
    if not existing:
        return None
    changes = data.model_dump(exclude_unset=True)
    assignments: list[str] = []
    values: list[Any] = []
    for key, value in changes.items():
        assignments.append(f"{key}=?")
        if key == "metadata":
            value = json.dumps(value, ensure_ascii=False)
        elif key == "enabled":
            value = 1 if value else 0
        values.append(value)
    if assignments:
        assignments.append("updated_at=?")
        values.extend([_now(), source_id])
        conn.execute(
            f"UPDATE context_sources SET {', '.join(assignments)} WHERE source_id=?",
            values,
        )
        conn.execute(
            "UPDATE contexts SET status='draft', updated_at=? WHERE context_id=?",
            (_now(), existing["context_id"]),
        )
        conn.commit()
    return get_source(conn, source_id)


def delete_source(conn: sqlite3.Connection, source_id: str) -> bool:
    existing = get_source(conn, source_id)
    if not existing:
        return False
    conn.execute("DELETE FROM context_sources WHERE source_id=?", (source_id,))
    conn.execute(
        "UPDATE contexts SET status='draft', updated_at=? WHERE context_id=?",
        (_now(), existing["context_id"]),
    )
    conn.commit()
    return True
