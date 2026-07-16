from __future__ import annotations

from datetime import datetime, timezone

from app.services.safe_source_fetcher import (
    FetchErrorCode,
    FetchedPage,
    SafeFetchError,
)
from app.services.source_acquisition import (
    AcquisitionConfig,
    AcquisitionGapCode,
    SourceAcquisitionOrchestrator,
)
from app.services.source_queries import (
    PublicTripFields,
    generate_public_trip_queries,
)
from app.services.source_ranking import (
    QualityTier,
    RankingContext,
    SourceRole,
)
from app.services.source_search import (
    FakeSearchProvider,
    NullSearchProvider,
    SearchErrorCode,
    SearchHit,
    SearchIssue,
)


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


class FakePageFetcher:
    def __init__(self, pages: dict[str, FetchedPage]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        return self.pages[url]


def page(
    url: str,
    title: str,
    text: str,
    *,
    publisher: str,
    cache_allowed: bool = True,
) -> FetchedPage:
    return FetchedPage(
        url=url,
        title=title,
        publisher=publisher,
        text=text,
        content_type="text/html",
        retrieved_at=NOW,
        source_updated_at=None,
        license=None,
        bytes_read=len(text.encode()),
        cache_allowed=cache_allowed,
        cache_restriction=(
            None if cache_allowed else "Map/place content must not be cached."
        ),
    )


def test_orchestrator_dedupes_ranks_and_uses_only_original_page_text():
    trip = PublicTripFields(
        event="DataConf",
        destination="Berlin",
        start_date="2026-09-10",
        end_date="2026-09-12",
        public_needs=("event schedule",),
    )
    queries = generate_public_trip_queries(trip).queries
    official = SearchHit(
        url="https://data.example/schedule",
        title="Official DataConf schedule",
        snippet="False snippet claim at 8 AM",
        provider_rank=9,
    )
    secondary = SearchHit(
        url="https://news.example/guide?utm_source=search",
        title="Independent guide",
        snippet="Discovery-only restaurant claim",
        provider_rank=0,
    )
    duplicate = SearchHit(
        url="https://news.example/guide#section",
        title="Same independent guide",
        provider_rank=2,
    )
    provider = FakeSearchProvider(
        {
            queries[0]: [secondary, official],
            queries[1]: [duplicate],
        }
    )
    fetcher = FakePageFetcher(
        {
            official.url: page(
                official.url,
                "Official DataConf schedule",
                "Verified original page: keynote at 10 AM.",
                publisher="DataConf",
            ),
            secondary.url: page(
                "https://news.example/guide",
                "Independent guide",
                "Original secondary article.",
                publisher="Example News",
            ),
        }
    )
    orchestrator = SourceAcquisitionOrchestrator(
        provider,
        fetcher,
        config=AcquisitionConfig(max_fetches=10),
    )
    context = RankingContext(
        event_official_domains=frozenset({"data.example"}),
        reputable_domains=frozenset({"news.example"}),
    )

    result = orchestrator.acquire(trip, ranking_context=context)

    assert len(result.candidates) == 2
    assert result.candidates[0].source_role is SourceRole.EVENT_OFFICIAL
    assert (
        result.candidates[0].quality_tier
        is QualityTier.OFFICIAL_PRIMARY
    )
    assert result.candidates[0].text == (
        "Verified original page: keynote at 10 AM."
    )
    assert "False snippet" not in result.candidates[0].text
    assert result.candidates[0].search_snippet_used_as_evidence is False
    assert result.candidates[0].evidence_origin == "original_page"
    assert result.stats.duplicate_hits_removed == 1
    assert result.stats.fetches_succeeded == 2
    assert fetcher.calls[0] == official.url


def test_no_key_returns_gap_and_performs_no_fetch():
    provider = NullSearchProvider()
    fetcher = FakePageFetcher({})
    orchestrator = SourceAcquisitionOrchestrator(provider, fetcher)

    result = orchestrator.acquire(
        PublicTripFields(destination="Osaka")
    )

    assert result.candidates == ()
    assert result.gaps[0].code is AcquisitionGapCode.SEARCH_NOT_CONFIGURED
    assert result.stats.searches_attempted == 1
    assert result.stats.fetches_attempted == 0
    assert len(provider.calls) == 1
    assert fetcher.calls == []


def test_provider_outage_is_a_gap_and_stops_repeated_requests():
    provider = FakeSearchProvider(
        issue=SearchIssue(
            SearchErrorCode.PROVIDER_OUTAGE,
            "Provider unavailable.",
            retryable=True,
        )
    )
    fetcher = FakePageFetcher({})
    orchestrator = SourceAcquisitionOrchestrator(provider, fetcher)

    result = orchestrator.acquire(
        PublicTripFields(event="Public Expo", destination="Rome")
    )

    assert result.candidates == ()
    assert result.gaps[0].code is AcquisitionGapCode.SEARCH_UNAVAILABLE
    assert len(provider.calls) == 1
    assert fetcher.calls == []


def test_private_input_is_rejected_before_provider_boundary():
    provider = FakeSearchProvider()
    fetcher = FakePageFetcher({})
    orchestrator = SourceAcquisitionOrchestrator(provider, fetcher)

    result = orchestrator.acquire(
        {
            "event": "Public Expo",
            "destination": "Rome",
            "traveler_name": "Private Person",
            "attachment_text": "private itinerary",
        }
    )

    assert result.gaps[0].code is AcquisitionGapCode.PRIVATE_INPUT_REJECTED
    assert provider.calls == []
    assert fetcher.calls == []
    assert "Private Person" not in result.gaps[0].message


def test_fetch_block_is_reported_without_promoting_search_snippet():
    trip = PublicTripFields(destination="Paris")
    query = generate_public_trip_queries(trip).queries[0]
    hit = SearchHit(
        url="https://blocked.example/page",
        title="Blocked page",
        snippet="Unverified claim must not become a source.",
    )
    provider = FakeSearchProvider({query: [hit]})

    class BlockingFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, url: str) -> FetchedPage:
            self.calls.append(url)
            raise SafeFetchError(
                FetchErrorCode.UNSAFE_ADDRESS, "private address"
            )

    fetcher = BlockingFetcher()
    orchestrator = SourceAcquisitionOrchestrator(provider, fetcher)

    result = orchestrator.acquire(trip)

    assert result.candidates == ()
    assert result.gaps[0].code is AcquisitionGapCode.FETCH_BLOCKED
    assert "Unverified claim" not in result.gaps[0].message
    assert result.stats.fetches_blocked_or_failed == 1


def test_non_cacheable_place_candidate_never_exposes_cacheable_text():
    trip = PublicTripFields(destination="Madrid")
    query = generate_public_trip_queries(trip).queries[0]
    hit = SearchHit(
        url="https://maps.google.com/place/example",
        title="Place details",
    )
    provider = FakeSearchProvider({query: [hit]})
    fetcher = FakePageFetcher(
        {
            hit.url: page(
                hit.url,
                "Place details",
                "Transient place content",
                publisher="Maps",
                cache_allowed=False,
            )
        }
    )

    result = SourceAcquisitionOrchestrator(provider, fetcher).acquire(trip)

    assert len(result.candidates) == 1
    assert result.candidates[0].cache_allowed is False
    assert result.candidates[0].cacheable_text() is None
