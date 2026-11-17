"""Collect validated market data into a source-neutral report snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from marketpulse.config.settings import Settings
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.commodity_futures_quote_service import CommodityFuturesMasterError
from marketpulse.data.crude_oil_futures_quote_service import CrudeOilFuturesQuoteService
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.gold_futures_quote_service import GoldFuturesQuoteService
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.index_quote_service import IndexQuoteService
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.kotak_neo import KotakConfigurationError, KotakNeoProvider
from marketpulse.data.news_sources.et_acquirer import ET_FEEDS, EtRssAcquirer
from marketpulse.data.market_news import (
    MarketNewsCategory,
    MarketNewsCollectionResult,
    MarketNewsItem,
    NewsSourceStatus,
    NewsUnavailableReason,
)
from marketpulse.data.market_news_window import build_market_news_window
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversResult,
    MarketMoversService,
    NIFTY_100_UNIVERSE,
)
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import (
    SectorPerformanceDataError,
    SectorPerformanceService,
)
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.silver_futures_quote_service import SilverFuturesQuoteService
from marketpulse.data.year_high_service import (
    Nifty10052WeekHighService,
    StocksAt52WeekHighResult,
)
from marketpulse.reports.news import (
    build_report_article_section,
    build_report_article_sections,
)
from marketpulse.reports.snapshot import (
    CoverageMetadata,
    ReportArticle,
    ReportArticleCategory,
    ReportArticlePublisher,
    ReportArticleSection,
    ReportArticleSections,
    ReportArticleStatus,
    ReportSection,
    ReportSnapshot,
    SectionStatus,
)

IST = ZoneInfo("Asia/Kolkata")
INDEX_NAMES = (
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY MIDCAP 100",
    "NIFTY SMALLCAP 100",
    "SENSEX",
)
_UNAVAILABLE_REASONS = {
    "indices": "Market index quotes are unavailable or incomplete.",
    "sectors": "NSE sector quotes are unavailable or incomplete.",
    "market_movers": "NIFTY 100 quotes are unavailable or incomplete.",
    "stocks_at_52_week_high": "NIFTY 100 quotes are unavailable or incomplete.",
    "gold": "MCX Gold futures quote is unavailable.",
    "silver": "MCX Silver futures quote is unavailable.",
    "crude_oil": "MCX Crude Oil futures quote is unavailable.",
}


@dataclass(frozen=True)
class ReportDataServices:
    """Group the market services and optional Economic Times acquirer."""

    indices: IndexQuoteService
    sectors: SectorPerformanceService
    market_movers: MarketMoversService
    year_high: Nifty10052WeekHighService
    gold: GoldFuturesQuoteService
    silver: SilverFuturesQuoteService
    crude_oil: CrudeOilFuturesQuoteService
    et_news_acquirer: EtRssAcquirer | None = None


class ReportDataCollector:
    """Collect report sections independently while preserving source metadata."""

    def __init__(
        self,
        services: ReportDataServices,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Set injected market-data services and an optional timezone-aware clock."""
        self._services = services
        self._clock = clock or _utc_now

    def collect(
        self,
        report_date: date | None = None,
    ) -> ReportSnapshot:
        """Build the Morning snapshot without assigning an unverified market-session date."""
        generated_at = self._now_ist()
        if report_date is None:
            selected_date = generated_at.date()
        elif isinstance(report_date, date) and not isinstance(report_date, datetime):
            selected_date = report_date
        else:
            raise ValueError("Report date must be a calendar date.")

        indices = self._collect_indices()
        sectors = self._collect_sectors()
        movers, highs = self._collect_nifty100()
        gold = self._collect_quote("gold", self._services.gold.get_quote)
        silver = self._collect_quote("silver", self._services.silver.get_quote)
        crude_oil = self._collect_quote("crude_oil", self._services.crude_oil.get_quote)

        return ReportSnapshot(
            report_date=selected_date,
            generated_at=generated_at,
            indices=indices,
            sectors=sectors,
            market_movers=movers,
            stocks_at_52_week_high=highs,
            gold=gold,
            silver=silver,
            crude_oil=crude_oil,
            source_news=self._collect_et_news(generated_at),
        )

    def _collect_et_news(
        self,
        generated_at: datetime,
    ) -> ReportArticleSections | None:
        """Collect and map ET feeds independently into the three PDF listing sections."""
        acquirer = self._services.et_news_acquirer
        if acquirer is None:
            return None

        retrieved_at = self._now_ist()
        try:
            window_start, window_end = build_market_news_window(generated_at)
        except Exception:
            return build_report_article_sections(*(
                _unavailable_et_article_section(
                    ReportArticleCategory(config.report_section),
                    retrieved_at,
                    NewsUnavailableReason.SOURCE_ERROR,
                )
                for config in ET_FEEDS.values()
            ))
        expected_ids = {config.source_id for config in ET_FEEDS.values()}
        by_source: dict[str, MarketNewsCollectionResult] = {}
        duplicates: set[str] = set()
        try:
            results = tuple(acquirer.collect(retrieved_at))
            if any(not isinstance(result, MarketNewsCollectionResult) for result in results):
                raise ValueError("Invalid ET collection result.")
            for result in results:
                if result.source_id not in expected_ids:
                    continue
                if result.source_id in by_source:
                    duplicates.add(result.source_id)
                else:
                    by_source[result.source_id] = result
        except Exception:
            by_source = {}
            duplicates = set(expected_ids)

        sections = []
        for config in ET_FEEDS.values():
            category = ReportArticleCategory(config.report_section)
            result = by_source.get(config.source_id)
            if result is None or config.source_id in duplicates:
                section = _unavailable_et_article_section(
                    category, retrieved_at, NewsUnavailableReason.SOURCE_ERROR
                )
            elif result.status is NewsSourceStatus.UNAVAILABLE:
                section = _unavailable_et_article_section(
                    category,
                    result.retrieved_at,
                    result.unavailable_reason or NewsUnavailableReason.SOURCE_ERROR,
                )
            else:
                section = self._build_et_article_section(
                    config.source_id,
                    category,
                    config.category,
                    result,
                    window_start,
                    window_end,
                )
            sections.append(section)
        return build_report_article_sections(*sections)

    def _build_et_article_section(
        self,
        source_id: str,
        section_category: ReportArticleCategory,
        news_category: MarketNewsCategory,
        result: MarketNewsCollectionResult,
        window_start: datetime,
        window_end: datetime,
    ) -> ReportArticleSection:
        """Filter one feed by its half-open report window and preserve eligible feed order."""
        selected: list[ReportArticle] = []
        try:
            for item in result.items:
                if (
                    not isinstance(item, MarketNewsItem)
                    or item.source_id != source_id
                    or item.category is not news_category
                    or item.publisher != "The Economic Times"
                ):
                    raise ValueError("ET article provenance is invalid.")
                article = ReportArticle(
                    headline=item.headline,
                    article_url=item.article_url,
                    publisher=ReportArticlePublisher.ECONOMIC_TIMES,
                    source_category=section_category,
                    published_at=item.published_at,
                    retrieved_at=item.retrieved_at,
                    article_identity=item.source_item_id,
                )
                if item.published_at is None:
                    continue
                elif window_start <= item.published_at < window_end:
                    selected.append(article)
            return build_report_article_section(
                section_category,
                ReportArticleStatus.AVAILABLE,
                selected,
                retrieved_at=result.retrieved_at,
            )
        except Exception:
            return _unavailable_et_article_section(
                section_category,
                result.retrieved_at,
                NewsUnavailableReason.INVALID_RESPONSE,
            )

    def _collect_indices(self) -> ReportSection[tuple[IndexQuote, ...]]:
        """Collect the six supported indices and record their local retrieval time."""
        try:
            result = tuple(self._services.indices.get_quotes(list(INDEX_NAMES)))
            retrieved_at = self._now_ist()
            if (
                len(result) != 6
                or any(not isinstance(item, IndexQuote) for item in result)
                or {item.index_name for item in result} != set(INDEX_NAMES)
                or len({item.index_name for item in result}) != 6
            ):
                raise ValueError("Incomplete index data.")
            return ReportSection(
                status=SectionStatus.AVAILABLE,
                data=result,
                source=result[0].source,
                retrieved_at=retrieved_at,
                coverage=CoverageMetadata(6, 6, True),
            )
        except Exception as error:
            return _unavailable("indices", error)

    def _collect_sectors(self) -> ReportSection[tuple[SectorPerformance, ...]]:
        """Collect all twelve validated sectors with their service retrieval time."""
        try:
            result = tuple(self._services.sectors.get_sector_performance())
            if (
                len(result) != 12
                or any(not isinstance(item, SectorPerformance) for item in result)
                or len({item.sector_name for item in result}) != 12
                or any(not _aware(item.retrieved_at) for item in result)
                or any(item.retrieved_at != result[0].retrieved_at for item in result)
            ):
                raise SectorPerformanceDataError("Incomplete sector coverage.")
            return ReportSection(
                status=SectionStatus.AVAILABLE,
                data=result,
                source=result[0].source,
                retrieved_at=result[0].retrieved_at,
                coverage=CoverageMetadata(12, 12, True),
            )
        except Exception as error:
            return _unavailable("sectors", error)

    def _collect_nifty100(
        self,
    ) -> tuple[ReportSection[MarketMoversResult], ReportSection[StocksAt52WeekHighResult]]:
        """Fetch shared NIFTY 100 quote batches once for both analytical results."""
        try:
            quote_batches = tuple(self._services.market_movers.iter_universe_quote_batches())
        except Exception as error:
            return (
                _unavailable("market_movers", error),
                _unavailable("stocks_at_52_week_high", error),
            )

        try:
            movers_result = self._services.market_movers.get_market_movers(quote_batches)
            if (
                not isinstance(movers_result, MarketMoversResult)
                or movers_result.universe_name != NIFTY_100_UNIVERSE.name
                or movers_result.expected_constituent_count != NIFTY_100_UNIVERSE.expected_constituent_count
                or movers_result.validated_constituent_count != NIFTY_100_UNIVERSE.expected_constituent_count
                or movers_result.coverage_complete is not True
                or len(movers_result.quotes) != NIFTY_100_UNIVERSE.expected_constituent_count
                or not _aware(movers_result.retrieved_at)
            ):
                raise MarketMoversDataError("Incomplete NIFTY 100 coverage.")
            movers = ReportSection(
                status=SectionStatus.AVAILABLE,
                data=movers_result,
                source="Kotak Neo",
                retrieved_at=movers_result.retrieved_at,
                coverage=CoverageMetadata(100, 100, True),
            )
        except Exception as error:
            movers = _unavailable("market_movers", error)

        try:
            highs_result = self._services.year_high.get_stocks_at_52_week_high(quote_batches)
            if (
                not isinstance(highs_result, StocksAt52WeekHighResult)
                or highs_result.universe_name != NIFTY_100_UNIVERSE.name
                or highs_result.expected_constituent_count != NIFTY_100_UNIVERSE.expected_constituent_count
                or highs_result.validated_quote_count != NIFTY_100_UNIVERSE.expected_constituent_count
                or highs_result.coverage_complete is not True
                or len(highs_result.stocks) > 10
                or not _aware(highs_result.retrieved_at)
            ):
                raise MarketMoversDataError("Incomplete NIFTY 100 coverage.")
            highs = ReportSection(
                status=SectionStatus.AVAILABLE,
                data=highs_result,
                source="Kotak Neo",
                retrieved_at=highs_result.retrieved_at,
                coverage=CoverageMetadata(100, 100, True),
            )
        except Exception as error:
            highs = _unavailable("stocks_at_52_week_high", error)
        return movers, highs

    def _collect_quote(self, section: str, get_quote: Callable[[], object]) -> ReportSection:
        """Collect one futures quote and retain its own source and retrieval time."""
        try:
            quote = get_quote()
            expected_type = {
                "gold": GoldFuturesQuote,
                "silver": SilverFuturesQuote,
                "crude_oil": CommodityFuturesQuote,
            }[section]
            retrieved_at = getattr(quote, "retrieved_at", None)
            source = getattr(quote, "source", None)
            if (
                not isinstance(quote, expected_type)
                or not _aware(retrieved_at)
                or not isinstance(source, str)
                or not source.strip()
            ):
                raise ValueError("Invalid quote provenance.")
            return ReportSection(
                status=SectionStatus.AVAILABLE,
                data=quote,
                source=source,
                retrieved_at=retrieved_at,
            )
        except Exception as error:
            return _unavailable(section, error)

    def _now_ist(self) -> datetime:
        """Return the injected aware clock value normalized to India Standard Time."""
        value = self._clock()
        if not _aware(value):
            raise ValueError("Report clock must return a timezone-aware datetime.")
        return value.astimezone(IST)


