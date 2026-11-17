"""Parse bounded Economic Times RSS documents without performing retrieval."""

from datetime import datetime
from email.utils import parsedate_to_datetime
from unicodedata import category as unicode_category
from urllib.parse import urlsplit
from xml.parsers import expat

from marketpulse.data.market_news import (
    MarketNewsCategory,
    MarketNewsCollectionResult,
    MarketNewsItem,
    MarketNewsModelError,
    NewsSourceStatus,
    NewsUnavailableReason,
)

ET_SOURCE_ID = "economic_times"
ET_PUBLISHER = "The Economic Times"
MAX_XML_BYTES = 1_000_000
MAX_ITEMS = 200


class _InvalidFeed(Exception):
    """Signal unsafe or structurally invalid feed data without retaining XML."""


class _FeedHandler:
    """Collect only direct RSS title, link, GUID and publication-date fields."""

    def __init__(self, retrieved_at: datetime, category: MarketNewsCategory) -> None:
        """Initialize bounded feed state and preserve the supplied provenance."""
        self.retrieved_at = retrieved_at
        self.category = category
        self.stack: list[str] = []
        self.root_seen = False
        self.channel_count = 0
        self.item_count = 0
        self.items: list[MarketNewsItem] = []
        self.current_item: dict[str, str] | None = None
        self.active_field: str | None = None
        self.field_depth = 0
        self.field_text: list[str] = []
        self.bad_item = False

    def start(self, name: str, attributes: dict[str, str]) -> None:
        """Track RSS structure and capture only the fields used by the report."""
        del attributes
        tag = _local_name(name)
        depth = len(self.stack) + 1
        if depth == 1:
            if tag != "rss":
                raise _InvalidFeed
            self.root_seen = True
        elif depth == 2 and tag == "channel" and self.stack == ["rss"]:
            self.channel_count += 1
            if self.channel_count > 1:
                raise _InvalidFeed
        elif depth == 3 and tag == "item" and self.stack == ["rss", "channel"]:
            self.item_count += 1
            if self.item_count > MAX_ITEMS:
                raise _InvalidFeed
            self.current_item = {}
            self.bad_item = False
        elif (
            self.current_item is not None
            and depth == 4
            and self.stack[-2:] == ["channel", "item"]
            and tag in {"title", "link", "guid", "pubdate"}
        ):
            key = "pubDate" if tag == "pubdate" else tag
            if key in self.current_item or self.active_field is not None:
                self.bad_item = True
            self.active_field = key
            self.field_depth = depth
            self.field_text = []
        elif self.active_field is not None:
            self.bad_item = True
        self.stack.append(tag)

    def end(self, name: str) -> None:
        """Finalize fields and skip individual invalid or unsafe articles."""
        tag = _local_name(name)
        depth = len(self.stack)
        if self.active_field is not None and depth == self.field_depth:
            field_tag = "pubdate" if self.active_field == "pubDate" else self.active_field
            if tag == field_tag.lower():
                self.current_item[self.active_field] = "".join(self.field_text)
                self.active_field = None
                self.field_text = []
        if depth == 3 and tag == "item" and self.current_item is not None:
            if not self.bad_item:
                item = _build_item(self.current_item, self.retrieved_at, self.category)
                if item is not None:
                    self.items.append(item)
            self.current_item = None
            self.active_field = None
            self.field_text = []
        if not self.stack or self.stack[-1] != tag:
            raise _InvalidFeed
        self.stack.pop()

    def text(self, value: str) -> None:
        """Retain character data only inside one supported direct item field."""
        if self.active_field is not None and len(self.stack) == self.field_depth:
            self.field_text.append(value)

    def reject_doctype(self, *values: object) -> None:
        """Reject DTD declarations before entity definitions or expansion."""
        del values
        raise _InvalidFeed

    def reject_external_entity(
        self,
        context: str | None,
        base: str | None,
        system_id: str | None,
        public_id: str | None,
    ) -> int:
        """Refuse external entity resolution regardless of parser defaults."""
        del context, base, system_id, public_id
        return 0


