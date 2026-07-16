"""SQLite storage and versioned migrations.

Retrieval uses two complementary local indexes, both offline:
- FTS5 over document text/title (lexical / BM25-style).
- A character trigram table over a normalized romanized form, so phonetic or
  misspelled input (e.g. "simida") can still reach the canonical entry
  ("seumnida" / "-습니다").

The product data model separates:
- Context: editable user intent/configuration.
- Pack: immutable prepared artifact/version derived from a context.
- Conversation/message: persisted interaction history.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    pack_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    manifest    TEXT NOT NULL,
    ready       INTEGER NOT NULL DEFAULT 0,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experts (
    pack_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    program_id  TEXT,
    compiler    TEXT,
    spec        TEXT,
    PRIMARY KEY (pack_id, role)
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id     TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT '',
    tier        INTEGER NOT NULL DEFAULT 3,
    stable      INTEGER NOT NULL DEFAULT 1,
    as_of       TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, text, content='documents', content_rowid='doc_id'
);

CREATE TABLE IF NOT EXISTS doc_grams (
    doc_id  INTEGER NOT NULL,
    gram    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_grams_gram ON doc_grams(gram);
CREATE INDEX IF NOT EXISTS idx_doc_grams_doc ON doc_grams(doc_id);

CREATE TABLE IF NOT EXISTS answer_cards (
    card_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id     TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    sources     TEXT NOT NULL DEFAULT '[]',
    support     TEXT NOT NULL DEFAULT 'high',
    stable      INTEGER NOT NULL DEFAULT 1,
    as_of       TEXT
);

CREATE TABLE IF NOT EXISTS card_grams (
    card_id INTEGER NOT NULL,
    gram    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_card_grams_gram ON card_grams(gram);

CREATE TABLE IF NOT EXISTS question_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id         TEXT,
    question        TEXT NOT NULL,
    offline_answer  TEXT,
    offline_support TEXT,
    offline_sources TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'pending',
    verified_answer TEXT,
    changed         INTEGER,
    created_at      TEXT NOT NULL,
    verified_at     TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    pack_id     TEXT,
    state       TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT '{}',
    progress    TEXT NOT NULL DEFAULT '[]',
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""

LATEST_SCHEMA_VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split()[0]
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migration_1(conn: sqlite3.Connection) -> None:
    """Add editable contexts, sources, settings, and conversation history."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contexts (
            context_id              TEXT PRIMARY KEY,
            name                    TEXT NOT NULL,
            context_type            TEXT NOT NULL DEFAULT 'custom',
            goal                    TEXT NOT NULL DEFAULT '',
            starts_at               TEXT,
            ends_at                 TEXT,
            languages               TEXT NOT NULL DEFAULT '[]',
            interests               TEXT NOT NULL DEFAULT '[]',
            expected_needs          TEXT NOT NULL DEFAULT '[]',
            storage_budget_mb       INTEGER NOT NULL DEFAULT 1200,
            privacy_mode            TEXT NOT NULL DEFAULT 'local_only',
            preparation_quality     TEXT NOT NULL DEFAULT 'fast',
            active_pack_id          TEXT,
            template_id             TEXT,
            status                  TEXT NOT NULL DEFAULT 'draft',
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS context_sources (
            source_id       TEXT PRIMARY KEY,
            context_id      TEXT NOT NULL,
            title           TEXT NOT NULL,
            source_type     TEXT NOT NULL DEFAULT 'text',
            url             TEXT,
            local_path      TEXT,
            content         TEXT NOT NULL DEFAULT '',
            metadata        TEXT NOT NULL DEFAULT '{}',
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_context_sources_context
            ON context_sources(context_id, enabled);

        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            context_id      TEXT,
            title           TEXT NOT NULL DEFAULT 'New conversation',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(updated_at DESC);

        CREATE TABLE IF NOT EXISTS messages (
            message_id      TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            kind            TEXT NOT NULL DEFAULT 'text',
            content         TEXT NOT NULL DEFAULT '',
            payload         TEXT NOT NULL DEFAULT '{}',
            sources         TEXT NOT NULL DEFAULT '[]',
            pack_id         TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, created_at);

        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        """
    )
    _add_column(conn, "packs", "context_id TEXT")
    _add_column(conn, "packs", "version INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "packs", "is_current INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "jobs", "context_id TEXT")
    _add_column(conn, "jobs", "cancel_requested INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "question_queue", "conversation_id TEXT")
    _add_column(conn, "question_queue", "message_id TEXT")


def _migration_2(conn: sqlite3.Connection) -> None:
    """Seed safe release defaults without creating a hidden Korea context."""
    defaults = {
        "theme": "system",
        "active_context_id": None,
        "privacy_mode": "local_only",
        "default_storage_budget_mb": 1200,
        "show_advanced": False,
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (key, dumps(value), _now()),
        )


def _migration_3(conn: sqlite3.Connection) -> None:
    """Link an existing Korea demo pack to an explicit optional example context."""
    pack = conn.execute(
        "SELECT pack_id, title FROM packs WHERE pack_id='korea-language'"
    ).fetchone()
    if not pack:
        return
    context_id = "example-korea"
    now = _now()
    conn.execute(
        """
        INSERT OR IGNORE INTO contexts (
            context_id, name, context_type, goal, languages, interests,
            expected_needs, storage_budget_mb, privacy_mode,
            preparation_quality, active_pack_id, template_id, status,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            context_id,
            "South Korea example",
            "trip",
            "Understand Korean language, food, transport, and local customs.",
            dumps(["en", "ko"]),
            dumps(["language", "food", "transport", "etiquette", "money", "safety"]),
            dumps([]),
            1200,
            "local_only",
            "fast",
            pack["pack_id"],
            "korea",
            "ready",
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE packs SET context_id=? WHERE pack_id=? AND context_id IS NULL",
        (context_id, pack["pack_id"]),
    )
    current = conn.execute(
        "SELECT value FROM settings WHERE key='active_context_id'"
    ).fetchone()
    if current and current["value"] in ("null", '""', ""):
        conn.execute(
            "UPDATE settings SET value=?, updated_at=? WHERE key='active_context_id'",
            (dumps(context_id), now),
        )


def _migration_4(conn: sqlite3.Connection) -> None:
    """Add travel brief, freshness, search, and compiler promotion state."""
    _add_column(conn, "contexts", "trip_brief TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "contexts", "suggested_questions TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "contexts", "prepared_at TEXT")
    _add_column(conn, "contexts", "optimization_status TEXT NOT NULL DEFAULT 'idle'")
    _add_column(conn, "contexts", "search_enabled INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "contexts", "search_refreshed_at TEXT")

    _add_column(conn, "context_sources", "publisher TEXT")
    _add_column(conn, "context_sources", "quality_tier TEXT")
    _add_column(conn, "context_sources", "freshness_class TEXT")
    _add_column(conn, "context_sources", "retrieved_at TEXT")
    _add_column(conn, "context_sources", "source_updated_at TEXT")
    _add_column(conn, "context_sources", "expires_at TEXT")
    _add_column(conn, "context_sources", "license TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS program_versions (
            version_id      TEXT PRIMARY KEY,
            context_id     TEXT,
            pack_id        TEXT,
            role           TEXT NOT NULL,
            program_id     TEXT NOT NULL,
            compiler       TEXT NOT NULL,
            stage          TEXT NOT NULL,
            score          REAL,
            metrics        TEXT NOT NULL DEFAULT '{}',
            is_active      INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_program_versions_pack_role
            ON program_versions(pack_id, role, created_at);

        CREATE TABLE IF NOT EXISTS search_runs (
            search_run_id  TEXT PRIMARY KEY,
            context_id     TEXT NOT NULL,
            provider       TEXT NOT NULL,
            queries        TEXT NOT NULL DEFAULT '[]',
            status         TEXT NOT NULL,
            stats          TEXT NOT NULL DEFAULT '{}',
            gaps           TEXT NOT NULL DEFAULT '[]',
            error          TEXT,
            created_at     TEXT NOT NULL,
            completed_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_search_runs_context
            ON search_runs(context_id, created_at DESC);
        """
    )


def _migration_5(conn: sqlite3.Connection) -> None:
    """Add simple travel UX defaults."""
    defaults = {
        "optimize_in_background": True,
        "search_mode": "automatic",
        "ask_history_window": 3,
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (key, dumps(value), _now()),
        )


MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
}


def connect() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        applied = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations")
        }
        for version in range(1, LATEST_SCHEMA_VERSION + 1):
            if version in applied:
                continue
            MIGRATIONS[version](conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                (version, _now()),
            )
        conn.commit()
    finally:
        conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def db_path() -> Path:
    return get_settings().db_path