def create_report_data_collector(
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    *,
    et_news_acquirer: EtRssAcquirer | None = None,
) -> ReportDataCollector:
    """Build market services and an ET acquirer without opening a network connection."""
    provider = KotakNeoProvider(settings=settings)
    store = InstrumentMasterStore(settings=settings)
    instrument_service = InstrumentMasterService(store, InstrumentMasterParser())
    services = ReportDataServices(
        indices=IndexQuoteService(provider),
        sectors=SectorPerformanceService(provider),
        market_movers=MarketMoversService(
            instrument_service, provider, universe=NIFTY_100_UNIVERSE
        ),
        year_high=Nifty10052WeekHighService(instrument_service, provider),
        gold=GoldFuturesQuoteService(store, instrument_service, provider),
        silver=SilverFuturesQuoteService(store, instrument_service, provider),
        crude_oil=CrudeOilFuturesQuoteService(store, instrument_service, provider),
        et_news_acquirer=et_news_acquirer if et_news_acquirer is not None else EtRssAcquirer(),
    )
    return ReportDataCollector(services, clock=clock)


def _unavailable(section: str, error: Exception) -> ReportSection:
    """Map internal failures to a fixed safe reason for one report section."""
    if isinstance(error, KotakConfigurationError):
        reason = "Kotak Neo is not configured."
    elif isinstance(error, (FileNotFoundError, CommodityFuturesMasterError)):
        reason = "The required local instrument master is missing or invalid."
    elif isinstance(error, MarketMoversDataError):
        reason = "NIFTY 100 constituent data or complete quote coverage is invalid."
    elif isinstance(error, SectorPerformanceDataError):
        reason = "NSE sector quote coverage or values are invalid."
    elif isinstance(error, ValueError) and section == "indices":
        reason = _UNAVAILABLE_REASONS[section]
    else:
        reason = _UNAVAILABLE_REASONS[section]
    return ReportSection(
        status=SectionStatus.UNAVAILABLE,
        unavailable_reason=reason,
    )


def _unavailable_et_article_section(
    category: ReportArticleCategory,
    retrieved_at: datetime,
    reason: NewsUnavailableReason,
) -> ReportArticleSection:
    """Build a fixed unavailable result for one ET listing without exposing details."""
    return build_report_article_section(
        category,
        ReportArticleStatus.UNAVAILABLE,
        retrieved_at=retrieved_at,
        unavailable_reason=reason,
    )


def _aware(value: object) -> bool:
    """Return whether a value is a datetime with a usable timezone offset."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for report generation."""
    return datetime.now(timezone.utc)
