"""Fast, private parsing of one-sentence travel preparation requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict


EVENT_RE = re.compile(
    r"\b((?:ICML|NeurIPS|ICLR|ACL|EMNLP|CVPR|AAAI|SIGGRAPH|KDD|CHI)"
    r"\s*(?:20\d{2})?)\b",
    re.IGNORECASE,
)
DESTINATION_RE = re.compile(
    r"\b(?:in|at|to|visiting)\s+([A-Za-z][A-Za-z .'-]{1,50}?)(?=$|[,.]|\s+(?:from|for|on|with)\b)",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")

LANGUAGE_BY_DESTINATION = {
    "seoul": ["en", "ko"],
    "south korea": ["en", "ko"],
    "korea": ["en", "ko"],
    "tokyo": ["en", "ja"],
    "japan": ["en", "ja"],
    "paris": ["en", "fr"],
    "france": ["en", "fr"],
    "madrid": ["en", "es"],
    "spain": ["en", "es"],
    "berlin": ["en", "de"],
    "germany": ["en", "de"],
    "beijing": ["en", "zh"],
    "china": ["en", "zh"],
}


@dataclass
class ParsedTrip:
    name: str
    destination: str
    event: str
    starts_at: str | None
    ends_at: str | None
    languages: list[str]
    traveler_needs: list[str]
    public_summary: str
    suggested_queries: list[str]
    coverage: list[str]
    blocking_question: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _title(value: str) -> str:
    return " ".join(word.capitalize() for word in value.strip().split())


def _needs(text: str) -> list[str]:
    checks = {
        "child-friendly options": r"\b(child|children|kid|kids|family)\b",
        "accessibility": r"\b(wheelchair|accessible|mobility|disabled)\b",
        "vegetarian food": r"\b(vegetarian|vegan)\b",
        "food allergy safety": r"\b(allerg|gluten|peanut|nut-free)\b",
        "business travel": r"\b(business|meeting|client)\b",
    }
    return [label for label, pattern in checks.items() if re.search(pattern, text, re.I)]


def parse(text: str) -> ParsedTrip:
    clean = " ".join(text.strip().split())
    event_match = EVENT_RE.search(clean)
    event = event_match.group(1).upper().replace("  ", " ") if event_match else ""
    destination_match = DESTINATION_RE.search(clean)
    destination = _title(destination_match.group(1)) if destination_match else ""
    if event and destination.lower().startswith(event.lower()):
        # "going to ICML 2026 in Seoul" should use the final "in Seoul".
        matches = list(DESTINATION_RE.finditer(clean))
        if len(matches) > 1:
            destination = _title(matches[-1].group(1))
        else:
            in_match = re.search(r"\bin\s+([A-Za-z][A-Za-z .'-]{1,50})$", clean, re.I)
            destination = _title(in_match.group(1)) if in_match else ""

    year_match = YEAR_RE.search(event or clean)
    year = year_match.group(1) if year_match else ""
    languages = LANGUAGE_BY_DESTINATION.get(destination.casefold(), ["en"])
    needs = _needs(clean)
    name = event or (f"{destination} trip" if destination else "New trip")
    if year and year not in name:
        name = f"{name} {year}"

    public_parts = [part for part in (event, destination, year) if part]
    public_summary = ", ".join(dict.fromkeys(public_parts))
    queries: list[str] = []
    if event:
        queries.extend(
            [
                f"{event} official dates venue schedule",
                f"{event} official attendee information",
            ]
        )
    if destination:
        queries.extend(
            [
                f"{destination} official airport public transit visitor information",
                f"{destination} official emergency numbers tourism",
            ]
        )
    coverage = ["itinerary", "arrival and transit", "safety"]
    if event:
        coverage.insert(0, "event schedule and venue")
    if len(languages) > 1:
        coverage.extend(["language", "food and etiquette"])
    blocking = None
    if not event and not destination:
        blocking = "Where are you going, or what event are you attending?"

    return ParsedTrip(
        name=name,
        destination=destination,
        event=event,
        starts_at=None,
        ends_at=None,
        languages=languages,
        traveler_needs=needs,
        public_summary=public_summary,
        suggested_queries=list(dict.fromkeys(queries)),
        coverage=list(dict.fromkeys(coverage)),
        blocking_question=blocking,
    )
