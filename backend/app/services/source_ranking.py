"""Official-first ranking for web sources discovered during preparation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Iterable
from urllib.parse import urlsplit

from .source_search import SearchHit


class QualityTier(IntEnum):
    OFFICIAL_PRIMARY = 1
    OFFICIAL_AUTHORITY = 2
    REPUTABLE_SECONDARY = 3
    DISCOVERY_ONLY = 4


class SourceRole(str, Enum):
    EVENT_OFFICIAL = "event_official"
    VENUE_OFFICIAL = "venue_official"
    AIRPORT_OFFICIAL = "airport_official"
    TRANSIT_AUTHORITY = "transit_authority"
    GOVERNMENT = "government"
    EMBASSY = "embassy"
    TOURISM_BOARD = "tourism_board"
    REPUTABLE_SECONDARY = "reputable_secondary"
    OTHER = "other"


class ConsequenceFlag(str, Enum):
    SAFETY = "safety"
    ENTRY_REQUIREMENTS = "entry_requirements"
    HEALTH = "health"
    LEGAL = "legal"
    EVENT_SCHEDULE = "event_schedule"
    TRANSPORT_STATUS = "transport_status"
    PRICE_OR_AVAILABILITY = "price_or_availability"


_PRIMARY_ROLES = {
    SourceRole.EVENT_OFFICIAL,
    SourceRole.VENUE_OFFICIAL,
}
_AUTHORITY_ROLES = {
    SourceRole.AIRPORT_OFFICIAL,
    SourceRole.TRANSIT_AUTHORITY,
    SourceRole.GOVERNMENT,
    SourceRole.EMBASSY,
    SourceRole.TOURISM_BOARD,
}
_ROLE_WEIGHT = {
    SourceRole.EVENT_OFFICIAL: 90,
    SourceRole.VENUE_OFFICIAL: 80,
    SourceRole.AIRPORT_OFFICIAL: 75,
    SourceRole.TRANSIT_AUTHORITY: 72,
    SourceRole.GOVERNMENT: 70,
    SourceRole.EMBASSY: 68,
    SourceRole.TOURISM_BOARD: 60,
    SourceRole.REPUTABLE_SECONDARY: 40,
    SourceRole.OTHER: 0,
}

_CONSEQUENCE_TERMS: tuple[
    tuple[ConsequenceFlag, tuple[str, ...]], ...
] = (
    (
        ConsequenceFlag.SAFETY,
        (
            "alert",
            "danger",
            "emergency",
            "evacuation",
            "safety",
            "security",
            "travel advisory",
            "warning",
        ),
    ),
    (
        ConsequenceFlag.ENTRY_REQUIREMENTS,
        (
            "border",
            "customs",
            "entry requirement",
            "immigration",
            "passport",
            "visa",
        ),
    ),
    (
        ConsequenceFlag.HEALTH,
        (
            "disease",
            "health",
            "medicine",
            "vaccination",
            "vaccine",
        ),
    ),
    (
        ConsequenceFlag.LEGAL,
        ("law", "legal", "prohibited", "regulation", "requirement"),
    ),
    (
        ConsequenceFlag.EVENT_SCHEDULE,
        (
            "agenda",
            "date",
            "event schedule",
            "opening time",
            "program",
            "schedule",
            "timetable",
        ),
    ),
    (
        ConsequenceFlag.TRANSPORT_STATUS,
        (
            "airport",
            "cancellation",
            "delay",
            "departure",
            "flight",
            "service status",
            "train",
            "transit",
        ),
    ),
    (
        ConsequenceFlag.PRICE_OR_AVAILABILITY,
        (
            "availability",
            "fee",
            "fare",
            "price",
            "sold out",
            "ticket",
        ),
    ),
)


@dataclass(frozen=True)
class RankingContext:
    """Known domains supplied by the event/trip configuration."""

    event_official_domains: frozenset[str] = frozenset()
    venue_domains: frozenset[str] = frozenset()
    airport_domains: frozenset[str] = frozenset()
    transit_authority_domains: frozenset[str] = frozenset()
    government_domains: frozenset[str] = frozenset()
    embassy_domains: frozenset[str] = frozenset()
    tourism_domains: frozenset[str] = frozenset()
    reputable_domains: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for field_name in (
            "event_official_domains",
            "venue_domains",
            "airport_domains",
            "transit_authority_domains",
            "government_domains",
            "embassy_domains",
            "tourism_domains",
            "reputable_domains",
        ):
            domains = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                frozenset(
                    normalized
                    for domain in domains
                    if (normalized := normalize_domain(domain))
                ),
            )


@dataclass(frozen=True)
class RankedSearchHit:
    hit: SearchHit
    role: SourceRole
    quality_tier: QualityTier
    consequence_flags: frozenset[ConsequenceFlag]
    score: float
    snippet_is_discovery_only: bool = True
    evidence_eligible: bool = False


def rank_search_hits(
    hits: Iterable[SearchHit],
    context: RankingContext | None = None,
    *,
    query: str = "",
) -> list[RankedSearchHit]:
    ranked = [
        classify_search_hit(hit, context or RankingContext(), query=query)
        for hit in hits
    ]
    ranked.sort(
        key=lambda item: (
            item.quality_tier,
            -item.score,
            item.hit.provider_rank,
            item.hit.url,
        )
    )
    return ranked


def classify_search_hit(
    hit: SearchHit,
    context: RankingContext | None = None,
    *,
    query: str = "",
) -> RankedSearchHit:
    ranking_context = context or RankingContext()
    role = classify_source_role(hit.url, hit.title, ranking_context)
    tier = quality_tier_for_role(role)
    text = " ".join((query, hit.title, hit.url))
    flags = detect_consequence_flags(text)
    scheme = urlsplit(hit.url).scheme.lower()
    score = (
        (5 - int(tier)) * 100
        + _ROLE_WEIGHT[role]
        + (5 if scheme == "https" else 0)
        - min(max(hit.provider_rank, 0), 50) * 0.25
    )
    return RankedSearchHit(
        hit=hit,
        role=role,
        quality_tier=tier,
        consequence_flags=flags,
        score=score,
    )


def classify_source_role(
    url: str,
    title: str,
    context: RankingContext,
) -> SourceRole:
    host = normalize_domain(url)
    title_lower = title.casefold()
    if domain_in_set(host, context.event_official_domains):
        return SourceRole.EVENT_OFFICIAL
    if domain_in_set(host, context.venue_domains):
        return SourceRole.VENUE_OFFICIAL
    if domain_in_set(host, context.airport_domains):
        return SourceRole.AIRPORT_OFFICIAL
    if domain_in_set(host, context.transit_authority_domains):
        return SourceRole.TRANSIT_AUTHORITY
    if domain_in_set(host, context.embassy_domains):
        return SourceRole.EMBASSY
    if domain_in_set(host, context.government_domains):
        if "embassy" in title_lower or "consulate" in title_lower:
            return SourceRole.EMBASSY
        return SourceRole.GOVERNMENT
    if domain_in_set(host, context.tourism_domains):
        return SourceRole.TOURISM_BOARD
    if _looks_like_government_domain(host):
        if "embassy" in title_lower or "consulate" in title_lower:
            return SourceRole.EMBASSY
        return SourceRole.GOVERNMENT
    if domain_in_set(host, context.reputable_domains):
        return SourceRole.REPUTABLE_SECONDARY
    return SourceRole.OTHER


def quality_tier_for_role(role: SourceRole) -> QualityTier:
    if role in _PRIMARY_ROLES:
        return QualityTier.OFFICIAL_PRIMARY
    if role in _AUTHORITY_ROLES:
        return QualityTier.OFFICIAL_AUTHORITY
    if role is SourceRole.REPUTABLE_SECONDARY:
        return QualityTier.REPUTABLE_SECONDARY
    return QualityTier.DISCOVERY_ONLY


def detect_consequence_flags(text: str) -> frozenset[ConsequenceFlag]:
    normalized = re.sub(r"[_/?.=&-]+", " ", text.casefold())
    return frozenset(
        flag
        for flag, terms in _CONSEQUENCE_TERMS
        if any(term in normalized for term in terms)
    )


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"//{raw}"
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname or ""
        return host.rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return ""


def domain_in_set(host: str, domains: Iterable[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def is_official_role(role: SourceRole) -> bool:
    return role in _PRIMARY_ROLES or role in _AUTHORITY_ROLES


def _looks_like_government_domain(host: str) -> bool:
    if not host:
        return False
    government_suffixes = (
        ".gov",
        ".gov.au",
        ".gov.br",
        ".gov.sg",
        ".gov.uk",
        ".go.jp",
        ".go.kr",
        ".gc.ca",
        ".gob.es",
        ".gouv.fr",
    )
    return host == "gov.uk" or host.endswith(government_suffixes)
