"""Provider-agnostic web search primitives for Prepare-time discovery.

Search results are discovery hints.  Their snippets are never evidence and
must not be copied into a prepared pack as cited facts.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence

import httpx


class SearchFreshness(str, Enum):
    """Freshness filters supported by Brave's web search endpoint."""

    ANY = ""
    PAST_DAY = "pd"
    PAST_WEEK = "pw"
    PAST_MONTH = "pm"
    PAST_YEAR = "py"


class SearchErrorCode(str, Enum):
    NO_API_KEY = "no_api_key"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_OUTAGE = "provider_outage"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class SearchRequest:
    query: str
    count: int = 10
    freshness: SearchFreshness = SearchFreshness.ANY
    country: str | None = None
    search_language: str | None = None

    def __post_init__(self) -> None:
        query = self.query.strip()
        if not query:
            raise ValueError("search query must not be empty")
        if len(query) > 512:
            raise ValueError("search query must be at most 512 characters")
        if self.count < 1:
            raise ValueError("search result count must be positive")
        object.__setattr__(self, "query", query)


@dataclass(frozen=True)
class SearchHit:
    """A provider result which may only be used to discover an original URL."""

    url: str
    title: str
    snippet: str = ""
    provider_rank: int = 0
    published_at: str | None = None
    publisher: str | None = None
    provider: str = "unknown"
    discovery_only: bool = field(default=True, init=False)


@dataclass(frozen=True)
class SearchIssue:
    code: SearchErrorCode
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class SearchResponse:
    provider: str
    query: str
    results: tuple[SearchHit, ...] = ()
    issue: SearchIssue | None = None
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.issue is None


class SearchProvider(Protocol):
    """Minimal synchronous provider contract used by acquisition jobs."""

    name: str

    def search(self, request: SearchRequest) -> SearchResponse:
        ...


class BraveSearchProvider:
    """Bounded Brave Search API adapter.

    A client can be injected for tests.  When no client is supplied, the
    provider creates a short-lived client with environment proxies disabled.
    """

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    max_api_results = 20
    max_timeout_seconds = 15.0

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_results: int = 10,
        timeout_seconds: float = 8.0,
        client: httpx.Client | None = None,
    ) -> None:
        if max_results < 1:
            raise ValueError("max_results must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = (
            os.environ.get("BRAVE_SEARCH_API_KEY", "")
            if api_key is None
            else api_key
        ).strip()
        self.max_results = min(max_results, self.max_api_results)
        self.timeout_seconds = min(timeout_seconds, self.max_timeout_seconds)
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, request: SearchRequest) -> SearchResponse:
        started = time.monotonic()
        if not self.api_key:
            return SearchResponse(
                provider=self.name,
                query=request.query,
                issue=SearchIssue(
                    SearchErrorCode.NO_API_KEY,
                    "Brave Search is disabled because no API key is configured.",
                ),
            )

        count = min(request.count, self.max_results, self.max_api_results)
        params: dict[str, str | int] = {
            "q": request.query,
            "count": count,
            "safesearch": "moderate",
        }
        if request.freshness is not SearchFreshness.ANY:
            params["freshness"] = request.freshness.value
        if request.country:
            params["country"] = request.country
        if request.search_language:
            params["search_lang"] = request.search_language
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        try:
            if self._client is not None:
                response = self._client.get(
                    self.endpoint,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response = client.get(
                        self.endpoint,
                        params=params,
                        headers=headers,
                    )
        except httpx.TimeoutException:
            return self._failure(
                request,
                started,
                SearchErrorCode.TIMEOUT,
                "Brave Search timed out.",
                retryable=True,
            )
        except httpx.TransportError:
            return self._failure(
                request,
                started,
                SearchErrorCode.PROVIDER_OUTAGE,
                "Brave Search is temporarily unavailable.",
                retryable=True,
            )

        if response.status_code == 429:
            return self._failure(
                request,
                started,
                SearchErrorCode.RATE_LIMITED,
                "Brave Search rate limit reached.",
                retryable=True,
            )
        if response.status_code >= 500:
            return self._failure(
                request,
                started,
                SearchErrorCode.PROVIDER_OUTAGE,
                "Brave Search is temporarily unavailable.",
                retryable=True,
            )
        if response.status_code < 200 or response.status_code >= 300:
            return self._failure(
                request,
                started,
                SearchErrorCode.HTTP_ERROR,
                f"Brave Search returned HTTP {response.status_code}.",
            )

        try:
            payload = response.json()
            raw_results = payload.get("web", {}).get("results", [])
            if not isinstance(raw_results, list):
                raise TypeError("web.results is not a list")
        except (TypeError, ValueError):
            return self._failure(
                request,
                started,
                SearchErrorCode.INVALID_RESPONSE,
                "Brave Search returned an invalid response.",
            )

        hits: list[SearchHit] = []
        for rank, item in enumerate(raw_results[:count]):
            if not isinstance(item, Mapping):
                continue
            url = item.get("url")
            title = item.get("title")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            profile = item.get("profile")
            publisher = None
            if isinstance(profile, Mapping):
                long_name = profile.get("long_name")
                if isinstance(long_name, str):
                    publisher = long_name
            age = item.get("age")
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=(
                        item.get("description", "")
                        if isinstance(item.get("description", ""), str)
                        else ""
                    ),
                    provider_rank=rank,
                    published_at=age if isinstance(age, str) else None,
                    publisher=publisher,
                    provider=self.name,
                )
            )

        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=tuple(hits),
            elapsed_ms=self._elapsed_ms(started),
        )

    def _failure(
        self,
        request: SearchRequest,
        started: float,
        code: SearchErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> SearchResponse:
        return SearchResponse(
            provider=self.name,
            query=request.query,
            issue=SearchIssue(code, message, retryable),
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))


