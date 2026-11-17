"""Source-neutral snapshot models for MarketPulse India reports."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar
from unicodedata import category as unicode_category
from urllib.parse import urlsplit

from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.market_news import NewsUnavailableReason
from marketpulse.data.market_movers_service import MarketMoversResult
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.year_high_service import StocksAt52WeekHighResult

T = TypeVar("T")


class SectionStatus(str, Enum):
    """Describe whether a report section is current, retained, or unavailable."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    CACHED = "cached"


class ReportSnapshotError(ValueError):
    """Signal invalid report identity, section state, or coverage metadata."""


class ReportArticleCategory(str, Enum):
    """Identify the source listing that supplied a report article."""

    STOCK_MARKET_NEWS = "stock_market_news"
    ECONOMIC_NEWS = "economic_news"
    COMPANY_NEWS = "company_news"


class ReportArticleStatus(str, Enum):
    """Describe one source-listing section independently of the others."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class ReportArticlePublisher(str, Enum):
    """Identify the sole supported publisher for source-listed report articles."""

    ECONOMIC_TIMES = "The Economic Times"


@dataclass(frozen=True)
class ReportArticle:
    """Hold a validated source-listed article without topical reclassification."""

    headline: str
    article_url: str
    source_category: ReportArticleCategory
    retrieved_at: datetime
    published_at: datetime | None = None
    article_identity: str | None = None
    publisher: ReportArticlePublisher = ReportArticlePublisher.ECONOMIC_TIMES

    def __post_init__(self) -> None:
        """Validate article text, category, URL, and supplied timestamps."""
        if not _bounded_text(self.headline, 1200):
            raise ReportSnapshotError("Article headline must be nonblank and bounded.")
        if not isinstance(self.publisher, ReportArticlePublisher):
            raise ReportSnapshotError("Source-listed articles need a supported publisher.")
        if self.article_identity is not None and not _bounded_text(self.article_identity, 512):
            raise ReportSnapshotError("Article identity must be nonblank and bounded when provided.")
        if not isinstance(self.source_category, ReportArticleCategory):
            raise ReportSnapshotError("Article source category is invalid.")
        if not _safe_https_url(self.article_url):
            raise ReportSnapshotError("Article URL must be HTTPS without embedded credentials.")
        if not _is_aware(self.retrieved_at) or (
            self.published_at is not None and not _is_aware(self.published_at)
        ):
            raise ReportSnapshotError("Article timestamps must be timezone-aware when provided.")


@dataclass(frozen=True)
class ReportArticleSection:
    """Keep one listing category's status, retrieval time, and ordered articles."""

    category: ReportArticleCategory
    status: ReportArticleStatus
    retrieved_at: datetime | None = None
    articles: tuple[ReportArticle, ...] = ()
    unavailable_reason: NewsUnavailableReason | None = None

    def __post_init__(self) -> None:
        """Enforce independent configured, available, and unavailable states."""
        if not isinstance(self.category, ReportArticleCategory) or not isinstance(
            self.status, ReportArticleStatus
        ):
            raise ReportSnapshotError("Article section category or status is invalid.")
        if not isinstance(self.articles, tuple) or len(self.articles) > 15 or any(
            not isinstance(article, ReportArticle) or article.source_category is not self.category
            for article in self.articles
        ):
            raise ReportSnapshotError("Article section must contain at most 15 matching article records.")
        if len({_article_url_identity(item.article_url) for item in self.articles}) != len(self.articles):
            raise ReportSnapshotError("Article URLs must be unique within a section.")
        if self.status is ReportArticleStatus.NOT_CONFIGURED:
            if (
                self.retrieved_at is not None
                or self.articles
                or self.unavailable_reason is not None
            ):
                raise ReportSnapshotError("Not-configured article sections cannot contain retrieval data.")
            return
        if not _is_aware(self.retrieved_at):
            raise ReportSnapshotError("Configured article sections need an aware retrieval timestamp.")
        if self.status is ReportArticleStatus.AVAILABLE:
            if self.unavailable_reason is not None:
                raise ReportSnapshotError("Available article sections cannot have an unavailable reason.")
        elif (
            self.articles
            or not isinstance(self.unavailable_reason, NewsUnavailableReason)
            or self.unavailable_reason is NewsUnavailableReason.NOT_CONFIGURED
        ):
            raise ReportSnapshotError("Unavailable article sections need a safe reason and no articles.")


