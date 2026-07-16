"""Freshness policies for cached source material and Ask-time decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

from .source_ranking import ConsequenceFlag, SourceRole


class FreshnessClass(str, Enum):
    STABLE = "stable"
    SEMI_STATIC = "semi_static"
    EVENT_CURRENT = "event_current"
    VOLATILE = "volatile"


class FreshnessState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


@dataclass(frozen=True)
class FreshnessPolicy:
    minimum_ttl: timedelta
    default_ttl: timedelta
    maximum_ttl: timedelta

    def __post_init__(self) -> None:
        if not (
            timedelta(0)
            < self.minimum_ttl
            <= self.default_ttl
            <= self.maximum_ttl
        ):
            raise ValueError("freshness TTLs must be positive and ordered")


FRESHNESS_POLICIES = {
    FreshnessClass.STABLE: FreshnessPolicy(
        minimum_ttl=timedelta(days=180),
        default_ttl=timedelta(days=270),
        maximum_ttl=timedelta(days=365),
    ),
    FreshnessClass.SEMI_STATIC: FreshnessPolicy(
        minimum_ttl=timedelta(days=30),
        default_ttl=timedelta(days=60),
        maximum_ttl=timedelta(days=90),
    ),
    FreshnessClass.EVENT_CURRENT: FreshnessPolicy(
        minimum_ttl=timedelta(days=1),
        default_ttl=timedelta(days=3),
        maximum_ttl=timedelta(days=7),
    ),
    FreshnessClass.VOLATILE: FreshnessPolicy(
        minimum_ttl=timedelta(minutes=5),
        default_ttl=timedelta(minutes=30),
        maximum_ttl=timedelta(hours=6),
    ),
}


@dataclass(frozen=True)
class FreshnessAssessment:
    freshness_class: FreshnessClass
    state: FreshnessState
    observed_at: datetime
    checked_at: datetime
    age: timedelta
    fresh_until: datetime
    expires_at: datetime


@dataclass(frozen=True)
class AskFreshnessDecision:
    allow_offline: bool
    require_online_refresh: bool
    include_as_of: bool
    as_of: datetime
    reason: str


def classify_freshness_class(
    *,
    title: str = "",
    url: str = "",
    source_role: SourceRole = SourceRole.OTHER,
    consequence_flags: Iterable[ConsequenceFlag | str] = (),
) -> FreshnessClass:
    """Classify a source using its topic, role, and consequence profile."""

    flags = {_flag_value(flag) for flag in consequence_flags}
    text = re.sub(r"[_/?.=&-]+", " ", f"{title} {url}".casefold())
    volatile_terms = (
        "arrival",
        "availability",
        "cancellation",
        "delay",
        "departure",
        "exchange rate",
        "flight status",
        "live",
        "price",
        "service status",
        "status update",
        "traffic",
        "weather",
    )
    if (
        any(term in text for term in volatile_terms)
        or ConsequenceFlag.PRICE_OR_AVAILABILITY.value in flags
    ):
        return FreshnessClass.VOLATILE

    event_terms = (
        "agenda",
        "event",
        "opening time",
        "program",
        "schedule",
        "timetable",
    )
    if (
        source_role is SourceRole.EVENT_OFFICIAL
        or any(term in text for term in event_terms)
        or ConsequenceFlag.EVENT_SCHEDULE.value in flags
    ):
        return FreshnessClass.EVENT_CURRENT

    semi_static_terms = (
        "admission",
        "contact",
        "entry requirement",
        "fare",
        "fee",
        "hours",
        "policy",
        "route",
        "visa",
    )
    if (
        source_role
        in {
            SourceRole.AIRPORT_OFFICIAL,
            SourceRole.TRANSIT_AUTHORITY,
            SourceRole.GOVERNMENT,
            SourceRole.EMBASSY,
            SourceRole.TOURISM_BOARD,
        }
        or any(term in text for term in semi_static_terms)
        or flags
        & {
            ConsequenceFlag.ENTRY_REQUIREMENTS.value,
            ConsequenceFlag.HEALTH.value,
            ConsequenceFlag.LEGAL.value,
            ConsequenceFlag.SAFETY.value,
        }
    ):
        return FreshnessClass.SEMI_STATIC
    return FreshnessClass.STABLE


def assess_freshness(
    observed_at: datetime | str,
    freshness_class: FreshnessClass,
    *,
    now: datetime | str | None = None,
    fresh_for: timedelta | None = None,
    expires_after: timedelta | None = None,
) -> FreshnessAssessment:
    """Return fresh/stale/expired using a soft and a hard TTL."""

    observed = parse_timestamp(observed_at)
    checked = parse_timestamp(now) if now is not None else datetime.now(timezone.utc)
    policy = FRESHNESS_POLICIES[freshness_class]
    soft_ttl = fresh_for or policy.default_ttl
    hard_ttl = expires_after or policy.maximum_ttl
    if soft_ttl <= timedelta(0) or hard_ttl < soft_ttl:
        raise ValueError("fresh TTL must be positive and no longer than expiry")

    age = max(timedelta(0), checked - observed)
    if age <= soft_ttl:
        state = FreshnessState.FRESH
    elif age <= hard_ttl:
        state = FreshnessState.STALE
    else:
        state = FreshnessState.EXPIRED
    return FreshnessAssessment(
        freshness_class=freshness_class,
        state=state,
        observed_at=observed,
        checked_at=checked,
        age=age,
        fresh_until=observed + soft_ttl,
        expires_at=observed + hard_ttl,
    )


def ask_freshness_decision(
    assessment: FreshnessAssessment,
    *,
    consequence_flags: Iterable[ConsequenceFlag | str] = (),
    high_consequence: bool | None = None,
) -> AskFreshnessDecision:
    """Decide whether Ask may use a cached source or must refresh online."""

    flags = {_flag_value(flag) for flag in consequence_flags}
    consequential = bool(flags) if high_consequence is None else high_consequence
    non_stable = assessment.freshness_class is not FreshnessClass.STABLE

    if assessment.state is FreshnessState.FRESH:
        return AskFreshnessDecision(
            allow_offline=True,
            require_online_refresh=False,
            include_as_of=consequential or non_stable,
            as_of=assessment.observed_at,
            reason="Source is within its freshness TTL.",
        )

    if (
        assessment.state is FreshnessState.STALE
        and not consequential
        and assessment.freshness_class is not FreshnessClass.VOLATILE
    ):
        return AskFreshnessDecision(
            allow_offline=True,
            require_online_refresh=False,
            include_as_of=True,
            as_of=assessment.observed_at,
            reason="Source is stale but may be used with a visible As-of date.",
        )

    reason = (
        "Consequential or volatile information is stale and requires refresh."
        if assessment.state is FreshnessState.STALE
        else "Source is beyond its maximum TTL and requires refresh."
    )
    return AskFreshnessDecision(
        allow_offline=False,
        require_online_refresh=True,
        include_as_of=True,
        as_of=assessment.observed_at,
        reason=reason,
    )


def is_fresh(assessment: FreshnessAssessment) -> bool:
    return assessment.state is FreshnessState.FRESH


def is_stale(assessment: FreshnessAssessment) -> bool:
    return assessment.state is FreshnessState.STALE


def is_expired(assessment: FreshnessAssessment) -> bool:
    return assessment.state is FreshnessState.EXPIRED


def parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _flag_value(flag: ConsequenceFlag | str) -> str:
    return flag.value if isinstance(flag, ConsequenceFlag) else str(flag)
