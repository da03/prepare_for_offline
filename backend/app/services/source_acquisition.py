"""Search, rank, and safely fetch original sources for Prepare enrichment."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .safe_source_fetcher import (
    FetchErrorCode,
    FetchedPage,
    SafeFetchError,
    SafeOriginalPageFetcher,
)
from .source_freshness import (
    FreshnessAssessment,
    FreshnessClass,
    assess_freshness,
    classify_freshness_class,
)
from .source_queries import (
    PrivateQueryDataError,
    PublicTripFields,
    generate_public_trip_queries,
)
from .source_ranking import (
    ConsequenceFlag,
    QualityTier,
    RankedSearchHit,
    RankingContext,
    SourceRole,
    classify_search_hit,
)
from .source_search import (
    SearchErrorCode,
    SearchFreshness,
    SearchHit,
    SearchProvider,
    SearchRequest,
    default_search_provider,
)


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage:
        ...


class AcquisitionGapCode(str, Enum):
    PRIVATE_INPUT_REJECTED = "private_input_rejected"
    NO_PUBLIC_QUERY = "no_public_query"
    SEARCH_NOT_CONFIGURED = "search_not_configured"
    SEARCH_UNAVAILABLE = "search_unavailable"
    NO_RESULTS = "no_results"
    INVALID_RESULT_URL = "invalid_result_url"
    FETCH_BLOCKED = "fetch_blocked"
    FETCH_FAILED = "fetch_failed"
    EMPTY_PAGE = "empty_page"


@dataclass(frozen=True)
class AcquisitionGap:
    code: AcquisitionGapCode
    message: str
    query: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class AcquisitionConfig:
    max_queries: int = 8
    results_per_query: int = 8
    max_fetches: int = 12
    search_freshness: SearchFreshness = SearchFreshness.ANY
    country: str | None = None
    search_language: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.max_queries <= 20:
            raise ValueError("max_queries must be between 1 and 20")
        if not 1 <= self.results_per_query <= 20:
            raise ValueError("results_per_query must be between 1 and 20")
        if not 1 <= self.max_fetches <= 50:
            raise ValueError("max_fetches must be between 1 and 50")


@dataclass(frozen=True)
class NormalizedSourceCandidate:
    source_id: str
    url: str
    title: str
    publisher: str
    text: str
    content_type: str
    retrieved_at: datetime
    source_updated_at: datetime | None
    license: str | None
    bytes_read: int
    quality_tier: QualityTier
    source_role: SourceRole
    consequence_flags: frozenset[ConsequenceFlag]
    rank_score: float
    freshness_class: FreshnessClass
    freshness: FreshnessAssessment
    cache_allowed: bool
    cache_restriction: str | None
    evidence_origin: str = "original_page"
    search_snippet_used_as_evidence: bool = False

    def cacheable_text(self) -> str | None:
        return self.text if self.cache_allowed else None


@dataclass(frozen=True)
class AcquisitionStats:
    provider: str
    queries_generated: int = 0
    privacy_redactions: int = 0
    searches_attempted: int = 0
    searches_succeeded: int = 0
    hits_seen: int = 0
    duplicate_hits_removed: int = 0
    fetches_attempted: int = 0
    fetches_succeeded: int = 0
    fetches_blocked_or_failed: int = 0


@dataclass(frozen=True)
class AcquisitionResult:
    candidates: tuple[NormalizedSourceCandidate, ...]
    gaps: tuple[AcquisitionGap, ...]
    stats: AcquisitionStats
    queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RankedDiscovery:
    canonical_url: str
    ranked: RankedSearchHit
    query: str


class SourceAcquisitionOrchestrator:
    """Deterministic orchestration with injectable provider and fetcher."""

    def __init__(
        self,
        provider: SearchProvider | None = None,
        fetcher: PageFetcher | None = None,
        *,
        config: AcquisitionConfig | None = None,
    ) -> None:
        self.provider = provider or default_search_provider()
        self.fetcher = fetcher or SafeOriginalPageFetcher()
        self.config = config or AcquisitionConfig()

    def acquire(
        self,
        trip: PublicTripFields | Mapping[str, object],
        *,
        ranking_context: RankingContext | None = None,
        private_tokens: Iterable[str] = (),
        reject_private_fields: bool = True,
    ) -> AcquisitionResult:
        context = ranking_context or RankingContext()
        try:
            query_plan = generate_public_trip_queries(
                trip,
                private_tokens=private_tokens,
                reject_private_fields=reject_private_fields,
                max_queries=self.config.max_queries,
            )
        except PrivateQueryDataError as exc:
            fields = ", ".join(exc.field_names)
            return self._empty_result(
                AcquisitionGap(
                    AcquisitionGapCode.PRIVATE_INPUT_REJECTED,
                    f"Private fields were rejected before search: {fields}.",
                )
            )
        except ValueError:
            return self._empty_result(
                AcquisitionGap(
                    AcquisitionGapCode.NO_PUBLIC_QUERY,
                    "No safe public trip fields were available for search.",
                )
            )

        gaps: list[AcquisitionGap] = []
        discoveries: dict[str, _RankedDiscovery] = {}
        searches_attempted = 0
        searches_succeeded = 0
        hits_seen = 0
        valid_hits = 0

        for query in query_plan.queries:
            searches_attempted += 1
            request = SearchRequest(
                query=query,
                count=self.config.results_per_query,
                freshness=self.config.search_freshness,
                country=self.config.country,
                search_language=self.config.search_language,
            )
            try:
                response = self.provider.search(request)
            except Exception:
                gaps.append(
                    AcquisitionGap(
                        AcquisitionGapCode.SEARCH_UNAVAILABLE,
                        "Search provider failed before returning results.",
                        query=query,
                    )
                )
                break

            if response.issue is not None:
                gap_code = (
                    AcquisitionGapCode.SEARCH_NOT_CONFIGURED
                    if response.issue.code is SearchErrorCode.NO_API_KEY
                    else AcquisitionGapCode.SEARCH_UNAVAILABLE
                )
                gaps.append(
                    AcquisitionGap(
                        gap_code,
                        response.issue.message,
                        query=query,
                    )
                )
                if response.issue.code in {
                    SearchErrorCode.NO_API_KEY,
                    SearchErrorCode.PROVIDER_OUTAGE,
                    SearchErrorCode.RATE_LIMITED,
                    SearchErrorCode.TIMEOUT,
                }:
                    break
                continue

            searches_succeeded += 1
            hits_seen += len(response.results)
            for hit in response.results:
                canonical = canonicalize_url(hit.url)
                if not canonical:
                    gaps.append(
                        AcquisitionGap(
                            AcquisitionGapCode.INVALID_RESULT_URL,
                            "Search result URL was not a valid HTTP(S) URL.",
                            query=query,
                        )
                    )
                    continue
                valid_hits += 1
                ranked = classify_search_hit(hit, context, query=query)
                existing = discoveries.get(canonical)
                if existing is None or ranked.score > existing.ranked.score:
                    discoveries[canonical] = _RankedDiscovery(
                        canonical_url=canonical,
                        ranked=ranked,
                        query=query,
                    )

        if searches_succeeded and not discoveries:
            gaps.append(
                AcquisitionGap(
                    AcquisitionGapCode.NO_RESULTS,
                    "Search returned no usable original-page URLs.",
                )
            )

        ordered = sorted(
            discoveries.values(),
            key=lambda item: (
                item.ranked.quality_tier,
                -item.ranked.score,
                item.ranked.hit.provider_rank,
                item.canonical_url,
            ),
        )
        candidates: list[NormalizedSourceCandidate] = []
        final_urls: set[str] = set()
        fetches_attempted = 0
        fetches_succeeded = 0
        fetches_failed = 0

        for discovery in ordered[: self.config.max_fetches]:
            fetches_attempted += 1
            try:
                page = self.fetcher.fetch(discovery.ranked.hit.url)
            except SafeFetchError as exc:
                fetches_failed += 1
                blocked_codes = {
                    FetchErrorCode.INVALID_URL,
                    FetchErrorCode.UNSAFE_ADDRESS,
                    FetchErrorCode.DNS_FAILURE,
                    FetchErrorCode.UNSUPPORTED_CONTENT,
                    FetchErrorCode.TOO_LARGE,
                }
                gaps.append(
                    AcquisitionGap(
                        (
                            AcquisitionGapCode.FETCH_BLOCKED
                            if exc.code in blocked_codes
                            else AcquisitionGapCode.FETCH_FAILED
                        ),
                        "Original page could not be safely acquired.",
                        query=discovery.query,
                        url=discovery.ranked.hit.url,
                    )
                )
                continue
            except Exception:
                fetches_failed += 1
                gaps.append(
                    AcquisitionGap(
                        AcquisitionGapCode.FETCH_FAILED,
                        "Original page fetcher failed.",
                        query=discovery.query,
                        url=discovery.ranked.hit.url,
                    )
                )
                continue

            final_canonical = canonicalize_url(page.url)
            if not final_canonical:
                fetches_failed += 1
                gaps.append(
                    AcquisitionGap(
                        AcquisitionGapCode.FETCH_BLOCKED,
                        "Fetched page returned an invalid final URL.",
                        query=discovery.query,
                    )
                )
                continue
            if final_canonical in final_urls:
                continue
            final_urls.add(final_canonical)
            if not page.text.strip():
                fetches_failed += 1
                gaps.append(
                    AcquisitionGap(
                        AcquisitionGapCode.EMPTY_PAGE,
                        "Original page contained no usable visible text.",
                        query=discovery.query,
                        url=page.url,
                    )
                )
                continue

            final_hit = SearchHit(
                url=page.url,
                title=page.title,
                provider_rank=discovery.ranked.hit.provider_rank,
                publisher=page.publisher,
                provider=discovery.ranked.hit.provider,
            )
            final_rank = classify_search_hit(
                final_hit, context, query=discovery.query
            )
            flags = frozenset(
                discovery.ranked.consequence_flags
                | final_rank.consequence_flags
            )
            freshness_class = classify_freshness_class(
                title=page.title,
                url=page.url,
                source_role=final_rank.role,
                consequence_flags=flags,
            )
            freshness = assess_freshness(
                page.retrieved_at,
                freshness_class,
                now=page.retrieved_at,
            )
            candidates.append(
                NormalizedSourceCandidate(
                    source_id=_source_id(final_canonical),
                    url=page.url,
                    title=page.title,
                    publisher=page.publisher,
                    text=page.text,
                    content_type=page.content_type,
                    retrieved_at=page.retrieved_at,
                    source_updated_at=page.source_updated_at,
                    license=page.license,
                    bytes_read=page.bytes_read,
                    quality_tier=final_rank.quality_tier,
                    source_role=final_rank.role,
                    consequence_flags=flags,
                    rank_score=final_rank.score,
                    freshness_class=freshness_class,
                    freshness=freshness,
                    cache_allowed=page.cache_allowed,
                    cache_restriction=page.cache_restriction,
                )
            )
            fetches_succeeded += 1

        candidates.sort(
            key=lambda item: (
                item.quality_tier,
                -item.rank_score,
                item.url,
            )
        )
        stats = AcquisitionStats(
            provider=getattr(self.provider, "name", "unknown"),
            queries_generated=len(query_plan.queries),
            privacy_redactions=len(query_plan.redaction_categories),
            searches_attempted=searches_attempted,
            searches_succeeded=searches_succeeded,
            hits_seen=hits_seen,
            duplicate_hits_removed=max(0, valid_hits - len(discoveries)),
            fetches_attempted=fetches_attempted,
            fetches_succeeded=fetches_succeeded,
            fetches_blocked_or_failed=fetches_failed,
        )
        return AcquisitionResult(
            candidates=tuple(candidates),
            gaps=tuple(gaps),
            stats=stats,
            queries=query_plan.queries,
        )

    def _empty_result(self, gap: AcquisitionGap) -> AcquisitionResult:
        return AcquisitionResult(
            candidates=(),
            gaps=(gap,),
            stats=AcquisitionStats(
                provider=getattr(self.provider, "name", "unknown")
            ),
        )


_TRACKING_QUERY_KEYS = {
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


def canonicalize_url(url: str) -> str:
    """Create a conservative dedupe key without using page snippets."""

    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
        if port is not None and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        else:
            netloc = host
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")
        query_items = [
            (key, value)
            for key, value in parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if key.lower() not in _TRACKING_QUERY_KEYS
            and not key.lower().startswith("utm_")
        ]
        query = urlencode(sorted(query_items))
        return urlunsplit((scheme, netloc, path, query, ""))
    except (UnicodeError, ValueError):
        return ""


def _source_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:20]
    return f"web-{digest}"