@dataclass(frozen=True)
class ReportArticleSections:
    """Group the three independent source-page collections for a report."""

    stock_market: ReportArticleSection
    economic: ReportArticleSection
    company: ReportArticleSection

    def __post_init__(self) -> None:
        """Require exactly one correctly categorized section for each listing."""
        expected = (
            (self.stock_market, ReportArticleCategory.STOCK_MARKET_NEWS),
            (self.economic, ReportArticleCategory.ECONOMIC_NEWS),
            (self.company, ReportArticleCategory.COMPANY_NEWS),
        )
        if any(not isinstance(section, ReportArticleSection) or section.category is not category
               for section, category in expected):
            raise ReportSnapshotError("All three source-page article sections are required in category order.")


@dataclass(frozen=True)
class CoverageMetadata:
    """Record expected and validated record counts for a report section."""

    expected_count: int
    validated_count: int
    complete: bool

    def __post_init__(self) -> None:
        """Require consistent nonnegative coverage counts."""
        if (
            isinstance(self.expected_count, bool)
            or not isinstance(self.expected_count, int)
            or self.expected_count <= 0
            or isinstance(self.validated_count, bool)
            or not isinstance(self.validated_count, int)
            or not 0 <= self.validated_count <= self.expected_count
            or not isinstance(self.complete, bool)
            or self.complete != (self.validated_count == self.expected_count)
        ):
            raise ReportSnapshotError("Report section coverage metadata is inconsistent.")


