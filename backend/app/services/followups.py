"""Bounded conversation context for travel follow-up questions."""

from __future__ import annotations

import re
import sqlite3
import os

from . import paw_experts


FOLLOWUP_RE = re.compile(
    r"^(what about|how about|and |also |then |there|that|it|they|on |for |is it|can i)",
    re.IGNORECASE,
)


def rewrite(
    conn: sqlite3.Connection,
    conversation_id: str | None,
    text: str,
    *,
    new_topic: bool = False,
    max_prior_turns: int = 3,
) -> dict:
    clean = " ".join(text.strip().split())
    if new_topic or not conversation_id:
        return {"query": clean, "used_context": False, "previous_question": None}
    rows = conn.execute(
        "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
        "ORDER BY created_at DESC LIMIT ?",
        (conversation_id, max_prior_turns),
    ).fetchall()
    if not rows:
        return {"query": clean, "used_context": False, "previous_question": None}
    short = len(clean.split()) <= 8
    if not short and not FOLLOWUP_RE.search(clean):
        return {"query": clean, "used_context": False, "previous_question": None}
    previous = rows[0]["content"]
    raw = (
        paw_experts.run_global(
            "followup_rewriter",
            f"Previous: {previous}\nFollow-up: {clean}",
            max_tokens=64,
        )
        if os.environ.get("PREPARE_OFFLINE_USE_PAW_REWRITER", "0") == "1"
        else None
    )
    query = raw.strip() if raw else f'{clean} (follow-up to: "{previous}")'
    return {"query": query, "used_context": True, "previous_question": previous}
