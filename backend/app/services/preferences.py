"""Persisted product preferences."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..models import SettingsUpdate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_all(conn: sqlite3.Connection) -> dict:
    result: dict = {}
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            result[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            result[row["key"]] = row["value"]
    return result


def update(conn: sqlite3.Connection, data: SettingsUpdate) -> dict:
    for key, value in data.model_dump(exclude_unset=True).items():
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), _now()),
        )
    conn.commit()
    return get_all(conn)
