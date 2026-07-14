"""Likely-question synthesis.

Local-first and private by default: questions are generated from templates and
the curated library, never requiring any personal content to leave the device.
An optional online path (disabled unless explicitly enabled AND an API is
configured) could produce richer questions; it is deliberately opt-in.

Downstream, likely questions are used to: precompute answer cards, build the
evaluation set, and estimate coverage before a pack is marked ready.
"""

from __future__ import annotations

import os

from . import seed

# Templated questions per topic (local, private).
_TEMPLATES: dict[str, list[str]] = {
    "language": [
        "What does simida mean?",
        "How do I say thank you in Korean?",
        "How do I say hello in Korean?",
        "How do I ask how much something costs?",
        "Where is the restroom in Korean?",
    ],
    "food": [
        "What is bibimbap?",
        "What is bossam?",
        "Is tteokbokki spicy?",
        "Can I drink the white liquid served after the meal?",
        "Are the side dishes free?",
        "What is samgyeopsal?",
    ],
    "transport": [
        "How do I use the T-money card?",
        "What are the subway hours?",
        "How do taxis work in Korea?",
        "What is the KTX?",
    ],
    "etiquette": [
        "Do I take my shoes off indoors?",
        "Why are the three hotel elevators separated?",
        "How should I pour drinks for elders?",
    ],
    "money": [
        "Do I need to tip in Korea?",
        "What currency is used in Korea?",
        "Where can I withdraw cash?",
    ],
    "emergency": [
        "What number do I call in an emergency?",
        "Where do I find a pharmacy?",
    ],
    "itinerary": [],
}


def _online_enabled() -> bool:
    return os.environ.get("PREPARE_OFFLINE_ALLOW_ONLINE_SYNTH", "0") == "1"


def generate(topics: list[str], *, allow_online: bool = False) -> list[str]:
    questions: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        key = q.strip().lower()
        if key and key not in seen:
            seen.add(key)
            questions.append(q.strip())

    for topic in topics:
        for q in _TEMPLATES.get(topic, []):
            add(q)
    # Include curated answer-card questions for selected topics.
    for c in seed.ANSWER_CARDS:
        if c["topic"] in topics:
            add(c["question"])

    if allow_online and _online_enabled():
        # Placeholder for an opt-in online synthesis call. Intentionally not
        # implemented against a live API here; local generation is the private
        # default. Only behavioral hints (topics), never personal content,
        # would ever be sent.
        pass

    return questions
