"""Acquire the three fixed Economic Times RSS feeds over bounded HTTPS."""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

import certifi

from marketpulse.data.market_news import (
    MarketNewsCategory,
    MarketNewsCollectionResult,
    MarketNewsItem,
    NewsSourceStatus,
    NewsUnavailableReason,
)
from marketpulse.data.news_sources.et_rss import MAX_XML_BYTES, ET_SOURCE_ID, parse_et_rss

ET_STOCK_MARKET_URL = (
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"
)
ET_ECONOMY_URL = (
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"
)
ET_COMPANY_URL = (
    "https://economictimes.indiatimes.com/news/company/rssfeeds/2143429.cms"
)
ET_APPROVED_HOST = "economictimes.indiatimes.com"
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RESPONSE_BYTES = MAX_XML_BYTES


@dataclass(frozen=True)
class EtFeedConfig:
    """Bind one reviewed feed URL to its source, report section, and topic category."""

    source_id: str
    url: str
    report_section: Literal["stock_market_news", "economic_news", "company_news"]
    category: MarketNewsCategory


ET_FEEDS = MappingProxyType({
    "stock_market": EtFeedConfig(
        "economic_times_stock_market",
        ET_STOCK_MARKET_URL,
        "stock_market_news",
        MarketNewsCategory.INDIAN_MARKETS,
    ),
    "economy": EtFeedConfig(
        "economic_times_economy",
        ET_ECONOMY_URL,
        "economic_news",
        MarketNewsCategory.ECONOMY_POLICY,
    ),
    "company": EtFeedConfig(
        "economic_times_company",
        ET_COMPANY_URL,
        "company_news",
        MarketNewsCategory.LISTED_COMPANIES,
    ),
})
_APPROVED_FEED_URLS = frozenset(config.url for config in ET_FEEDS.values())


class EtRssDownloadError(Exception):
    """Represent an ET acquisition failure using only a fixed public category."""

    def __init__(
        self,
        reason: NewsUnavailableReason,
        *,
        failure_category: EtRssFailureCategory | None = None,
        http_status: int | None = None,
        redirect_destination: str | None = None,
    ) -> None:
        """Store fixed failure metadata without retaining exception messages or secrets."""
        self.reason = reason
        self.failure_category = failure_category or EtRssFailureCategory.UNKNOWN_ERROR
        self.http_status = http_status
        self.redirect_destination = redirect_destination
        super().__init__(f"Economic Times RSS acquisition failed: {self.failure_category.value}.")


class EtRssFailureCategory(str, Enum):
    """Classify ET acquisition failures for developer diagnostics only."""

    HTTP_STATUS_ERROR = "HTTP_STATUS_ERROR"
    TLS_ERROR = "TLS_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT = "TIMEOUT"
    REDIRECT_REJECTED = "REDIRECT_REJECTED"
    REDIRECT_LIMIT = "REDIRECT_LIMIT"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class _EtHttpsRedirectHandler(HTTPRedirectHandler):
    """Allow only redirects between the three reviewed ET feed endpoints."""

    def __init__(self) -> None:
        """Track redirect occurrence and a query-free destination for diagnostics."""
        super().__init__()
        self.redirect_encountered = False
        self.last_redirect_destination: str | None = None

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request:
        """Reject downgrade, credential-bearing, external, and non-feed redirects."""
        self.redirect_encountered = True
        self.last_redirect_destination = _safe_redirect_destination(new_url)
        if not _is_approved_feed_url(new_url):
            raise EtRssDownloadError(
                NewsUnavailableReason.SOURCE_ERROR,
                failure_category=EtRssFailureCategory.REDIRECT_REJECTED,
                redirect_destination=self.last_redirect_destination,
            )
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if redirected is None:
            raise EtRssDownloadError(
                NewsUnavailableReason.SOURCE_ERROR,
                failure_category=EtRssFailureCategory.REDIRECT_REJECTED,
                redirect_destination=self.last_redirect_destination,
            )
        return redirected


