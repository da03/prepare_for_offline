"""Pack building and summaries.

Phase 1 builds one Korea language/culture pack from the curated seed corpus.
The pack is a first-class artifact with a manifest; experts are resolved to
immutable program ids when compiled. Full resumable/atomic jobs come in Phase 2.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..config import get_settings
from . import retrieval, seed

KOREA_PACK_ID = "korea-language"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest(pack_id: str, title: str, experts: list[dict]) -> dict:
    return {
        "pack_id": pack_id,
        "title": title,
        "created_at": _now(),
        "languages": ["en", "ko"],
        "experts": experts,
        "models": {
            "paw_interpreter": get_settings().interpreter,
            "embedding_model": None,
        },
        "tiers": {"answer_cards": True, "structured_facts": True, "raw_chunks": False},
        "readiness": {"offline_tested": False},
    }


def build_korea_pack(
    conn: sqlite3.Connection,
    selected_topics: list[str] | None = None,
) -> str:
    """(Re)build the Korea pack corpus + answer cards. Idempotent.

    If selected_topics is given, only entries in those topics are included
    (this is how the storage budget shapes the pack).
    """
    pack_id = KOREA_PACK_ID
    topics = set(selected_topics) if selected_topics else set(seed.TOPICS)

    # Clear any prior content for a clean rebuild.
    conn.execute("DELETE FROM documents WHERE pack_id=?", (pack_id,))
    conn.execute("DELETE FROM documents_fts")
    conn.execute("DELETE FROM answer_cards WHERE pack_id=?", (pack_id,))
    conn.execute("DELETE FROM card_grams")
    conn.execute("DELETE FROM doc_grams")

    for d in seed.DOCUMENTS:
        if d["topic"] not in topics:
            continue
        retrieval.ingest_document(
            conn, pack_id, d["source_id"], d["title"], d["text"],
            lang="ko", tier=d["tier"], stable=d["stable"], as_of=d.get("as_of"),
            aliases=d["aliases"], meta={"topic": d["topic"]},
        )
    for c in seed.ANSWER_CARDS:
        if c["topic"] not in topics:
            continue
        retrieval.ingest_answer_card(
            conn, pack_id, c["question"], c["answer"],
            sources=c["sources"], support="high", stable=c.get("stable", True),
            as_of=c.get("as_of"), aliases=c["aliases"],
        )

    manifest = _manifest(pack_id, "South Korea - language & culture", experts=[])
    manifest["topics"] = sorted(topics)
    size = _estimate_size(conn, pack_id)
    conn.execute(
        "INSERT INTO packs (pack_id, title, manifest, ready, size_bytes, created_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(pack_id) DO UPDATE SET manifest=excluded.manifest, "
        "size_bytes=excluded.size_bytes",
        (pack_id, manifest["title"], json.dumps(manifest, ensure_ascii=False),
         0, size, _now()),
    )
    conn.commit()
    return pack_id


def mark_ready(conn: sqlite3.Connection, pack_id: str, offline_tested: bool) -> None:
    row = conn.execute("SELECT manifest FROM packs WHERE pack_id=?", (pack_id,)).fetchone()
    manifest = json.loads(row["manifest"]) if row else {}
    manifest.setdefault("readiness", {})["offline_tested"] = offline_tested
    conn.execute(
        "UPDATE packs SET ready=1, manifest=? WHERE pack_id=?",
        (json.dumps(manifest, ensure_ascii=False), pack_id),
    )
    conn.commit()


def finalize_pack(
    conn: sqlite3.Connection,
    pack_id: str,
    *,
    plan: dict,
    coverage: dict,
    checksum: str,
    offline_tested: bool,
) -> None:
    """Atomically record the plan/coverage/checksum and flip the pack to ready.

    A pack is only ready if the offline smoke test passed; otherwise it stays
    un-ready so the UI does not present an untrusted pack.
    """
    row = conn.execute("SELECT manifest FROM packs WHERE pack_id=?", (pack_id,)).fetchone()
    manifest = json.loads(row["manifest"]) if row else {}
    manifest["plan"] = plan
    manifest["coverage"] = coverage
    manifest["checksum"] = checksum
    manifest["readiness"] = {"offline_tested": offline_tested, "verified_at": _now()}
    manifest["valid_for"] = manifest.get("valid_for")
    size = _estimate_size(conn, pack_id)
    conn.execute(
        "UPDATE packs SET ready=?, manifest=?, size_bytes=? WHERE pack_id=?",
        (1 if offline_tested else 0, json.dumps(manifest, ensure_ascii=False), size, pack_id),
    )
    conn.commit()


def attach_expert(conn: sqlite3.Connection, pack_id: str, expert: dict) -> None:
    row = conn.execute("SELECT manifest FROM packs WHERE pack_id=?", (pack_id,)).fetchone()
    if not row:
        return
    manifest = json.loads(row["manifest"])
    experts = [e for e in manifest.get("experts", []) if e.get("role") != expert["role"]]
    experts.append(expert)
    manifest["experts"] = experts
    conn.execute(
        "UPDATE packs SET manifest=? WHERE pack_id=?",
        (json.dumps(manifest, ensure_ascii=False), pack_id),
    )
    conn.commit()


def _estimate_size(conn: sqlite3.Connection, pack_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(text)+LENGTH(title)),0) AS b FROM documents WHERE pack_id=?",
        (pack_id,),
    ).fetchone()
    docs = int(row["b"]) if row else 0
    row2 = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(answer)+LENGTH(question)),0) AS b FROM answer_cards WHERE pack_id=?",
        (pack_id,),
    ).fetchone()
    cards = int(row2["b"]) if row2 else 0
    return docs + cards


def list_packs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM packs ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        out.append({
            "pack_id": r["pack_id"],
            "title": r["title"],
            "ready": bool(r["ready"]),
            "size_bytes": int(r["size_bytes"]),
            "created_at": r["created_at"],
            "manifest": json.loads(r["manifest"]),
        })
    return out