@dataclass(frozen=True)
class ReportSection(Generic[T]):
    """Carry section data with provenance, coverage, and availability state."""

    status: SectionStatus
    data: T | None = None
    source: str | None = None
    retrieved_at: datetime | None = None
    coverage: CoverageMetadata | None = None
    unavailable_reason: str | None = None
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep unavailable, cached, and derived section states unambiguous."""
        if not isinstance(self.status, SectionStatus):
            raise ReportSnapshotError("Report section status is invalid.")
        if self.status is SectionStatus.UNAVAILABLE:
            if self.data is not None or not _nonblank(self.unavailable_reason):
                raise ReportSnapshotError(
                    "Unavailable report sections need a reason and cannot contain data."
                )
            if self.retrieved_at is not None and not _is_aware(self.retrieved_at):
                raise ReportSnapshotError("Report section retrieval time must be timezone-aware.")
        else:
            if self.data is None or self.unavailable_reason is not None:
                raise ReportSnapshotError(
                    "Available report sections need data and cannot have an unavailable reason."
                )
            if self.derived_from:
                if self.retrieved_at is not None:
                    raise ReportSnapshotError(
                        "Derived section retrieval time belongs to its source sections."
                    )
            elif not _is_aware(self.retrieved_at):
                raise ReportSnapshotError(
                    "Available report sections need an aware original retrieval time."
                )
        if self.coverage is not None and not isinstance(self.coverage, CoverageMetadata):
            raise ReportSnapshotError("Report section coverage metadata is invalid.")
        if self.coverage is not None and not self.coverage.complete:
            if self.status is not SectionStatus.UNAVAILABLE:
                raise ReportSnapshotError(
                    "Incomplete report section coverage cannot be marked available or cached."
                )
        if any(not _nonblank(key) for key in self.derived_from):
            raise ReportSnapshotError("Derived section source identities must be nonblank.")

    @property
    def cached(self) -> bool:
        """Indicate whether the section preserves a prior successful result."""
        return self.status is SectionStatus.CACHED


@dataclass(frozen=True)
class ReportSnapshot:
    """Capture report identity and all supported market-data sections."""

    report_date: date
    generated_at: datetime
    indices: ReportSection[tuple[IndexQuote, ...]]
    sectors: ReportSection[tuple[SectorPerformance, ...]]
    market_movers: ReportSection[MarketMoversResult]
    stocks_at_52_week_high: ReportSection[StocksAt52WeekHighResult]
    gold: ReportSection[GoldFuturesQuote]
    silver: ReportSection[SilverFuturesQuote]
    crude_oil: ReportSection[CommodityFuturesQuote]
    source_news: ReportArticleSections | None = None

    def __post_init__(self) -> None:
        """Validate report timestamps, required sections, and strict coverage."""
        if not isinstance(self.report_date, date) or isinstance(self.report_date, datetime):
            raise ReportSnapshotError("Report date must be a calendar date.")
        if not _is_aware(self.generated_at):
            raise ReportSnapshotError("Report generation timestamp must be timezone-aware.")

        sections = (
            self.indices,
            self.sectors,
            self.market_movers,
            self.stocks_at_52_week_high,
            self.gold,
            self.silver,
            self.crude_oil,
        )
        if any(not isinstance(section, ReportSection) for section in sections):
            raise ReportSnapshotError("Every required report section needs an explicit status.")
        if self.source_news is not None and not isinstance(self.source_news, ReportArticleSections):
            raise ReportSnapshotError("Optional source-listing article sections are invalid.")

        self._validate_indices()
        self._validate_sectors()
        self._validate_market_movers()
        self._validate_year_highs()
        self._validate_quote_models()

    def _validate_indices(self) -> None:
        """Require six distinct index models and matching full-coverage metadata."""
        if self.indices.status is SectionStatus.UNAVAILABLE:
            return
        values = self.indices.data
        expected = {
            "NIFTY 50",
            "NIFTY BANK",
            "NIFTY IT",
            "NIFTY MIDCAP 100",
            "NIFTY SMALLCAP 100",
            "SENSEX",
        }
        if (
            not isinstance(values, tuple)
            or len(values) != 6
            or any(not isinstance(value, IndexQuote) for value in values)
            or {value.index_name for value in values} != expected
            or self.indices.coverage != CoverageMetadata(6, 6, True)
        ):
            raise ReportSnapshotError("Available index data must contain all six supported indices.")

    def _validate_sectors(self) -> None:
        """Require all twelve sector records before accepting a heatmap section."""
        if self.sectors.status is SectionStatus.UNAVAILABLE:
            return
        values = self.sectors.data
        if (
            not isinstance(values, tuple)
            or len(values) != 12
            or any(not isinstance(value, SectorPerformance) for value in values)
            or len({value.sector_name for value in values}) != 12
            or any(value.retrieved_at != self.sectors.retrieved_at for value in values)
            or self.sectors.coverage != CoverageMetadata(12, 12, True)
        ):
            raise ReportSnapshotError("Available sector data must have complete twelve-sector coverage.")

    def _validate_market_movers(self) -> None:
        """Reject incomplete NIFTY 100 rankings at the report boundary."""
        if self.market_movers.status is SectionStatus.UNAVAILABLE:
            return
        result = self.market_movers.data
        if (
            not isinstance(result, MarketMoversResult)
            or result.universe_name != "NIFTY 100"
            or result.expected_constituent_count != 100
            or result.validated_constituent_count != 100
            or result.coverage_complete is not True
            or len(result.quotes) != 100
            or result.retrieved_at != self.market_movers.retrieved_at
            or self.market_movers.coverage != CoverageMetadata(100, 100, True)
        ):
            raise ReportSnapshotError("Available market movers require complete NIFTY 100 coverage.")

    def _validate_year_highs(self) -> None:
        """Require full NIFTY 100 coverage while allowing zero qualifiers."""
        if self.stocks_at_52_week_high.status is SectionStatus.UNAVAILABLE:
            return
        result = self.stocks_at_52_week_high.data
        if (
            not isinstance(result, StocksAt52WeekHighResult)
            or result.universe_name != "NIFTY 100"
            or result.expected_constituent_count != 100
            or result.validated_quote_count != 100
            or result.coverage_complete is not True
            or len(result.stocks) > 10
            or result.retrieved_at != self.stocks_at_52_week_high.retrieved_at
            or self.stocks_at_52_week_high.coverage != CoverageMetadata(100, 100, True)
        ):
            raise ReportSnapshotError("Available 52-week-high data requires complete NIFTY 100 coverage.")

    def _validate_quote_models(self) -> None:
        """Match available commodity sections to their source model types and times."""
        typed_sections = (
            (self.gold, GoldFuturesQuote),
            (self.silver, SilverFuturesQuote),
            (self.crude_oil, CommodityFuturesQuote),
        )
        for section, model_type in typed_sections:
            if section.status is SectionStatus.UNAVAILABLE:
                continue
            if (
                not isinstance(section.data, model_type)
                or section.data.retrieved_at != section.retrieved_at
            ):
                raise ReportSnapshotError("Available futures section data or retrieval time is invalid.")


def _is_aware(value: datetime | None) -> bool:
    """Return whether a timestamp includes a usable timezone offset."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _nonblank(value: str | None) -> bool:
    """Return whether an optional text value contains non-whitespace characters."""
    return isinstance(value, str) and bool(value.strip())


def _bounded_text(value: object, maximum_length: int) -> bool:
    """Return whether text is nonblank and within its model limit."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum_length


def _safe_https_url(value: object) -> bool:
    """Require an absolute Economic Times HTTPS URL without unsafe authority fields."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if "\\" in value or any(
        character.isspace() or unicode_category(character) == "Cc" for character in value
    ):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme.lower() == "https"
            and bool(parts.netloc)
            and bool(parts.hostname)
            and parts.hostname.lower() == "economictimes.indiatimes.com"
            and parts.username is None
            and parts.password is None
            and (parts.port is None or parts.port == 443)
        )
    except ValueError:
        return False


def _article_url_identity(value: str) -> str:
    """Normalize a validated URL for exact within-section deduplication."""
    parts = urlsplit(value)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path or '/'}?{parts.query}"
