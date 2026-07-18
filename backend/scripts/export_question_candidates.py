"""Export locally retained questions for manual benchmark curation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _payload(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def export_questions(
    conn: sqlite3.Connection, *, include_answers: bool = False
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT role, content, payload, created_at
        FROM messages
        ORDER BY conversation_id, created_at
        """
    ).fetchall()
    pending_question: sqlite3.Row | None = None
    by_question: dict[str, dict] = {}
    for row in rows:
        if row["role"] == "user":
            pending_question = row
            continue
        if row["role"] != "assistant" or pending_question is None:
            continue

        question = " ".join(pending_question["content"].strip().split())
        key = question.casefold()
        if not question:
            pending_question = None
            continue
        user_payload = _payload(pending_question["payload"])
        answer_payload = _payload(row["payload"])
        existing = by_question.get(key)
        if existing:
            existing["occurrences"] += 1
            existing["last_asked_at"] = pending_question["created_at"]
        else:
            item = {
                "question": question,
                "occurrences": 1,
                "first_asked_at": pending_question["created_at"],
                "last_asked_at": pending_question["created_at"],
                "used_context": bool(user_payload.get("used_context")),
                "program_labels": answer_payload.get("program_labels", []),
            }
            standalone = user_payload.get("standalone_query")
            if isinstance(standalone, str) and standalone.strip() != question:
                item["standalone_query"] = standalone.strip()
            if include_answers:
                item["answer"] = row["content"]
            by_question[key] = item
        pending_question = None
    return sorted(
        by_question.values(),
        key=lambda item: (-item["occurrences"], item["first_asked_at"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".prepare_offline" / "prepare_offline.db",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-answers",
        action="store_true",
        help="Include generated answers and their possible personal context.",
    )
    args = parser.parse_args()

    uri = f"{args.db.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        questions = export_questions(
            conn, include_answers=args.include_answers
        )
    finally:
        conn.close()
    document = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "privacy": (
            "Local manual export. Review and redact questions before adding "
            "them to a benchmark or sharing them."
        ),
        "question_count": len(questions),
        "questions": questions,
    }
    rendered = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
        print(f"Exported {len(questions)} questions to {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
