"""Persisted root-question threads and bounded follow-up history."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conversation_dict(
    row: sqlite3.Row, *, question_count: int | None = None
) -> dict:
    result = {
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if question_count is not None:
        result["question_count"] = question_count
    return result


def _parse_json(value: str, fallback):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "payload": _parse_json(row["payload"], {}),
        "created_at": row["created_at"],
    }


def create(
    conn: sqlite3.Connection,
    title: str = "New conversation",
) -> dict:
    conversation_id = f"conv-{uuid.uuid4().hex[:12]}"
    now = _now()
    conn.execute(
        """
        INSERT INTO conversations (
            conversation_id, title, created_at, updated_at
        ) VALUES (?,?,?,?)
        """,
        (conversation_id, title, now, now),
    )
    conn.commit()
    return get(conn, conversation_id)


def get(conn: sqlite3.Connection, conversation_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return _conversation_dict(row) if row else None


def list_all(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses: list[str] = []
    args: list[Any] = []
    if query:
        clauses.append(
            "(LOWER(c.title) LIKE ? OR EXISTS ("
            "SELECT 1 FROM messages m2 WHERE m2.conversation_id=c.conversation_id "
            "AND LOWER(m2.content) LIKE ?))"
        )
        pattern = f"%{query.lower()}%"
        args.extend([pattern, pattern])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(limit)
    rows = conn.execute(
        f"""
        SELECT c.*,
               SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS question_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id=c.conversation_id
        {where}
        GROUP BY c.conversation_id
        ORDER BY c.updated_at DESC
        LIMIT ?
        """,
        args,
    ).fetchall()
    return [
        _conversation_dict(row, question_count=int(row["question_count"]))
        for row in rows
    ]


def delete(conn: sqlite3.Connection, conversation_id: str) -> bool:
    deleted = conn.execute(
        "DELETE FROM conversations WHERE conversation_id=?", (conversation_id,)
    ).rowcount > 0
    conn.commit()
    return deleted


def add_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    role: str,
    kind: str = "text",
    content: str = "",
    payload: dict | None = None,
) -> dict:
    message_id = f"msg-{uuid.uuid4().hex[:14]}"
    now = _now()
    conn.execute(
        """
        INSERT INTO messages (
            message_id, conversation_id, role, kind, content, payload, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            message_id,
            conversation_id,
            role,
            kind,
            content,
            json.dumps(payload or {}, ensure_ascii=False),
            now,
        ),
    )
    # Auto-title a brand new conversation from its first user message.
    if role == "user":
        row = conn.execute(
            "SELECT title, (SELECT COUNT(*) FROM messages WHERE conversation_id=?) AS n "
            "FROM conversations WHERE conversation_id=?",
            (conversation_id, conversation_id),
        ).fetchone()
        if row and row["title"] == "New conversation" and int(row["n"]) == 1:
            title = " ".join(content.strip().split())[:60] or "New conversation"
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE conversation_id=?",
                (title, now, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                (now, conversation_id),
            )
    else:
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
            (now, conversation_id),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
    return _message_dict(row)


def get_message(conn: sqlite3.Connection, message_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM messages WHERE message_id=?", (message_id,)
    ).fetchone()
    return _message_dict(row) if row else None


def answer_context(
    conn: sqlite3.Connection, answer_message_id: str
) -> dict | None:
    answer_row = conn.execute(
        "SELECT * FROM messages WHERE message_id=? AND role='assistant'",
        (answer_message_id,),
    ).fetchone()
    if not answer_row:
        return None

    answer = _message_dict(answer_row)
    question_message_id = answer["payload"].get("question_message_id")
    question_row = None
    if isinstance(question_message_id, str):
        question_row = conn.execute(
            "SELECT * FROM messages WHERE message_id=? AND role='user' "
            "AND conversation_id=?",
            (question_message_id, answer["conversation_id"]),
        ).fetchone()
    if question_row is None:
        question_row = conn.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id=? AND role='user' AND created_at<=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (answer["conversation_id"], answer["created_at"]),
        ).fetchone()
    if not question_row:
        return None
    question = _message_dict(question_row)
    return {
        "conversation_id": answer["conversation_id"],
        "question_message_id": question["message_id"],
        "question": question["content"],
        "answer_message_id": answer["message_id"],
        "answer": answer["content"],
    }


def messages(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    return [
        _message_dict(row)
        for row in conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conversation_id,),
        )
    ]
