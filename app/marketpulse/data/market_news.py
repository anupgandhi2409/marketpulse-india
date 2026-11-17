"""Source-neutral models for validated market-news collection results."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit, urlunsplit


class MarketNewsCategory(str, Enum):
    """Classify an item using a small stable set of editorial categories."""

    INDIAN_MARKETS = "indian_markets"
    ECONOMY_POLICY = "economy_policy"
    LISTED_COMPANIES = "listed_companies"


class NewsSourceStatus(str, Enum):
    """Describe whether a source request succeeded, independent of item count."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class NewsUnavailableReason(str, Enum):
    """Provide safe fixed reasons without retaining exception text."""

    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    SOURCE_ERROR = "source_error"


class MarketNewsModelError(ValueError):
    """Signal invalid news model values with non-sensitive validation messages."""


@dataclass(frozen=True)
class MarketNewsItem:
    """Hold one validated news headline and its source provenance."""

    source_id: str
    publisher: str
    headline: str
    article_url: str
    category: MarketNewsCategory
    published_at: datetime | None
    retrieved_at: datetime
    source_item_id: str | None = None

    def __post_init__(self) -> None:
        """Validate required text, URL safety, category, and timestamp awareness."""
        _require_text("source_id", self.source_id, 100)
        _require_text("publisher", self.publisher, 200)
        _require_text("headline", self.headline, 500)
        if self.source_item_id is not None:
            _require_text("source_item_id", self.source_item_id, 512)
        _validate_article_url(self.article_url)
        if not isinstance(self.category, MarketNewsCategory):
            raise MarketNewsModelError("category must be a supported MarketNewsCategory.")
        if self.published_at is not None and not _is_aware(self.published_at):
            raise MarketNewsModelError("published_at must be timezone-aware when provided.")
        if not _is_aware(self.retrieved_at):
            raise MarketNewsModelError("retrieved_at must be timezone-aware.")

    @property
    def deduplication_identity(self) -> tuple[str, str, str]:
        """Return a publisher-scoped GUID key or conservative URL key."""
        if self.source_item_id is not None:
            return self.source_id, self.publisher, f"guid:{self.source_item_id}"
        parts = urlsplit(self.article_url)
        netloc = parts.netloc.lower()
        normalized_url = urlunsplit(
            (parts.scheme.lower(), netloc, parts.path or "/", parts.query, "")
        )
        return self.source_id, self.publisher, f"url:{normalized_url}"


@dataclass(frozen=True)
class MarketNewsCollectionResult:
    """Describe one source attempt; success does not imply complete news coverage."""

    source_id: str
    retrieved_at: datetime
    status: NewsSourceStatus
    items: tuple[MarketNewsItem, ...] = ()
    window_start: datetime | None = None
    window_end: datetime | None = None
    unavailable_reason: NewsUnavailableReason | None = None

    def __post_init__(self) -> None:
        """Require consistent source status, item provenance, and optional window bounds."""
        _require_text("source_id", self.source_id, 100)
        if not _is_aware(self.retrieved_at):
            raise MarketNewsModelError("retrieved_at must be timezone-aware.")
        if not isinstance(self.status, NewsSourceStatus):
            raise MarketNewsModelError("status must be a supported NewsSourceStatus.")
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, MarketNewsItem) for item in self.items
        ):
            raise MarketNewsModelError("items must be a tuple of MarketNewsItem values.")
        if any(item.source_id != self.source_id for item in self.items):
            raise MarketNewsModelError("Every item must match the collection source_id.")
        if self.status is NewsSourceStatus.AVAILABLE:
            if self.unavailable_reason is not None:
                raise MarketNewsModelError("An available source cannot have an unavailable reason.")
        elif self.unavailable_reason is None or self.items:
            raise MarketNewsModelError(
                "An unavailable source needs a safe reason and cannot contain items."
            )
        elif not isinstance(self.unavailable_reason, NewsUnavailableReason):
            raise MarketNewsModelError("unavailable_reason must be a safe NewsUnavailableReason.")

        if (self.window_start is None) != (self.window_end is None):
            raise MarketNewsModelError("News window start and end must be provided together.")
        if self.window_start is not None:
            if not _is_aware(self.window_start) or not _is_aware(self.window_end):
                raise MarketNewsModelError("News window boundaries must be timezone-aware.")
            if self.window_start > self.window_end:
                raise MarketNewsModelError("News window start cannot be after its end.")

    @property
    def item_count(self) -> int:
        """Return the number of validated items in this successful collection."""
        return len(self.items)


def _require_text(field_name: str, value: object, maximum_length: int) -> None:
    """Require a bounded nonblank string without altering the supplied text."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum_length
    ):
        raise MarketNewsModelError(
            f"{field_name} must be a nonblank string of at most {maximum_length} characters."
        )


def _validate_article_url(value: object) -> None:
    """Require an HTTPS article URL with a host and no embedded credentials."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise MarketNewsModelError(
            "article_url must be a nonblank HTTPS URL of at most 2048 characters."
        )
    if "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
        raise MarketNewsModelError("article_url contains unsupported characters.")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise MarketNewsModelError("article_url is invalid.") from None
    if (
        parts.scheme.lower() != "https"
        or not parts.netloc
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise MarketNewsModelError("article_url must use HTTPS and cannot contain credentials.")


def _is_aware(value: object) -> bool:
    """Return whether a datetime has a usable timezone offset."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False