class NullSearchProvider:
    """No-network provider used when online enrichment is unavailable."""

    name = "null"

    def __init__(
        self,
        code: SearchErrorCode = SearchErrorCode.NO_API_KEY,
        message: str = "Online search is not configured.",
    ) -> None:
        self.code = code
        self.message = message
        self.calls: list[SearchRequest] = []

    @property
    def available(self) -> bool:
        return False

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls.append(request)
        return SearchResponse(
            provider=self.name,
            query=request.query,
            issue=SearchIssue(self.code, self.message),
        )


class FakeSearchProvider:
    """Deterministic provider for tests and offline development."""

    name = "fake"

    def __init__(
        self,
        results: Mapping[str, Sequence[SearchHit]] | None = None,
        *,
        issue: SearchIssue | None = None,
    ) -> None:
        self._results = {
            query: tuple(items) for query, items in (results or {}).items()
        }
        self.issue = issue
        self.calls: list[SearchRequest] = []

    @property
    def available(self) -> bool:
        return self.issue is None

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls.append(request)
        if self.issue is not None:
            return SearchResponse(
                provider=self.name,
                query=request.query,
                issue=self.issue,
            )
        hits = self._results.get(request.query, ())
        bounded = hits[: request.count]
        normalized = tuple(
            SearchHit(
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                provider_rank=hit.provider_rank,
                published_at=hit.published_at,
                publisher=hit.publisher,
                provider=self.name,
            )
            for hit in bounded
        )
        return SearchResponse(
            provider=self.name,
            query=request.query,
            results=normalized,
        )


def default_search_provider(
    *,
    api_key: str | None = None,
    max_results: int = 10,
    timeout_seconds: float = 8.0,
) -> SearchProvider:
    """Select Brave when configured, otherwise a no-network provider."""

    if api_key is None:
        from .search_credentials import get_key

        key = get_key()
    else:
        key = api_key.strip()
    if not key:
        return NullSearchProvider()
    return BraveSearchProvider(
        key,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
    )
