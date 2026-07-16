from __future__ import annotations

import httpx

from app.services.source_search import (
    BraveSearchProvider,
    FakeSearchProvider,
    NullSearchProvider,
    SearchErrorCode,
    SearchFreshness,
    SearchHit,
    SearchRequest,
    default_search_provider,
)


def test_brave_without_key_never_calls_transport():
    def fail_if_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(fail_if_called))
    provider = BraveSearchProvider(api_key="", client=client)

    response = provider.search(SearchRequest("public event"))

    assert response.issue is not None
    assert response.issue.code is SearchErrorCode.NO_API_KEY
    assert response.results == ()
    client.close()


def test_default_provider_uses_null_without_environment_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    provider = default_search_provider()

    assert isinstance(provider, NullSearchProvider)


def test_brave_bounds_results_and_sends_freshness_filter():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        results = [
            {
                "url": f"https://public.example/{index}",
                "title": f"Result {index}",
                "description": f"Discovery snippet {index}",
                "age": "2 days ago",
                "profile": {"long_name": "Public Example"},
            }
            for index in range(30)
        ]
        return httpx.Response(200, json={"web": {"results": results}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = BraveSearchProvider(
        api_key="test-key",
        max_results=7,
        timeout_seconds=999,
        client=client,
    )

    response = provider.search(
        SearchRequest(
            "conference official schedule",
            count=20,
            freshness=SearchFreshness.PAST_WEEK,
            country="US",
            search_language="en",
        )
    )

    assert response.ok
    assert len(response.results) == 7
    assert all(hit.discovery_only for hit in response.results)
    assert response.results[0].publisher == "Public Example"
    assert captured[0].url.params["count"] == "7"
    assert captured[0].url.params["freshness"] == "pw"
    assert captured[0].url.params["country"] == "US"
    assert captured[0].headers["x-subscription-token"] == "test-key"
    assert provider.timeout_seconds == provider.max_timeout_seconds
    client.close()


def test_brave_provider_outage_is_returned_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = BraveSearchProvider(api_key="test-key", client=client)

    response = provider.search(SearchRequest("official source"))

    assert response.issue is not None
    assert response.issue.code is SearchErrorCode.PROVIDER_OUTAGE
    assert response.issue.retryable is True
    client.close()


def test_fake_provider_is_bounded_and_records_requests():
    hits = [
        SearchHit(url=f"https://example.com/{index}", title=str(index))
        for index in range(4)
    ]
    provider = FakeSearchProvider({"query": hits})

    response = provider.search(SearchRequest("query", count=2))

    assert len(response.results) == 2
    assert response.results[0].provider == "fake"
    assert provider.calls == [SearchRequest("query", count=2)]