def parse_et_rss(
    xml_bytes: bytes,
    retrieved_at: datetime,
    category: MarketNewsCategory = MarketNewsCategory.INDIAN_MARKETS,
) -> MarketNewsCollectionResult:
    """Parse bounded ET RSS bytes into validated source-neutral news items.

    Pass the existing topical category explicitly for economy or company feeds.
    """
    if not _is_aware(retrieved_at):
        raise ValueError("retrieved_at must be timezone-aware.")
    if not isinstance(category, MarketNewsCategory):
        raise ValueError("category must be a supported MarketNewsCategory.")
    if not isinstance(xml_bytes, bytes) or len(xml_bytes) > MAX_XML_BYTES:
        return _unavailable(retrieved_at)

    handler = _FeedHandler(retrieved_at, category)
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = handler.start
    parser.EndElementHandler = handler.end
    parser.CharacterDataHandler = handler.text
    parser.StartDoctypeDeclHandler = handler.reject_doctype
    parser.EntityDeclHandler = handler.reject_doctype
    parser.ExternalEntityRefHandler = handler.reject_external_entity
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(xml_bytes, True)
    except (expat.ExpatError, _InvalidFeed, ValueError):
        return _unavailable(retrieved_at)

    if not handler.root_seen or handler.channel_count != 1 or handler.stack:
        return _unavailable(retrieved_at)
    return MarketNewsCollectionResult(
        source_id=ET_SOURCE_ID,
        retrieved_at=retrieved_at,
        status=NewsSourceStatus.AVAILABLE,
        items=tuple(handler.items),
    )


def _build_item(
    fields: dict[str, str],
    retrieved_at: datetime,
    category: MarketNewsCategory,
) -> MarketNewsItem | None:
    """Map one safe RSS record to the existing source-neutral item contract."""
    title = fields.get("title")
    link = fields.get("link")
    if title is None or link is None:
        return None
    headline = " ".join(title.split())
    article_url = link.strip()
    if not headline or "<" in headline or ">" in headline or not _is_et_url(article_url):
        return None
    guid_value = fields.get("guid")
    guid = guid_value.strip() if guid_value and guid_value.strip() else None
    published_at = _parse_publication_time(fields.get("pubDate"))
    try:
        return MarketNewsItem(
            source_id=ET_SOURCE_ID,
            publisher="The Economic Times",
            headline=headline,
            article_url=article_url,
            category=category,
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_item_id=guid,
        )
    except MarketNewsModelError:
        return None


def _is_et_url(value: str) -> bool:
    """Accept only canonical Economic Times HTTPS article URLs without unsafe authority."""
    if not value or "\\" in value or any(
        character.isspace() or unicode_category(character) == "Cc" for character in value
    ):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme.lower() == "https"
            and parts.hostname is not None
            and parts.hostname.lower() == "economictimes.indiatimes.com"
            and parts.username is None
            and parts.password is None
            and (parts.port is None or parts.port == 443)
        )
    except ValueError:
        return False


def _parse_publication_time(value: str | None) -> datetime | None:
    """Parse an aware RSS publication date or preserve it as unavailable."""
    if not value or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if _is_aware(parsed) else None


def _local_name(value: str) -> str:
    """Return an XML element local name for plain or namespace-qualified RSS tags."""
    return value.rsplit("}", 1)[-1].lower()


def _is_aware(value: object) -> bool:
    """Return whether a datetime has a usable timezone offset."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except (OverflowError, TypeError, ValueError):
        return False


def _unavailable(retrieved_at: datetime) -> MarketNewsCollectionResult:
    """Return a fixed safe failure for malformed, unsafe, or oversized XML."""
    return MarketNewsCollectionResult(
        source_id=ET_SOURCE_ID,
        retrieved_at=retrieved_at,
        status=NewsSourceStatus.UNAVAILABLE,
        unavailable_reason=NewsUnavailableReason.INVALID_RESPONSE,
    )
