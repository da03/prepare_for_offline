"""SQLite storage: packs, experts, tiered knowledge, answer cards, verify queue, jobs.

Retrieval uses two complementary local indexes, both offline:
- FTS5 over document text/title (lexical / BM25-style).
- A character trigram table over a normalized romanized form, so phonetic or
  misspelled input (e.g. "simida") can still reach the canonical entry
  ("seumnida" / "-습니다").
"""

from __future__ import annotations

import json
import sqlite3
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
"""


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
        conn.commit()
    finally:
        conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def db_path() -> Path:
    return get_settings().db_path
