"""Privacy-safe query generation from explicitly public trip metadata."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping


MAX_QUERY_CHARS = 256
MAX_PUBLIC_NEEDS = 8

_FIELD_ALIASES = {
    "event": "event",
    "event_name": "event",
    "public_event": "event",
    "destination": "destination",
    "public_destination": "destination",
    "start_date": "start_date",
    "starts_at": "start_date",
    "end_date": "end_date",
    "ends_at": "end_date",
    "public_needs": "public_needs",
}

_PRIVATE_KEY_PARTS = {
    "attachment",
    "body",
    "booking",
    "confirmation",
    "contact",
    "content",
    "document",
    "email",
    "full_name",
    "guest",
    "itinerary",
    "name",
    "note",
    "passenger",
    "passport",
    "phone",
    "pnr",
    "reservation",
    "source_content",
    "traveler",
}

_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(
            r"(?<![\w.+-])[\w.+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,}(?![\w.-])",
            re.IGNORECASE,
        ),
    ),
    (
        "reservation_code",
        re.compile(
            r"\b(?:(?:booking|reservation|confirmation|ticket)\s+"
            r"(?:code|number|no\.?|#)|record\s+locator|pnr)"
            r"\s*[:=#-]?\s*[A-Z0-9][A-Z0-9-]{4,19}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "reservation_reference",
        re.compile(
            r"\b(?i:booking|reservation|confirmation)\s+"
            r"(?=[A-Z0-9-]{5,20}\b)(?=[A-Z0-9-]*\d|[A-Z]{6}\b)"
            r"[A-Z0-9-]{5,20}\b"
        ),
    ),
    (
        "passport_number",
        re.compile(
            r"\bpassport\s*(?:number|no\.?|#)?\s*[:=#-]?\s*"
            r"[A-Z0-9]{5,20}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "phone_number",
        re.compile(
            r"\b(?:phone|telephone|tel)\s*[:=#-]?\s*"
            r"(?:\+?\d[\d ().-]{6,}\d)",
            re.IGNORECASE,
        ),
    ),
    (
        "international_phone_number",
        re.compile(r"(?<!\w)\+\d[\d ().-]{6,}\d(?!\w)"),
    ),
    (
        "payment_card",
        re.compile(
            r"\b(?:card|credit\s+card)\s*(?:number|no\.?|#)?\s*[:=#-]?"
            r"\s*(?:\d[ -]?){13,19}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "credential",
        re.compile(
            r"\b(?:api[_ -]?key|access[_ -]?token|password|secret)"
            r"\s*[:=]\s*[^\s,;]{6,}",
            re.IGNORECASE,
        ),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
            r"\.[A-Za-z0-9_-]{8,}\b"
        ),
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
    ),
)


class PrivateQueryDataError(ValueError):
    """Raised before search when private input fields are supplied."""

    def __init__(self, field_names: Iterable[str]) -> None:
        names = tuple(sorted(set(field_names)))
        self.field_names = names
        super().__init__(
            "private fields cannot be used for web search: " + ", ".join(names)
        )


@dataclass(frozen=True)
class PublicTripFields:
    """The complete allowlist of data permitted to leave the device."""

    event: str = ""
    destination: str = ""
    start_date: str | date | datetime | None = None
    end_date: str | date | datetime | None = None
    public_needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        needs = (
            (self.public_needs,)
            if isinstance(self.public_needs, str)
            else self.public_needs
        )
        object.__setattr__(self, "event", str(self.event or ""))
        object.__setattr__(self, "destination", str(self.destination or ""))
        object.__setattr__(
            self,
            "public_needs",
            tuple(str(item) for item in needs[:MAX_PUBLIC_NEEDS]),
        )


@dataclass(frozen=True)
class QueryPlan:
    queries: tuple[str, ...]
    redaction_categories: tuple[str, ...] = ()
    ignored_private_fields: tuple[str, ...] = ()


def public_trip_fields_from_mapping(
    values: Mapping[str, object],
    *,
    reject_private_fields: bool = True,
) -> tuple[PublicTripFields, tuple[str, ...]]:
    """Copy only allowlisted public fields from a broader context mapping.

    Known private fields are rejected by default.  In filter mode their values
    are ignored and only field names are reported; private values are never
    retained in the result or an exception.
    """

    copied: dict[str, object] = {}
    private_fields: list[str] = []
    for raw_key, value in values.items():
        key = _normalize_key(str(raw_key))
        canonical = _FIELD_ALIASES.get(key)
        if canonical is not None:
            copied[canonical] = value
            continue
        if _has_value(value) and _is_private_key(key):
            private_fields.append(key)

    if private_fields and reject_private_fields:
        raise PrivateQueryDataError(private_fields)

    needs = copied.get("public_needs", ())
    if isinstance(needs, str):
        needs = (needs,)
    elif not isinstance(needs, (list, tuple)):
        needs = ()

    fields = PublicTripFields(
        event=str(copied.get("event", "") or ""),
        destination=str(copied.get("destination", "") or ""),
        start_date=_date_value(copied.get("start_date")),
        end_date=_date_value(copied.get("end_date")),
        public_needs=tuple(str(item) for item in needs),
    )
    return fields, tuple(sorted(set(private_fields)))


def generate_public_trip_queries(
    source: PublicTripFields | Mapping[str, object],
    *,
    private_tokens: Iterable[str] = (),
    reject_private_fields: bool = True,
    max_queries: int = 8,
) -> QueryPlan:
    """Generate bounded queries without allowing private context to leak."""

    if max_queries < 1:
        raise ValueError("max_queries must be positive")

    ignored: tuple[str, ...] = ()
    if isinstance(source, PublicTripFields):
        fields = source
    else:
        fields, ignored = public_trip_fields_from_mapping(
            source,
            reject_private_fields=reject_private_fields,
        )

    patterns = _private_token_patterns(private_tokens)
    redactions: set[str] = set()
    event = _sanitize_public_text(fields.event, patterns, redactions)
    destination = _sanitize_public_text(
        fields.destination, patterns, redactions
    )
    start_date = _sanitize_public_text(
        _date_value(fields.start_date), patterns, redactions
    )
    end_date = _sanitize_public_text(
        _date_value(fields.end_date), patterns, redactions
    )
    needs = [
        cleaned
        for item in fields.public_needs[:MAX_PUBLIC_NEEDS]
        if (
            cleaned := _sanitize_public_text(item, patterns, redactions)
        )
    ]

    if not any((event, destination, needs)):
        raise ValueError("at least one public trip field is required")

    dates = " ".join(part for part in (start_date, end_date) if part)
    anchor = " ".join(part for part in (event, destination) if part)
    candidates: list[str] = []
    if event:
        candidates.append(
            _bounded_query(
                event,
                destination,
                dates,
                "official event information",
            )
        )
        candidates.append(
            _bounded_query(event, destination, "official website")
        )
    if destination:
        candidates.append(
            _bounded_query(
                destination,
                dates,
                "official government tourism travel information",
            )
        )
    for need in needs:
        candidates.append(
            _bounded_query(anchor, need, dates, "official information")
        )

    queries = tuple(dict.fromkeys(q for q in candidates if q))[:max_queries]
    return QueryPlan(
        queries=queries,
        redaction_categories=tuple(sorted(redactions)),
        ignored_private_fields=ignored,
    )


def contains_private_token(
    text: str,
    *,
    private_tokens: Iterable[str] = (),
) -> bool:
    """Conservative check useful at a provider boundary."""

    normalized = unicodedata.normalize("NFKC", text)
    if any(pattern.search(normalized) for _, pattern in _TOKEN_PATTERNS):
        return True
    return any(
        pattern.search(normalized)
        for _, pattern in _private_token_patterns(private_tokens)
    )


def _sanitize_public_text(
    value: object,
    private_patterns: tuple[tuple[str, re.Pattern[str]], ...],
    redactions: set[str],
) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        character
        for character in text
        if character in "\t\n " or unicodedata.category(character) != "Cc"
    )
    for category, pattern in (*_TOKEN_PATTERNS, *private_patterns):
        text, count = pattern.subn(" ", text)
        if count:
            redactions.add(category)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,;|")
    return text[:500].strip()


def _private_token_patterns(
    private_tokens: Iterable[str],
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for token in private_tokens:
        normalized = unicodedata.normalize("NFKC", str(token)).strip()
        if len(normalized) < 2:
            continue
        patterns.append(
            (
                "caller_private_token",
                re.compile(re.escape(normalized), re.IGNORECASE),
            )
        )
    return tuple(patterns)


def _bounded_query(*parts: str) -> str:
    query = re.sub(r"\s+", " ", " ".join(part for part in parts if part))
    query = query.strip()
    if len(query) <= MAX_QUERY_CHARS:
        return query
    suffix = " official"
    room = MAX_QUERY_CHARS - len(suffix)
    prefix = query[:room].rsplit(" ", 1)[0].strip()
    return (prefix + suffix).strip()


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _is_private_key(key: str) -> bool:
    if key in _PRIVATE_KEY_PARTS:
        return True
    parts = set(key.split("_"))
    return bool(parts & _PRIVATE_KEY_PARTS)


def _has_value(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _date_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()[:40]
