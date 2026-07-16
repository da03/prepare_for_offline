"""Build immutable, versioned packs from editable contexts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..config import get_settings
from . import contexts, retrieval, seed

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


def _clear_pack_indexes(conn: sqlite3.Connection, pack_id: str) -> None:
    doc_ids = [
        row["doc_id"]
        for row in conn.execute("SELECT doc_id FROM documents WHERE pack_id=?", (pack_id,))
    ]
    card_ids = [
        row["card_id"]
        for row in conn.execute("SELECT card_id FROM answer_cards WHERE pack_id=?", (pack_id,))
    ]
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        conn.execute(f"DELETE FROM doc_grams WHERE doc_id IN ({placeholders})", doc_ids)
        conn.execute(f"DELETE FROM documents_fts WHERE rowid IN ({placeholders})", doc_ids)
    if card_ids:
        placeholders = ",".join("?" for _ in card_ids)
        conn.execute(f"DELETE FROM card_grams WHERE card_id IN ({placeholders})", card_ids)
    conn.execute("DELETE FROM documents WHERE pack_id=?", (pack_id,))
    conn.execute("DELETE FROM answer_cards WHERE pack_id=?", (pack_id,))


def _chunks(text: str, target_chars: int = 1800, overlap_chars: int = 200) -> list[str]:
    """Paragraph-aware local chunking with a small overlap."""
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(clean) <= target_chars:
        return [clean] if clean else []
    paragraphs = clean.split("\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > target_chars:
            chunks.append(current)
            overlap = current[-overlap_chars:]
            current = f"{overlap}\n{paragraph}"
        else:
            current = f"{current}\n{paragraph}".strip()
        while len(current) > target_chars * 2:
            chunks.append(current[:target_chars])
            current = current[target_chars - overlap_chars :]
    if current:
        chunks.append(current)
    return chunks


def build_pack(conn: sqlite3.Connection, context: dict, plan: dict) -> str:
    """Create a new immutable pack version. It is not activated until ready."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version),0) AS v FROM packs WHERE context_id=?",
        (context["context_id"],),
    ).fetchone()
    version = int(row["v"]) + 1
    pack_id = f"pack-{context['context_id'].removeprefix('ctx-')}-v{version}-{uuid.uuid4().hex[:6]}"
    manifest = _manifest(pack_id, context["name"], experts=[])
    manifest.update(
        {
            "context_id": context["context_id"],
            "context_type": context["context_type"],
            "goal": context["goal"],
            "languages": context["languages"],
            "version": version,
            "template_id": plan.get("template_id"),
            "topics": plan.get("selected_topics", []),
            "source_ids": plan.get("selected_source_ids", []),
            "readiness": {"offline_tested": False},
        }
    )
    conn.execute(
        """
        INSERT INTO packs (
            pack_id, title, manifest, ready, size_bytes, created_at,
            context_id, version, is_current
        ) VALUES (?,?,?,0,0,?,?,?,0)
        """,
        (
            pack_id,
            context["name"],
            json.dumps(manifest, ensure_ascii=False),
            _now(),
            context["context_id"],
            version,
        ),
    )

    if plan.get("template_id") == "korea":
        topics = set(plan.get("selected_topics", seed.TOPICS))
        for document in seed.DOCUMENTS:
            if document["topic"] not in topics:
                continue
            retrieval.ingest_document(
                conn,
                pack_id,
                document["source_id"],
                document["title"],
                document["text"],
                lang="ko",
                tier=document["tier"],
                stable=document["stable"],
                as_of=document.get("as_of"),
                aliases=document["aliases"],
                meta={"topic": document["topic"], "template_id": "korea"},
            )
        for card in seed.ANSWER_CARDS:
            if card["topic"] not in topics:
                continue
            retrieval.ingest_answer_card(
                conn,
                pack_id,
                card["question"],
                card["answer"],
                sources=card["sources"],
                support="high",
                stable=card.get("stable", True),
                as_of=card.get("as_of"),
                aliases=card["aliases"],
            )

    selected_sources = set(plan.get("selected_source_ids", []))
    for source in contexts.list_sources(conn, context["context_id"]):
        if not source["enabled"] or source["source_id"] not in selected_sources:
            continue
        if not source["content"].strip():
            continue
        metadata = dict(source["metadata"])
        metadata.update(
            {
                "context_source_id": source["source_id"],
                "source_type": source["source_type"],
                "url": source["url"],
                "publisher": source.get("publisher") or metadata.get("publisher"),
                "quality_tier": source.get("quality_tier")
                or metadata.get("quality_tier"),
                "freshness_class": source.get("freshness_class")
                or metadata.get("freshness_class"),
                "retrieved_at": source.get("retrieved_at")
                or metadata.get("retrieved_at"),
                "source_updated_at": source.get("source_updated_at")
                or metadata.get("source_updated_at"),
                "expires_at": source.get("expires_at")
                or metadata.get("expires_at"),
                "license": source.get("license") or metadata.get("license"),
            }
        )
        chunks = _chunks(source["content"])
        for index, chunk in enumerate(chunks):
            chunk_id = (
                source["source_id"]
                if len(chunks) == 1
                else f"{source['source_id']}:{index + 1}"
            )
            chunk_meta = {
                **metadata,
                "chunk_index": index,
                "chunk_count": len(chunks),
            }
            retrieval.ingest_document(
                conn,
                pack_id,
                chunk_id,
                source["title"],
                chunk,
                tier=int(metadata.get("tier", 3)),
                stable=bool(metadata.get("stable", True)),
                as_of=metadata.get("source_updated_at")
                or metadata.get("retrieved_at")
                or metadata.get("as_of"),
                aliases=metadata.get("aliases", []),
                meta=chunk_meta,
            )

    size = _estimate_size(conn, pack_id)
    conn.execute("UPDATE packs SET size_bytes=? WHERE pack_id=?", (size, pack_id))
    conn.commit()
    return pack_id


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

    # Compatibility path used by the Korea evaluation harness.
    _clear_pack_indexes(conn, pack_id)

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
    if offline_tested and manifest.get("context_id"):
        conn.execute(
            "UPDATE packs SET is_current=0 WHERE context_id=? AND pack_id<>?",
            (manifest["context_id"], pack_id),
        )
        conn.execute("UPDATE packs SET is_current=1 WHERE pack_id=?", (pack_id,))
        contexts.set_active_pack(conn, manifest["context_id"], pack_id, "ready")
        conn.execute(
            "UPDATE contexts SET prepared_at=? WHERE context_id=?",
            (_now(), manifest["context_id"]),
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


def delete_pack(conn: sqlite3.Connection, pack_id: str) -> bool:
    row = conn.execute("SELECT * FROM packs WHERE pack_id=?", (pack_id,)).fetchone()
    if not row:
        return False
    _clear_pack_indexes(conn, pack_id)
    conn.execute("DELETE FROM experts WHERE pack_id=?", (pack_id,))
    conn.execute("DELETE FROM packs WHERE pack_id=?", (pack_id,))
    if row["context_id"] and bool(row["is_current"]):
        fallback = conn.execute(
            "SELECT pack_id FROM packs WHERE context_id=? AND ready=1 "
            "ORDER BY version DESC LIMIT 1",
            (row["context_id"],),
        ).fetchone()
        if fallback:
            conn.execute(
                "UPDATE packs SET is_current=1 WHERE pack_id=?", (fallback["pack_id"],)
            )
            contexts.set_active_pack(conn, row["context_id"], fallback["pack_id"], "ready")
        else:
            conn.execute(
                "UPDATE contexts SET active_pack_id=NULL, status='draft', updated_at=? "
                "WHERE context_id=?",
                (_now(), row["context_id"]),
            )
    conn.commit()
    return True
