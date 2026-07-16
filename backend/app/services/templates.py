"""Optional example templates.

Templates are explicit user choices, never hidden defaults. User-provided
sources and context configuration remain the primary product model.
"""

from __future__ import annotations

from . import seed

TEMPLATES: dict[str, dict] = {
    "korea": {
        "template_id": "korea",
        "name": "South Korea travel example",
        "description": "Language, food, transport, etiquette, money, and safety.",
        "context_type": "trip",
        "languages": ["en", "ko"],
        "interests": ["language", "food", "transport", "etiquette", "money", "safety"],
        "topics": list(seed.TOPICS),
    },
}


def list_templates() -> list[dict]:
    return [dict(value) for value in TEMPLATES.values()]


def get(template_id: str | None) -> dict | None:
    if not template_id:
        return None
    value = TEMPLATES.get(template_id)
    return dict(value) if value else None
