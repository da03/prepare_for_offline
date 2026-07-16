"""Ingest page content into an editable context (used by the browser extension).

Security:
- Requires the app token (like every /api route).
- The server NEVER fetches URLs itself - the extension sends already-loaded
  page text. This eliminates SSRF: there is no server-side outbound request.
- HTML is sanitized to plain text; scripts/styles are dropped.
- Payloads are size-limited.
"""

from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import connect
from ..models import ContextSourceCreate
from ..security import require_token
from ..services import contexts

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
    context_id: str | None = None


@router.post("/api/ingest")
def ingest(req: IngestRequest) -> dict:
    raw = req.text or (_to_text(req.html) if req.html else "")
    if len(raw) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="Content too large")
    text = raw.strip()[:MAX_STORE_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No usable text content")

    digest = hashlib.sha256((req.url + text).encode()).hexdigest()[:10]
    title = req.title or (req.url[:80] if req.url else "Saved page")

    conn = connect()
    try:
        context_id = req.context_id
        if not context_id:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='active_context_id'"
            ).fetchone()
            if row:
                try:
                    context_id = json.loads(row["value"])
                except json.JSONDecodeError:
                    context_id = None
        if not context_id or not contexts.get(conn, context_id):
            raise HTTPException(
                status_code=409,
                detail="Choose or create an active context before saving this page.",
            )
        exists = conn.execute(
            "SELECT source_id FROM context_sources WHERE context_id=? "
            "AND json_extract(metadata, '$.digest')=?",
            (context_id, digest),
        ).fetchone()
        if exists:
            return {
                "source_id": exists["source_id"],
                "context_id": context_id,
                "status": "already_saved",
            }
        source = contexts.add_source(
            conn,
            context_id,
            ContextSourceCreate(
                title=title,
                source_type="web",
                url=req.url or None,
                content=text,
                metadata={
                    "digest": digest,
                    "aliases": [req.url] if req.url else [],
                    "stable": True,
                },
            ),
        )
        return {
            "source_id": source["source_id"],
            "context_id": context_id,
            "status": "saved",
            "rebuild_required": True,
        }
    finally:
        conn.close()
