"""Ingest page content into an offline pack (used by the browser extension).

Security:
- Requires the app token (like every /api route).
- The server NEVER fetches URLs itself - the extension sends already-loaded
  page text. This eliminates SSRF: there is no server-side outbound request.
- HTML is sanitized to plain text; scripts/styles are dropped.
- Payloads are size-limited.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import connect
from ..security import require_token
from ..services import retrieval
from ..services.packs import KOREA_PACK_ID

router = APIRouter(dependencies=[Depends(require_token)])

MAX_TEXT_CHARS = 200_000
MAX_STORE_CHARS = 8_000  # per clip, keep packs lean


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            t = data.strip()
            if t:
                self.parts.append(t)


def _to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return " ".join(p.parts)


class IngestRequest(BaseModel):
    title: str = Field(default="", max_length=500)
    text: str | None = Field(default=None)
    html: str | None = Field(default=None)
    url: str = Field(default="", max_length=2000)
    pack_id: str | None = None


@router.post("/api/ingest")
def ingest(req: IngestRequest) -> dict:
    raw = req.text or (_to_text(req.html) if req.html else "")
    if len(raw) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="Content too large")
    text = raw.strip()[:MAX_STORE_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No usable text content")

    pack_id = req.pack_id or KOREA_PACK_ID
    digest = hashlib.sha256((req.url + text).encode()).hexdigest()[:10]
    source_id = f"clip-{digest}"
    title = req.title or (req.url[:80] if req.url else "Saved page")

    conn = connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM documents WHERE pack_id=? AND source_id=?",
            (pack_id, source_id),
        ).fetchone()
        if exists:
            return {"source_id": source_id, "status": "already_saved"}
        doc_id = retrieval.ingest_document(
            conn, pack_id, source_id, title, text,
            lang="", tier=3, stable=True,
            aliases=[req.url] if req.url else [],
            meta={"topic": "clip", "url": req.url},
        )
        conn.commit()
        return {"source_id": source_id, "doc_id": doc_id, "status": "saved"}
    finally:
        conn.close()
