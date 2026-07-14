"""Local retrieval: FTS5 lexical search fused with character-trigram matching.

Ingestion writes documents (Tier 2/3) and precomputed answer cards (Tier 1),
building both an FTS5 index and a trigram table (over text + curated phonetic
aliases). Search fuses a lexical signal with trigram overlap so phonetic input
still surfaces the right entry.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .textnorm import normalize, trigrams

_FTS_TOKEN = re.compile(r"[0-9A-Za-z\u00c0-\uffff]+")

# Common English question words carry no retrieval signal and, worse, inflate
# every document's score. Strip them from the QUERY side only (never from
# indexed content or resolver-proposed terms).
_STOPWORDS = {
    "what", "whats", "is", "are", "the", "a", "an", "of", "to", "do", "does",
    "did", "how", "i", "me", "my", "mean", "means", "meaning", "in", "on",
    "for", "this", "that", "it", "and", "or", "with", "can", "should", "was",
    "were", "when", "where", "why", "who", "which", "you", "your",
}


def _strip_stopwords(text: str) -> str:
    kept = [t for t in normalize(text).split(" ") if t and t not in _STOPWORDS]
    return " ".join(kept) if kept else normalize(text)


@dataclass
class Candidate:
    source_id: str
    title: str
    text: str
    tier: int
    stable: bool
    as_of: str | None
    score: float
    fts_hit: bool = False
    tri: float = 0.0
    meta: dict = field(default_factory=dict)


def _fts_query(text: str) -> str:
    tokens = _FTS_TOKEN.findall(text or "")
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def ingest_document(
    conn: sqlite3.Connection,
    pack_id: str,
    source_id: str,
    title: str,
    text: str,
    *,
    lang: str = "",
    tier: int = 3,
    stable: bool = True,
    as_of: str | None = None,
    aliases: list[str] | None = None,
    meta: dict | None = None,
) -> int:
    import json

    meta = dict(meta or {})
    meta["aliases"] = aliases or []
    cur = conn.execute(
        "INSERT INTO documents (pack_id, source_id, title, text, lang, tier, stable, as_of, meta)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            pack_id,
            source_id,
            title,
            text,
            lang,
            tier,
            1 if stable else 0,
            as_of,
            json.dumps(meta, ensure_ascii=False),
        ),
    )
    doc_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO documents_fts (rowid, title, text) VALUES (?,?,?)",
        (doc_id, title, text),
    )
    gram_source = " ".join([title, text, " ".join(aliases or [])])
    grams = trigrams(gram_source)
    conn.executemany(
        "INSERT INTO doc_grams (doc_id, gram) VALUES (?,?)",
        [(doc_id, g) for g in grams],
    )
    return doc_id


def ingest_answer_card(
    conn: sqlite3.Connection,
    pack_id: str,
    question: str,
    answer: str,
    *,
    sources: list[str] | None = None,
    support: str = "high",
    stable: bool = True,
    as_of: str | None = None,
    aliases: list[str] | None = None,
) -> int:
    import json

    cur = conn.execute(
        "INSERT INTO answer_cards (pack_id, question, answer, sources, support, stable, as_of)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            pack_id,
            question,
            answer,
            json.dumps(sources or [], ensure_ascii=False),
            support,
            1 if stable else 0,
            as_of,
        ),
    )
    card_id = int(cur.lastrowid)
    gram_source = " ".join([question, " ".join(aliases or [])])
    grams = trigrams(gram_source)
    conn.executemany(
        "INSERT INTO card_grams (card_id, gram) VALUES (?,?)",
        [(card_id, g) for g in grams],
    )
    return card_id


def _trigram_scores(
    conn: sqlite3.Connection,
    table: str,
    id_col: str,
    grams: set[str],
    pack_filter_sql: str,
    pack_id: str,
) -> dict[int, float]:
    if not grams:
        return {}
    placeholders = ",".join("?" for _ in grams)
    sql = (
        f"SELECT g.{id_col} AS oid, COUNT(*) AS shared FROM {table} g "
        f"{pack_filter_sql} WHERE g.gram IN ({placeholders}) "
        f"AND parent.pack_id = ? GROUP BY g.{id_col}"
    )
    rows = conn.execute(sql, (*grams, pack_id)).fetchall()
    denom = float(len(grams))
    return {int(r["oid"]): r["shared"] / denom for r in rows}


def match_answer_card(
    conn: sqlite3.Connection,
    pack_id: str,
    query: str,
    *,
    extra_terms: list[str] | None = None,
    threshold: float = 0.62,
) -> dict | None:
    import json

    text = " ".join([query, *(extra_terms or [])])
    grams = trigrams(text)
    scores = _trigram_scores(
        conn,
        table="card_grams",
        id_col="card_id",
        grams=grams,
        pack_filter_sql="JOIN answer_cards parent ON parent.card_id = g.card_id",
        pack_id=pack_id,
    )
    if not scores:
        return None
    best_id = max(scores, key=scores.get)
    if scores[best_id] < threshold:
        return None
    row = conn.execute(
        "SELECT * FROM answer_cards WHERE card_id = ?", (best_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "card_id": best_id,
        "question": row["question"],
        "answer": row["answer"],
        "sources": json.loads(row["sources"]),
        "support": row["support"],
        "stable": bool(row["stable"]),
        "as_of": row["as_of"],
        "score": scores[best_id],
    }


def search(
    conn: sqlite3.Connection,
    pack_id: str,
    query: str,
    *,
    extra_terms: list[str] | None = None,
    limit: int = 5,
) -> list[Candidate]:
    import json

    # Query-side text has stopwords stripped; resolver-proposed terms are kept
    # verbatim because they are already high-signal canonical candidates.
    joined = " ".join([_strip_stopwords(query), *(extra_terms or [])]).strip()

    # Lexical (FTS5). rank via bm25 (lower is better -> invert to [0,1]).
    fts_scores: dict[int, float] = {}
    fts_query = _fts_query(joined)
    if fts_query:
        rows = conn.execute(
            "SELECT d.doc_id AS doc_id, bm25(documents_fts) AS rank "
            "FROM documents_fts f JOIN documents d ON d.doc_id = f.rowid "
            "WHERE documents_fts MATCH ? AND d.pack_id = ? "
            "ORDER BY rank LIMIT 50",
            (fts_query, pack_id),
        ).fetchall()
        for r in rows:
            fts_scores[int(r["doc_id"])] = 1.0 / (1.0 + max(0.0, float(r["rank"])))

    # Trigram overlap (phonetic-tolerant).
    tri_scores = _trigram_scores(
        conn,
        table="doc_grams",
        id_col="doc_id",
        grams=trigrams(joined),
        pack_filter_sql="JOIN documents parent ON parent.doc_id = g.doc_id",
        pack_id=pack_id,
    )

    doc_ids = set(fts_scores) | set(tri_scores)
    if not doc_ids:
        return []

    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        f"SELECT * FROM documents WHERE doc_id IN ({placeholders})",
        tuple(doc_ids),
    ).fetchall()

    candidates: list[Candidate] = []
    for row in rows:
        doc_id = int(row["doc_id"])
        fts = fts_scores.get(doc_id, 0.0)
        tri = tri_scores.get(doc_id, 0.0)
        score = 0.55 * fts + 0.45 * tri
        candidates.append(
            Candidate(
                source_id=row["source_id"],
                title=row["title"],
                text=row["text"],
                tier=int(row["tier"]),
                stable=bool(row["stable"]),
                as_of=row["as_of"],
                score=score,
                fts_hit=doc_id in fts_scores,
                tri=tri,
                meta=json.loads(row["meta"]),
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)

    # Optional embedding rerank (no-op unless an embedding GGUF is configured).
    from . import vector

    if vector.enabled():
        candidates = vector.rerank(joined, candidates)

    return candidates[:limit]
