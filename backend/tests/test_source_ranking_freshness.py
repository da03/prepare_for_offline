from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.source_freshness import (
    FRESHNESS_POLICIES,
    FreshnessClass,
    FreshnessState,
    ask_freshness_decision,
    assess_freshness,
    classify_freshness_class,
    is_expired,
    is_fresh,
    is_stale,
)
from app.services.source_ranking import (
    ConsequenceFlag,
    QualityTier,
    RankingContext,
    SourceRole,
    classify_search_hit,
    classify_source_role,
    rank_search_hits,
)
from app.services.source_search import SearchHit


def test_official_sources_outrank_provider_first_secondary_result():
    context = RankingContext(
        event_official_domains=frozenset({"event.example"}),
        venue_domains=frozenset({"venue.example"}),
        reputable_domains=frozenset({"newspaper.example"}),
    )
    hits = [
        SearchHit(
            url="https://newspaper.example/story",
            title="Event preview",
            provider_rank=0,
        ),
        SearchHit(
            url="https://venue.example/visit",
            title="Venue visitor information",
            provider_rank=2,
        ),
        SearchHit(
            url="https://event.example/schedule",
            title="Official schedule",
            snippet="The keynote is at 9; this must remain discovery-only.",
            provider_rank=10,
        ),
    ]

    ranked = rank_search_hits(hits, context, query="event schedule")

    assert [item.role for item in ranked] == [
        SourceRole.EVENT_OFFICIAL,
        SourceRole.VENUE_OFFICIAL,
        SourceRole.REPUTABLE_SECONDARY,
    ]
    assert ranked[0].quality_tier is QualityTier.OFFICIAL_PRIMARY
    assert ranked[0].snippet_is_discovery_only is True
    assert ranked[0].evidence_eligible is False


@pytest.mark.parametrize(
    ("url", "title", "expected"),
    [
        ("https://airport.example", "Airport", SourceRole.AIRPORT_OFFICIAL),
        ("https://transit.example", "Metro", SourceRole.TRANSIT_AUTHORITY),
        ("https://state.example", "Ministry", SourceRole.GOVERNMENT),
        ("https://embassy.example", "Embassy", SourceRole.EMBASSY),
        ("https://visit.example", "Tourism", SourceRole.TOURISM_BOARD),
        ("https://service.gov.uk", "Public service", SourceRole.GOVERNMENT),
    ],
)
def test_official_authority_roles(url, title, expected):
    context = RankingContext(
        airport_domains=frozenset({"airport.example"}),
        transit_authority_domains=frozenset({"transit.example"}),
        government_domains=frozenset({"state.example"}),
        embassy_domains=frozenset({"embassy.example"}),
        tourism_domains=frozenset({"visit.example"}),
    )

    assert classify_source_role(url, title, context) is expected


def test_domain_matching_does_not_trust_suffix_confusion():
    context = RankingContext(
        event_official_domains=frozenset({"event.example"})
    )
    hit = SearchHit(
        url="https://event.example.attacker.test/schedule",
        title="Fake official schedule",
    )

    ranked = classify_search_hit(hit, context)

    assert ranked.role is SourceRole.OTHER
    assert ranked.quality_tier is QualityTier.DISCOVERY_ONLY


def test_consequence_flags_are_carried_into_ranking():
    hit = SearchHit(
        url="https://travel.gov/visa-alert",
        title="Visa entry requirement and safety warning",
    )

    ranked = classify_search_hit(hit, RankingContext())

    assert ConsequenceFlag.ENTRY_REQUIREMENTS in ranked.consequence_flags
    assert ConsequenceFlag.SAFETY in ranked.consequence_flags
    assert ConsequenceFlag.LEGAL in ranked.consequence_flags


def test_ttl_ranges_match_source_volatility():
    assert FRESHNESS_POLICIES[FreshnessClass.STABLE].minimum_ttl == timedelta(
        days=180
    )
    assert FRESHNESS_POLICIES[FreshnessClass.STABLE].maximum_ttl == timedelta(
        days=365
    )
    assert FRESHNESS_POLICIES[
        FreshnessClass.SEMI_STATIC
    ].minimum_ttl == timedelta(days=30)
    assert FRESHNESS_POLICIES[
        FreshnessClass.SEMI_STATIC
    ].maximum_ttl == timedelta(days=90)
    assert FRESHNESS_POLICIES[
        FreshnessClass.EVENT_CURRENT
    ].minimum_ttl == timedelta(days=1)
    assert FRESHNESS_POLICIES[
        FreshnessClass.EVENT_CURRENT
    ].maximum_ttl == timedelta(days=7)
    assert FRESHNESS_POLICIES[
        FreshnessClass.VOLATILE
    ].minimum_ttl == timedelta(minutes=5)
    assert FRESHNESS_POLICIES[
        FreshnessClass.VOLATILE
    ].maximum_ttl == timedelta(hours=6)


@pytest.mark.parametrize("freshness_class", list(FreshnessClass))
def test_fresh_stale_expired_boundaries(freshness_class):
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    policy = FRESHNESS_POLICIES[freshness_class]

    fresh = assess_freshness(
        observed,
        freshness_class,
        now=observed + policy.default_ttl,
    )
    stale = assess_freshness(
        observed,
        freshness_class,
        now=observed + policy.default_ttl + timedelta(seconds=1),
    )
    expired = assess_freshness(
        observed,
        freshness_class,
        now=observed + policy.maximum_ttl + timedelta(seconds=1),
    )

    assert fresh.state is FreshnessState.FRESH
    assert is_fresh(fresh)
    assert stale.state is FreshnessState.STALE
    assert is_stale(stale)
    assert expired.state is FreshnessState.EXPIRED
    assert is_expired(expired)


def test_ask_allows_low_consequence_stale_source_with_as_of():
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assessment = assess_freshness(
        observed,
        FreshnessClass.STABLE,
        now=observed + timedelta(days=300),
    )

    decision = ask_freshness_decision(assessment)

    assert decision.allow_offline is True
    assert decision.require_online_refresh is False
    assert decision.include_as_of is True
    assert decision.as_of == observed


def test_ask_requires_refresh_for_stale_consequential_or_volatile_facts():
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stale_stable = assess_freshness(
        observed,
        FreshnessClass.STABLE,
        now=observed + timedelta(days=300),
    )
    stale_volatile = assess_freshness(
        observed,
        FreshnessClass.VOLATILE,
        now=observed + timedelta(hours=1),
    )

    consequential = ask_freshness_decision(
        stale_stable,
        consequence_flags=(ConsequenceFlag.ENTRY_REQUIREMENTS,),
    )
    volatile = ask_freshness_decision(stale_volatile)

    assert consequential.allow_offline is False
    assert consequential.require_online_refresh is True
    assert volatile.allow_offline is False
    assert volatile.require_online_refresh is True


def test_freshness_classifier_distinguishes_schedule_and_live_status():
    event = classify_freshness_class(
        title="Conference schedule",
        source_role=SourceRole.EVENT_OFFICIAL,
    )
    live = classify_freshness_class(
        title="Live train service status",
        source_role=SourceRole.TRANSIT_AUTHORITY,
    )
    stable = classify_freshness_class(title="History of the city")

    assert event is FreshnessClass.EVENT_CURRENT
    assert live is FreshnessClass.VOLATILE
    assert stable is FreshnessClass.STABLE
