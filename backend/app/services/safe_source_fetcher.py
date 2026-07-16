"""SSRF-resistant original-page fetcher for Prepare-time source acquisition."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from html.parser import HTMLParser
from typing import Callable, Iterable, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


Resolver = Callable[[str, int], Sequence[str]]
Clock = Callable[[], datetime]

_ALLOWED_CONTENT_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "text/markdown",
    "text/plain",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SUPPRESSED_HTML_TAGS = {"script", "style", "template", "noscript"}
_BLOCK_HTML_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


class FetchErrorCode(str, Enum):
    INVALID_URL = "invalid_url"
    UNSAFE_ADDRESS = "unsafe_address"
    DNS_FAILURE = "dns_failure"
    REDIRECT_LIMIT = "redirect_limit"
    REDIRECT_LOOP = "redirect_loop"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    UNSUPPORTED_CONTENT = "unsupported_content"
    TOO_LARGE = "too_large"


class SafeFetchError(RuntimeError):
    def __init__(self, code: FetchErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    publisher: str
    text: str
    content_type: str
    retrieved_at: datetime
    source_updated_at: datetime | None = None
    license: str | None = None
    bytes_read: int = 0
    cache_allowed: bool = True
    cache_restriction: str | None = None

    def cacheable_text(self) -> str | None:
        """Return text only when the source permits ordinary page caching."""

        return self.text if self.cache_allowed else None


class SafeOriginalPageFetcher:
    """Fetch a small public text page while revalidating every redirect."""

    max_timeout_seconds = 20.0
    hard_max_bytes = 5 * 1024 * 1024
    hard_max_redirects = 10

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        max_bytes: int = 1_000_000,
        max_redirects: int = 5,
        resolver: Resolver | None = None,
        client: httpx.Client | None = None,
        clock: Clock | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        self.timeout_seconds = min(
            timeout_seconds, self.max_timeout_seconds
        )
        self.max_bytes = min(max_bytes, self.hard_max_bytes)
        self.max_redirects = min(max_redirects, self.hard_max_redirects)
        self.resolver = resolver or resolve_host
        self._client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, url: str) -> FetchedPage:
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )

        current_url = url
        seen: set[str] = set()
        try:
            for redirect_count in range(self.max_redirects + 1):
                validated = validate_public_url(
                    current_url, resolver=self.resolver
                )
                if validated.url in seen:
                    raise SafeFetchError(
                        FetchErrorCode.REDIRECT_LOOP,
                        "redirect loop detected",
                    )
                seen.add(validated.url)

                try:
                    with client.stream(
                        "GET",
                        validated.url,
                        headers={
                            "Accept": (
                                "text/html, application/xhtml+xml, "
                                "text/plain;q=0.9, text/markdown;q=0.8"
                            ),
                            "Accept-Encoding": "gzip, deflate",
                            "User-Agent": "PrepareForOffline/0.2 source-fetcher",
                        },
                        timeout=self.timeout_seconds,
                        follow_redirects=False,
                    ) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise SafeFetchError(
                                    FetchErrorCode.HTTP_ERROR,
                                    "redirect response has no Location header",
                                )
                            if redirect_count >= self.max_redirects:
                                raise SafeFetchError(
                                    FetchErrorCode.REDIRECT_LIMIT,
                                    "redirect limit exceeded",
                                )
                            current_url = urljoin(validated.url, location)
                            continue

                        if not 200 <= response.status_code < 300:
                            raise SafeFetchError(
                                FetchErrorCode.HTTP_ERROR,
                                f"source returned HTTP {response.status_code}",
                            )
                        content_type = _content_type(response.headers)
                        if content_type not in _ALLOWED_CONTENT_TYPES:
                            raise SafeFetchError(
                                FetchErrorCode.UNSUPPORTED_CONTENT,
                                "source is not an allowed text or HTML page",
                            )
                        declared_size = _content_length(response.headers)
                        if (
                            declared_size is not None
                            and declared_size > self.max_bytes
                        ):
                            raise SafeFetchError(
                                FetchErrorCode.TOO_LARGE,
                                "source exceeds the maximum response size",
                            )
                        body = _read_bounded(response, self.max_bytes)
                        response_headers = dict(response.headers)
                except httpx.TimeoutException as exc:
                    raise SafeFetchError(
                        FetchErrorCode.TIMEOUT, "source fetch timed out"
                    ) from exc
                except httpx.TransportError as exc:
                    raise SafeFetchError(
                        FetchErrorCode.TRANSPORT_ERROR,
                        "source fetch failed",
                    ) from exc

                return _page_from_response(
                    validated.url,
                    body,
                    content_type,
                    response_headers,
                    retrieved_at=_utc(self.clock()),
                )
        finally:
            if owns_client:
                client.close()

        raise SafeFetchError(
            FetchErrorCode.REDIRECT_LIMIT,
            "redirect limit exceeded",
        )


def validate_public_url(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> ValidatedURL:
    """Resolve a URL and reject any non-global destination address."""

    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise SafeFetchError(FetchErrorCode.INVALID_URL, "invalid source URL")
    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise SafeFetchError(
                FetchErrorCode.INVALID_URL,
                "only http and https source URLs are allowed",
            )
        if parsed.username is not None or parsed.password is not None:
            raise SafeFetchError(
                FetchErrorCode.INVALID_URL,
                "source URLs may not contain credentials",
            )
        raw_host = parsed.hostname
        if not raw_host:
            raise SafeFetchError(
                FetchErrorCode.INVALID_URL,
                "source URL has no hostname",
            )
        if "%" in raw_host:
            raise SafeFetchError(
                FetchErrorCode.INVALID_URL,
                "scoped IP addresses are not allowed",
            )
        host = raw_host.rstrip(".").encode("idna").decode("ascii").lower()
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or host.endswith(".local")
        ):
            raise SafeFetchError(
                FetchErrorCode.UNSAFE_ADDRESS,
                "local source addresses are not allowed",
            )
        default_port = 443 if scheme == "https" else 80
        port = parsed.port or default_port
        if port not in {80, 443}:
            raise SafeFetchError(
                FetchErrorCode.INVALID_URL,
                "only standard HTTP and HTTPS ports are allowed",
            )
    except SafeFetchError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise SafeFetchError(
            FetchErrorCode.INVALID_URL, "invalid source URL"
        ) from exc

    addresses: Sequence[str]
    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = (resolver or resolve_host)(host, port)
        except (OSError, socket.gaierror) as exc:
            raise SafeFetchError(
                FetchErrorCode.DNS_FAILURE,
                "source hostname could not be resolved",
            ) from exc
    else:
        addresses = (str(direct_ip),)

    normalized_addresses: list[str] = []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(str(raw_address))
        except ValueError as exc:
            raise SafeFetchError(
                FetchErrorCode.DNS_FAILURE,
                "source hostname resolved to an invalid address",
            ) from exc
        if not address.is_global:
            raise SafeFetchError(
                FetchErrorCode.UNSAFE_ADDRESS,
                "source resolved to a non-public address",
            )
        normalized_addresses.append(str(address))
    if not normalized_addresses:
        raise SafeFetchError(
            FetchErrorCode.DNS_FAILURE,
            "source hostname did not resolve to an address",
        )

    host_for_url = f"[{host}]" if ":" in host else host
    include_port = parsed.port is not None
    netloc = f"{host_for_url}:{port}" if include_port else host_for_url
    normalized_url = urlunsplit(
        (scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    return ValidatedURL(
        url=normalized_url,
        host=host,
        port=port,
        addresses=tuple(dict.fromkeys(normalized_addresses)),
    )


def resolve_host(host: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(dict.fromkeys(record[4][0] for record in records))


def is_map_or_place_url(url: str) -> bool:
    """Identify sources whose provider terms commonly prohibit page caching."""

    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.lower()
    except ValueError:
        return False
    fixed_hosts = {
        "maps.apple.com",
        "maps.google.com",
        "maps.googleapis.com",
        "openstreetmap.org",
        "places.googleapis.com",
        "www.openstreetmap.org",
    }
    if host in fixed_hosts or host.endswith(".mapbox.com"):
        return True
    if (
        host == "google.com"
        or host.endswith(".google.com")
        or host == "bing.com"
        or host.endswith(".bing.com")
    ):
        return path.startswith("/maps") or "/place/" in path
    return False


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise SafeFetchError(
                FetchErrorCode.TOO_LARGE,
                "source exceeds the maximum response size",
            )
        body.extend(chunk)
    return bytes(body)


def _page_from_response(
    url: str,
    body: bytes,
    content_type: str,
    headers: dict[str, str],
    *,
    retrieved_at: datetime,
) -> FetchedPage:
    text = _decode_body(body, headers.get("content-type", ""))
    title = ""
    publisher = ""
    updated_value: str | None = None
    license_value: str | None = None
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleHTMLParser(url)
        parser.feed(text)
        parser.close()
        visible_text = parser.visible_text()
        title = parser.title()
        publisher = parser.publisher
        updated_value = parser.source_updated_at
        license_value = parser.license
    else:
        visible_text = _clean_text(text)

    if not updated_value:
        updated_value = headers.get("last-modified")
    source_updated_at = _parse_page_timestamp(updated_value)
    host = (urlsplit(url).hostname or "").lower()
    cache_allowed = not is_map_or_place_url(url)
    return FetchedPage(
        url=url,
        title=title or host or url,
        publisher=publisher or host,
        text=visible_text,
        content_type=content_type,
        retrieved_at=retrieved_at,
        source_updated_at=source_updated_at,
        license=license_value,
        bytes_read=len(body),
        cache_allowed=cache_allowed,
        cache_restriction=(
            None
            if cache_allowed
            else "Map/place-provider page content must not be cached."
        ),
    )


class _VisibleHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._suppressed_depth = 0
        self._title_depth = 0
        self._text_parts: list[str] = []
        self._title_parts: list[str] = []
        self.publisher = ""
        self.source_updated_at: str | None = None
        self.license: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if self._suppressed_depth:
            self._suppressed_depth += 1
            return
        if tag in _SUPPRESSED_HTML_TAGS:
            self._suppressed_depth = 1
            return
        attributes = {
            key.lower(): value or "" for key, value in attrs
        }
        if tag == "title":
            self._title_depth += 1
        if tag in _BLOCK_HTML_TAGS:
            self._text_parts.append("\n")
        if tag == "meta":
            self._read_meta(attributes)
        elif tag == "link":
            rel = attributes.get("rel", "").lower().split()
            href = attributes.get("href", "").strip()
            if "license" in rel and href:
                self.license = urljoin(self.base_url, href)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._suppressed_depth:
            self._suppressed_depth -= 1
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in _BLOCK_HTML_TAGS:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        if self._title_depth:
            self._title_parts.append(data)
        self._text_parts.append(data)

    def visible_text(self) -> str:
        return _clean_text("".join(self._text_parts))

    def title(self) -> str:
        return _clean_text(" ".join(self._title_parts))

    def _read_meta(self, attributes: dict[str, str]) -> None:
        key = (
            attributes.get("property")
            or attributes.get("name")
            or attributes.get("http-equiv")
            or ""
        ).strip().lower()
        content = attributes.get("content", "").strip()
        if not key or not content:
            return
        if key in {
            "application-name",
            "dc.publisher",
            "og:site_name",
            "publisher",
        }:
            self.publisher = content
        elif key in {
            "article:modified_time",
            "date.modified",
            "dc.date.modified",
            "last-modified",
            "last_modified",
        }:
            self.source_updated_at = content
        elif key in {"dc.rights", "license", "rights"}:
            self.license = content


def _clean_text(text: str) -> str:
    text = "".join(
        character
        for character in text
        if character in "\n\t " or ord(character) >= 32
    )
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_body(body: bytes, content_type_header: str) -> str:
    match = re.search(
        r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)",
        content_type_header,
        re.IGNORECASE,
    )
    encodings: Iterable[str] = (
        (match.group(1), "utf-8", "windows-1252")
        if match
        else ("utf-8", "windows-1252")
    )
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("content-length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_page_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            parsed = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return None
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
