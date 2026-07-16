from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.services.safe_source_fetcher import (
    FetchErrorCode,
    SafeFetchError,
    SafeOriginalPageFetcher,
    is_map_or_place_url,
    validate_public_url,
)


def public_resolver(host: str, port: int) -> tuple[str, ...]:
    del host, port
    return ("8.8.8.8",)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://public.example/file",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://[::1]/",
        "https://public.example:8443/",
        "https://user:password@public.example/",
    ],
)
def test_url_validator_rejects_non_public_or_non_http_targets(url):
    with pytest.raises(SafeFetchError):
        validate_public_url(url, resolver=public_resolver)


def test_url_validator_rejects_private_or_mixed_dns_answers():
    def private_resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return ("10.0.0.8",)

    def mixed_resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        return ("8.8.8.8", "169.254.169.254")

    with pytest.raises(SafeFetchError) as private:
        validate_public_url(
            "https://private.example/", resolver=private_resolver
        )
    with pytest.raises(SafeFetchError) as mixed:
        validate_public_url("https://mixed.example/", resolver=mixed_resolver)

    assert private.value.code is FetchErrorCode.UNSAFE_ADDRESS
    assert mixed.value.code is FetchErrorCode.UNSAFE_ADDRESS


def test_redirect_target_is_revalidated_before_second_request():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = SafeOriginalPageFetcher(
        client=client, resolver=public_resolver
    )

    with pytest.raises(SafeFetchError) as caught:
        fetcher.fetch("https://public.example/start")

    assert caught.value.code is FetchErrorCode.UNSAFE_ADDRESS
    assert requested == ["https://public.example/start"]
    client.close()


def test_fetches_original_page_sanitizes_html_and_retains_metadata():
    requested: list[str] = []
    html = b"""
        <html>
          <head>
            <title>Official Visitor Guide</title>
            <meta property="og:site_name" content="Example Tourism Board">
            <meta name="article:modified_time"
                  content="2026-07-15T10:30:00Z">
            <link rel="license" href="/open-license">
            <style>.private { display: none }</style>
            <script>window.SECRET = "snippet is not evidence";</script>
          </head>
          <body>
            <h1>Getting to the venue</h1>
            <p>Take the official airport train.</p>
          </body>
        </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/guide"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=html,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    retrieved = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    fetcher = SafeOriginalPageFetcher(
        client=client,
        resolver=public_resolver,
        clock=lambda: retrieved,
    )

    page = fetcher.fetch("https://public.example/start")

    assert requested == [
        "https://public.example/start",
        "https://public.example/guide",
    ]
    assert page.url == "https://public.example/guide"
    assert page.title == "Official Visitor Guide"
    assert page.publisher == "Example Tourism Board"
    assert "Getting to the venue" in page.text
    assert "official airport train" in page.text
    assert "window.SECRET" not in page.text
    assert "display: none" not in page.text
    assert page.retrieved_at == retrieved
    assert page.source_updated_at == datetime(
        2026, 7, 15, 10, 30, tzinfo=timezone.utc
    )
    assert page.license == "https://public.example/open-license"
    assert page.cache_allowed is True
    client.close()


def test_rejects_non_text_content_and_oversized_response():
    def pdf_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF",
        )

    pdf_client = httpx.Client(transport=httpx.MockTransport(pdf_handler))
    pdf_fetcher = SafeOriginalPageFetcher(
        client=pdf_client, resolver=public_resolver
    )
    with pytest.raises(SafeFetchError) as unsupported:
        pdf_fetcher.fetch("https://public.example/file")
    assert unsupported.value.code is FetchErrorCode.UNSUPPORTED_CONTENT
    pdf_client.close()

    def large_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"x" * 20,
        )

    large_client = httpx.Client(transport=httpx.MockTransport(large_handler))
    large_fetcher = SafeOriginalPageFetcher(
        client=large_client,
        resolver=public_resolver,
        max_bytes=10,
    )
    with pytest.raises(SafeFetchError) as too_large:
        large_fetcher.fetch("https://public.example/large")
    assert too_large.value.code is FetchErrorCode.TOO_LARGE
    large_client.close()


def test_map_and_place_pages_are_explicitly_non_cacheable():
    assert is_map_or_place_url(
        "https://www.google.com/maps/place/Example"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"Transient place details",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = SafeOriginalPageFetcher(
        client=client, resolver=public_resolver
    )

    page = fetcher.fetch("https://maps.google.com/place/example")

    assert page.cache_allowed is False
    assert page.cacheable_text() is None
    assert "must not be cached" in (page.cache_restriction or "")
    client.close()