class EtRssDownloader:
    """Download only configured ET RSS feeds with verified TLS and bounded reads."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Any | None = None,
    ) -> None:
        """Configure the verified HTTPS opener or an injected offline transport."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be a positive number.")
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 0 < max_response_bytes <= MAX_XML_BYTES
        ):
            raise ValueError("max_response_bytes must be between 1 and the parser limit.")
        self._timeout = float(timeout)
        self._max_response_bytes = max_response_bytes
        if opener is None:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._redirect_handler: _EtHttpsRedirectHandler | None = _EtHttpsRedirectHandler()
            self._opener = build_opener(
                self._redirect_handler,
                HTTPSHandler(context=ssl_context),
            )
        else:
            self._opener = opener
            self._redirect_handler = None
        self._last_redirect_encountered: bool | None = (
            False if self._redirect_handler is not None else None
        )
        self._last_redirect_destination: str | None = None
        self._last_http_status: int | None = None

    @property
    def last_redirect_encountered(self) -> bool | None:
        """Return whether the latest real-opener request encountered a redirect."""
        return self._last_redirect_encountered

    @property
    def last_redirect_destination(self) -> str | None:
        """Return the latest redirect host and path without credentials or query data."""
        return self._last_redirect_destination

    @property
    def last_http_status(self) -> int | None:
        """Return the latest numeric HTTP status without exposing response headers."""
        return self._last_http_status

    def download(self, feed_id: str) -> bytes:
        """Fetch one configured feed exactly once and return its bounded XML body."""
        feed = ET_FEEDS.get(feed_id)
        if feed is None:
            raise ValueError("feed_id must identify a configured Economic Times feed.")
        if self._redirect_handler is not None:
            self._redirect_handler.redirect_encountered = False
            self._redirect_handler.last_redirect_destination = None
        self._last_redirect_encountered = (
            False if self._redirect_handler is not None else None
        )
        self._last_redirect_destination = None
        self._last_http_status = None
        request = Request(
            feed.url,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.geturl() != feed.url or not _is_approved_feed_url(response.geturl()):
                    raise EtRssDownloadError(
                        NewsUnavailableReason.SOURCE_ERROR,
                        failure_category=EtRssFailureCategory.REDIRECT_REJECTED,
                        redirect_destination=_safe_redirect_destination(response.geturl()),
                    )
                status = response.getcode()
                self._last_http_status = status if isinstance(status, int) else None
                if status is None or not 200 <= status < 300:
                    raise EtRssDownloadError(
                        NewsUnavailableReason.SOURCE_ERROR,
                        failure_category=EtRssFailureCategory.HTTP_STATUS_ERROR,
                        http_status=status if isinstance(status, int) else None,
                    )
                content_length = _content_length(response)
                if content_length is not None and content_length < 0:
                    raise EtRssDownloadError(
                        NewsUnavailableReason.SOURCE_ERROR,
                        failure_category=EtRssFailureCategory.INVALID_RESPONSE,
                    )
                if content_length is not None and content_length > self._max_response_bytes:
                    raise EtRssDownloadError(
                        NewsUnavailableReason.SOURCE_ERROR,
                        failure_category=EtRssFailureCategory.RESPONSE_TOO_LARGE,
                    )
                self._capture_redirect_state()
                return _read_bounded(response, self._max_response_bytes)
        except EtRssDownloadError:
            self._capture_redirect_state()
            raise
        except HTTPError as error:
            self._capture_redirect_state()
            self._last_http_status = error.code if isinstance(error.code, int) else None
            if (
                error.code in {301, 302, 303, 307, 308}
                and self._last_redirect_encountered is True
            ):
                raise EtRssDownloadError(
                    NewsUnavailableReason.SOURCE_ERROR,
                    failure_category=EtRssFailureCategory.REDIRECT_LIMIT,
                    http_status=error.code,
                ) from None
            raise EtRssDownloadError(
                NewsUnavailableReason.SOURCE_ERROR,
                failure_category=EtRssFailureCategory.HTTP_STATUS_ERROR,
                http_status=error.code,
            ) from None
        except (TimeoutError, socket.timeout):
            self._capture_redirect_state()
            raise EtRssDownloadError(
                NewsUnavailableReason.TIMEOUT,
                failure_category=EtRssFailureCategory.TIMEOUT,
            ) from None
        except URLError as error:
            self._capture_redirect_state()
            category = _classify_transport_error(error.reason)
            reason = (
                NewsUnavailableReason.TIMEOUT
                if category is EtRssFailureCategory.TIMEOUT
                else NewsUnavailableReason.SOURCE_ERROR
            )
            raise EtRssDownloadError(reason, failure_category=category) from None
        except ssl.SSLError:
            self._capture_redirect_state()
            raise EtRssDownloadError(
                NewsUnavailableReason.SOURCE_ERROR,
                failure_category=EtRssFailureCategory.TLS_ERROR,
            ) from None
        except OSError as error:
            self._capture_redirect_state()
            category = _classify_transport_error(error)
            reason = (
                NewsUnavailableReason.TIMEOUT
                if category is EtRssFailureCategory.TIMEOUT
                else NewsUnavailableReason.SOURCE_ERROR
            )
            raise EtRssDownloadError(reason, failure_category=category) from None
        except Exception:
            self._capture_redirect_state()
            raise EtRssDownloadError(
                NewsUnavailableReason.SOURCE_ERROR,
                failure_category=EtRssFailureCategory.UNKNOWN_ERROR,
            ) from None

    def _capture_redirect_state(self) -> None:
        """Copy safe redirect flags from the default redirect handler."""
        if self._redirect_handler is not None:
            self._last_redirect_encountered = self._redirect_handler.redirect_encountered
            self._last_redirect_destination = self._redirect_handler.last_redirect_destination


class EtRssAcquirer:
    """Collect each configured ET feed independently into source-neutral results."""

    def __init__(self, downloader: EtRssDownloader | None = None) -> None:
        """Use the secure default downloader or an explicitly injected test fake."""
        self._downloader = downloader if downloader is not None else EtRssDownloader()

    def collect(self, retrieved_at: datetime) -> tuple[MarketNewsCollectionResult, ...]:
        """Acquire and parse each fixed feed once, isolating failures per category."""
        if not _is_aware(retrieved_at):
            raise ValueError("retrieved_at must be timezone-aware.")
        results = []
        for feed_id, config in ET_FEEDS.items():
            try:
                xml_bytes = self._downloader.download(feed_id)
            except EtRssDownloadError as error:
                results.append(_unavailable(config, retrieved_at, error.reason))
                continue
            except Exception:
                results.append(_unavailable(
                    config, retrieved_at, NewsUnavailableReason.SOURCE_ERROR
                ))
                continue
            try:
                parsed = parse_et_rss(xml_bytes, retrieved_at, config.category)
                results.append(_replace_source_identity(parsed, config))
            except Exception:
                results.append(_unavailable(
                    config, retrieved_at, NewsUnavailableReason.INVALID_RESPONSE
                ))
        return tuple(results)


def _replace_source_identity(
    result: MarketNewsCollectionResult,
    config: EtFeedConfig,
) -> MarketNewsCollectionResult:
    """Give each feed stable identities while preserving parsed article fields."""
    items = tuple(
        replace(item, source_id=config.source_id)
        for item in result.items
        if isinstance(item, MarketNewsItem)
    )
    return replace(result, source_id=config.source_id, items=items)


def _is_approved_feed_url(value: str) -> bool:
    """Require an exact configured feed URL on the approved HTTPS host and port."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
        return (
            value in _APPROVED_FEED_URLS
            and parts.scheme.lower() == "https"
            and parts.hostname is not None
            and parts.hostname.lower() == ET_APPROVED_HOST
            and parts.username is None
            and parts.password is None
            and parts.port in {None, 443}
            and parts.fragment == ""
        )
    except ValueError:
        return False


def _content_length(response: Any) -> int | None:
    """Return a declared body length, or None when the header is absent or malformed."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _read_bounded(response: Any, maximum: int) -> bytes:
    """Read at most one byte beyond the configured limit to detect oversized data."""
    body = bytearray()
    while len(body) <= maximum:
        chunk = response.read(min(64 * 1024, maximum + 1 - len(body)))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise EtRssDownloadError(NewsUnavailableReason.SOURCE_ERROR)
        body.extend(chunk)
    if len(body) > maximum:
        raise EtRssDownloadError(
            NewsUnavailableReason.SOURCE_ERROR,
            failure_category=EtRssFailureCategory.RESPONSE_TOO_LARGE,
        )
    return bytes(body)


def _unavailable(
    config: EtFeedConfig,
    retrieved_at: datetime,
    reason: NewsUnavailableReason,
) -> MarketNewsCollectionResult:
    """Create one fixed sanitized failure result for a configured feed."""
    return MarketNewsCollectionResult(
        source_id=config.source_id,
        retrieved_at=retrieved_at,
        status=NewsSourceStatus.UNAVAILABLE,
        unavailable_reason=reason,
    )


def _is_aware(value: object) -> bool:
    """Return whether a datetime includes a usable timezone offset."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False


def _classify_transport_error(error: object) -> EtRssFailureCategory:
    """Classify timeout, TLS, and socket failures without retaining their details."""
    if isinstance(error, (TimeoutError, socket.timeout)):
        return EtRssFailureCategory.TIMEOUT
    if isinstance(error, ssl.SSLError):
        return EtRssFailureCategory.TLS_ERROR
    if isinstance(error, (socket.gaierror, ConnectionError, OSError)):
        return EtRssFailureCategory.CONNECTION_ERROR
    return EtRssFailureCategory.UNKNOWN_ERROR


def _safe_redirect_destination(value: str) -> str:
    """Return redirect origin and path while dropping credentials, query, and fragment."""
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.hostname:
            return "unparseable"
        host = parts.hostname.lower()
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return f"{parts.scheme.lower()}://{host}{parts.path or '/'}"
    except (TypeError, ValueError):
        return "unparseable"
