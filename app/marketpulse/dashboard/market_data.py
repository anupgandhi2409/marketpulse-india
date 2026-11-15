from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from marketpulse.analytics.currency_conversion import (
    CurrencyConversionError,
    convert_crudeoil_inr_to_indicative_usd,
)
from marketpulse.config.settings import Settings
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.commodity_futures_quote_service import CommodityFuturesMasterError
from marketpulse.data.currency_futures_quote import CurrencyFuturesQuote
from marketpulse.data.crude_oil_futures_quote_service import CrudeOilFuturesQuoteService
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.gold_futures_quote_service import GoldFuturesQuoteService
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.index_quote_service import IndexQuoteService
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.kotak_neo import KotakConfigurationError, KotakNeoProvider
from marketpulse.analytics.market_movers import StockChange
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversResult,
    MarketMoversService,
    NIFTY_100_UNIVERSE,
    Nifty50Quote,
)
from marketpulse.data.year_high_service import (
    Nifty10052WeekHighService,
    StockAt52WeekHigh,
    StocksAt52WeekHighResult,
)
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.silver_futures_quote_service import SilverFuturesQuoteService
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import (
    SectorPerformanceDataError,
    SectorPerformanceService,
)
from marketpulse.data.usdinr_futures_quote_service import (
    UsdInrFuturesQuoteService,
    UsdInrMasterError,
)

IST = ZoneInfo("Asia/Kolkata")

INDEX_WATCHLIST = (
    ("NIFTY 50", "NSE"),
    ("NIFTY BANK", "NSE"),
    ("NIFTY IT", "NSE"),
    ("NIFTY MIDCAP 100", "NSE"),
    ("NIFTY SMALLCAP 100", "NSE"),
    ("SENSEX", "BSE"),
)
INDEX_NAMES = tuple(name for name, _ in INDEX_WATCHLIST)
@dataclass(frozen=True)
class DashboardMarketData:
    """Hold the last successful dashboard quotes with their original retrieval times."""

    index_quotes: tuple[IndexQuote, ...] = ()
    index_retrieved_at: datetime | None = None
    gold_quote: GoldFuturesQuote | None = None
    silver_quote: SilverFuturesQuote | None = None
    usdinr_quote: CurrencyFuturesQuote | None = None
    crude_oil_quote: CommodityFuturesQuote | None = None
    sector_performance: tuple[SectorPerformance, ...] = ()
    market_movers: MarketMoversResult | None = None
    stocks_at_52_week_high: StocksAt52WeekHighResult | None = None


@dataclass(frozen=True)
class RefreshOutcome:
    """Return retained market data and safe messages from a refresh attempt."""

    market_data: DashboardMarketData
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardServices:
    """Group dashboard quote services built with shared provider dependencies."""

    indices: IndexQuoteService
    gold: GoldFuturesQuoteService
    silver: SilverFuturesQuoteService
    usdinr: UsdInrFuturesQuoteService
    crude_oil: CrudeOilFuturesQuoteService
    sectors: SectorPerformanceService
    market_movers: MarketMoversService
    year_high: Nifty10052WeekHighService


@dataclass(frozen=True)
class CommodityMarketCard:
    """Provide formatted data and cache state for one dashboard commodity card."""

    section: str
    title: str
    subtitle: str
    price_label: str
    price: str
    change: str
    contract_symbol: str
    retrieved_at: datetime | None
    cached: bool
    error: str | None = None
    usd_equivalent: str | None = None
    usd_note: str | None = None
    usd_rate_contract_symbol: str | None = None
    usd_rate_retrieved_at: datetime | None = None
    usd_rate_cached: bool = False


@dataclass(frozen=True)
class SectorHeatmapTile:
    """Provide validated and formatted values for one ordered sector tile."""

    sector_name: str
    index_value: str
    percent_change: str
    direction: str
    retrieved_at: datetime
    cached: bool


@dataclass(frozen=True)
class MarketMoverTableRow:
    """Provide a formatted ranking row for the dashboard's movers tables."""

    rank: int
    symbol: str
    percent_change: str
    last_traded_price: str
    direction: str


@dataclass(frozen=True)
class MarketMoversTables:
    """Hold validated NIFTY 100 rankings with timestamp and cache status."""

    gainers: tuple[MarketMoverTableRow, ...]
    losers: tuple[MarketMoverTableRow, ...]
    retrieved_at: datetime
    cached: bool


@dataclass(frozen=True)
class YearHighTableRow:
    """Provide a formatted row for one qualified stock at its 52-week high."""

    rank: int
    symbol: str
    last_traded_price: str
    year_high: str


@dataclass(frozen=True)
class YearHighTable:
    """Hold a complete 52-week-high scan, including a successful empty result."""

    rows: tuple[YearHighTableRow, ...]
    retrieved_at: datetime
    cached: bool


def create_dashboard_services(settings: Settings | None = None) -> DashboardServices:
    """Build all read-only dashboard services with one shared provider and master service."""
    provider = KotakNeoProvider(settings=settings)
    store = InstrumentMasterStore(settings=settings)
    instrument_service = InstrumentMasterService(store, InstrumentMasterParser())
    return DashboardServices(
        indices=IndexQuoteService(provider),
        gold=GoldFuturesQuoteService(store, instrument_service, provider),
        silver=SilverFuturesQuoteService(store, instrument_service, provider),
        usdinr=UsdInrFuturesQuoteService(store, instrument_service, provider),
        crude_oil=CrudeOilFuturesQuoteService(store, instrument_service, provider),
        sectors=SectorPerformanceService(provider),
        market_movers=MarketMoversService(
            instrument_service, provider, universe=NIFTY_100_UNIVERSE
        ),
        year_high=Nifty10052WeekHighService(instrument_service, provider),
    )
def refresh_market_data(
    index_service: IndexQuoteService,
    previous: DashboardMarketData | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RefreshOutcome:
    """Refresh index quotes and retain their last successful snapshot on failure."""
    market_data = previous or DashboardMarketData()
    now = clock or _local_now
    errors: list[str] = []

    try:
        index_quotes = tuple(index_service.get_quotes(list(INDEX_NAMES)))
    except Exception as exc:
        errors.append(safe_error_message(exc, "indices"))
    else:
        market_data = replace(
            market_data,
            index_quotes=index_quotes,
            index_retrieved_at=now().astimezone(),
        )

    return RefreshOutcome(market_data=market_data, errors=tuple(errors))


def refresh_dashboard_data(
    services: DashboardServices,
    previous: DashboardMarketData | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RefreshOutcome:
    """Refresh each dataset independently and retain cached successes on failures."""
    outcome = refresh_market_data(
        services.indices,
        previous=previous,
        clock=clock,
    )
    market_data = outcome.market_data
    errors = list(outcome.errors)
    quote_services = (
        ("gold", services.gold, "gold_quote"),
        ("silver", services.silver, "silver_quote"),
        ("usdinr", services.usdinr, "usdinr_quote"),
        ("crude_oil", services.crude_oil, "crude_oil_quote"),
    )
    for section, service, field in quote_services:
        try:
            quote = service.get_quote()
        except Exception as exc:
            errors.append(safe_error_message(exc, section))
        else:
            market_data = replace(market_data, **{field: quote})
    try:
        sector_quotes = tuple(services.sectors.get_sector_performance())
        _validate_sector_snapshot(sector_quotes)
    except Exception as exc:
        errors.append(safe_error_message(exc, "sectors"))
    else:
        market_data = replace(market_data, sector_performance=sector_quotes)
    try:
        quote_batches = tuple(services.market_movers.iter_universe_quote_batches())
    except Exception as exc:
        errors.append(safe_error_message(exc, "market_movers"))
        errors.append(safe_error_message(exc, "year_high"))
    else:
        try:
            market_movers = services.market_movers.get_market_movers(quote_batches)
            _validate_market_movers_result(market_movers)
        except Exception as exc:
            errors.append(safe_error_message(exc, "market_movers"))
        else:
            market_data = replace(market_data, market_movers=market_movers)
        try:
            year_high_result = services.year_high.get_stocks_at_52_week_high(quote_batches)
            _validate_year_high_result(year_high_result)
        except Exception as exc:
            errors.append(safe_error_message(exc, "year_high"))
        else:
            market_data = replace(market_data, stocks_at_52_week_high=year_high_result)
    return RefreshOutcome(market_data=market_data, errors=tuple(errors))


def build_year_high_table(
    result: StocksAt52WeekHighResult | None,
    errors: tuple[str, ...] = (),
) -> YearHighTable | None:
    """Format a complete NIFTY 100 result, preserving its qualification order."""
    try:
        _validate_year_high_result(result)
    except MarketMoversDataError:
        return None
    assert result is not None
    cached = any(message.startswith(_ERROR_PREFIX["year_high"]) for message in errors)
    return YearHighTable(
        rows=tuple(
            YearHighTableRow(
                rank=index,
                symbol=stock.symbol,
                last_traded_price=str(stock.last_traded_price),
                year_high=str(stock.year_high),
            )
            for index, stock in enumerate(result.stocks, start=1)
        ),
        retrieved_at=result.retrieved_at,
        cached=cached,
    )


def _validate_year_high_result(result: StocksAt52WeekHighResult | None) -> None:
    """Require complete NIFTY 100 coverage and only valid, qualified result rows."""
    expected = NIFTY_100_UNIVERSE.expected_constituent_count
    if (
        not isinstance(result, StocksAt52WeekHighResult)
        or result.universe_name != NIFTY_100_UNIVERSE.name
        or result.expected_constituent_count != expected
        or result.validated_quote_count != expected
        or result.coverage_complete is not True
        or not isinstance(result.retrieved_at, datetime)
        or result.retrieved_at.tzinfo is None
        or result.retrieved_at.utcoffset() is None
        or not isinstance(result.stocks, tuple)
        or len(result.stocks) > Nifty10052WeekHighService.MAX_RESULTS
    ):
        raise MarketMoversDataError("NIFTY 100 52-week-high scan is incomplete or invalid.")

    symbols: list[str] = []
    for stock in result.stocks:
        if (
            not isinstance(stock, StockAt52WeekHigh)
            or not isinstance(stock.symbol, str)
            or not stock.symbol.strip()
            or not _is_finite_decimal(stock.last_traded_price)
            or stock.last_traded_price <= 0
            or not _is_finite_decimal(stock.year_high)
            or stock.year_high <= 0
            or stock.last_traded_price < stock.year_high
        ):
            raise MarketMoversDataError("NIFTY 100 52-week-high result contains invalid data.")
        symbols.append(stock.symbol)
    if len(symbols) != len(set(symbols)) or symbols != sorted(symbols):
        raise MarketMoversDataError("NIFTY 100 52-week-high result order is invalid.")


def build_market_movers_tables(
    result: MarketMoversResult | None,
    errors: tuple[str, ...] = (),
) -> MarketMoversTables | None:
    """Format complete NIFTY 100 rankings without changing the service's order."""
    try:
        _validate_market_movers_result(result)
    except MarketMoversDataError:
        return None
    assert result is not None
    quotes_by_symbol = {quote.symbol: quote for quote in result.quotes}
    cached = any(message.startswith(_ERROR_PREFIX["market_movers"]) for message in errors)
    return MarketMoversTables(
        gainers=tuple(
            _market_mover_row(index, item, quotes_by_symbol[item.symbol])
            for index, item in enumerate(result.gainers, start=1)
        ),
        losers=tuple(
            _market_mover_row(index, item, quotes_by_symbol[item.symbol])
            for index, item in enumerate(result.losers, start=1)
        ),
        retrieved_at=result.retrieved_at,
        cached=cached,
    )


def _market_mover_row(rank: int, change: StockChange, quote: Nifty50Quote) -> MarketMoverTableRow:
    """Format a validated mover with its service-provided percentage and LTP."""
    value = change.percent_change
    return MarketMoverTableRow(
        rank=rank,
        symbol=change.symbol,
        percent_change=f"+{value}%" if value > 0 else f"{value}%",
        last_traded_price=str(quote.last_traded_price),
        direction="positive" if value > 0 else "negative" if value < 0 else "neutral",
    )


def _validate_market_movers_result(result: MarketMoversResult | None) -> None:
    """Reject non-NIFTY-100, incomplete, malformed, or inconsistently ranked results."""
    expected = NIFTY_100_UNIVERSE.expected_constituent_count
    if (
        not isinstance(result, MarketMoversResult)
        or result.universe_name != NIFTY_100_UNIVERSE.name
        or result.expected_constituent_count != expected
        or result.validated_constituent_count != expected
        or result.coverage_complete is not True
        or not isinstance(result.retrieved_at, datetime)
        or result.retrieved_at.tzinfo is None
        or result.retrieved_at.utcoffset() is None
        or not isinstance(result.quotes, tuple)
        or not isinstance(result.gainers, tuple)
        or not isinstance(result.losers, tuple)
        or len(result.quotes) != expected
        or len(result.gainers) != 10
        or len(result.losers) != 10
    ):
        raise MarketMoversDataError("NIFTY 100 ranking is unavailable or incomplete.")

    quotes_by_symbol: dict[str, Nifty50Quote] = {}
    tokens: set[str] = set()
    for quote in result.quotes:
        if (
            not isinstance(quote, Nifty50Quote)
            or not isinstance(quote.symbol, str)
            or not quote.symbol.strip()
            or not isinstance(quote.instrument_token, str)
            or not quote.instrument_token.strip()
            or quote.exchange_segment != "nse_cm"
            or quote.instrument_token in tokens
            or quote.symbol in quotes_by_symbol
            or not _is_finite_decimal(quote.last_traded_price)
            or quote.last_traded_price <= 0
            or not _is_finite_decimal(quote.change)
            or not _is_finite_decimal(quote.percent_change)
        ):
            raise MarketMoversDataError("NIFTY 100 quote data is malformed.")
        tokens.add(quote.instrument_token)
        quotes_by_symbol[quote.symbol] = quote
    if len(quotes_by_symbol) != expected:
        raise MarketMoversDataError("NIFTY 100 quote coverage is incomplete.")

    for ranking, descending in ((result.gainers, True), (result.losers, False)):
        previous_key: tuple[Decimal, str] | None = None
        seen_symbols: set[str] = set()
        for item in ranking:
            quote = (
                quotes_by_symbol.get(item.symbol)
                if isinstance(item, StockChange) and isinstance(item.symbol, str)
                else None
            )
            if (
                quote is None
                or item.symbol in seen_symbols
                or not _is_finite_decimal(item.percent_change)
                or item.percent_change != quote.percent_change
            ):
                raise MarketMoversDataError("NIFTY 100 ranking does not match its quotes.")
            key = (-item.percent_change if descending else item.percent_change, item.symbol)
            if previous_key is not None and key < previous_key:
                raise MarketMoversDataError("NIFTY 100 ranking order is invalid.")
            seen_symbols.add(item.symbol)
            previous_key = key


def _is_finite_decimal(value: object) -> bool:
    """Return whether a value is a finite Decimal used by the market-movers result."""
    return isinstance(value, Decimal) and value.is_finite()


def build_sector_heatmap_tiles(
    sector_quotes: tuple[SectorPerformance, ...],
    errors: tuple[str, ...] = (),
) -> tuple[SectorHeatmapTile, ...]:
    """Format a complete sector snapshot in service order, including cache status."""
    try:
        _validate_sector_snapshot(sector_quotes)
    except SectorPerformanceDataError:
        return ()
    cached = any(message.startswith(_ERROR_PREFIX["sectors"]) for message in errors)
    return tuple(
        SectorHeatmapTile(
            sector_name=item.sector_name,
            index_value=str(item.index_value),
            percent_change=_format_sector_change(item.percent_change),
            direction=("positive" if item.percent_change > 0 else "negative" if item.percent_change < 0 else "neutral"),
            retrieved_at=item.retrieved_at,
            cached=cached,
        )
        for item in sector_quotes
    )


def _format_sector_change(value: Decimal) -> str:
    """Format a signed sector percentage while retaining zero as neutral text."""
    if value > 0:
        return f"+{value}%"
    return f"{value}%"


def _validate_sector_snapshot(sector_quotes: tuple[SectorPerformance, ...]) -> None:
    """Require all service-defined sectors in order with finite market values and times."""
    expected_names = tuple(name for name, _ in SectorPerformanceService.SECTORS)
    if len(sector_quotes) != len(expected_names):
        raise SectorPerformanceDataError("Sector quote response is incomplete.")
    for item, expected_name in zip(sector_quotes, expected_names, strict=True):
        if (
            not isinstance(item, SectorPerformance)
            or item.sector_name != expected_name
            or not isinstance(item.index_value, Decimal)
            or not item.index_value.is_finite()
            or item.index_value <= 0
            or not isinstance(item.percent_change, Decimal)
            or not item.percent_change.is_finite()
            or not isinstance(item.retrieved_at, datetime)
            or item.retrieved_at.tzinfo is None
            or item.retrieved_at.utcoffset() is None
        ):
            raise SectorPerformanceDataError("Sector quote response is incomplete or invalid.")


def build_commodity_cards(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> tuple[CommodityMarketCard, ...]:
    """Format four commodity and currency quote cards without Streamlit dependencies."""
    definitions = (
        ("gold", "Gold", "MCX Gold Futures", "Price", market_data.gold_quote),
        ("silver", "Silver", "MCX Silver Futures", "Price", market_data.silver_quote),
        ("usdinr", "USD/INR", "USD/INR Currency Futures", "Rate", market_data.usdinr_quote),
        ("crude_oil", "Crude Oil", "MCX Crude Oil Futures", "Price", market_data.crude_oil_quote),
    )
    cards: list[CommodityMarketCard] = []
    for section, title, subtitle, price_label, quote in definitions:
        error = next((message for message in errors if message.startswith(_ERROR_PREFIX[section])), None)
        usd_equivalent = None
        usd_note = None
        usd_rate_symbol = None
        usd_rate_retrieved_at = None
        usd_rate_cached = False
        if section == "crude_oil":
            usd_note = "Indicative conversion; market-data freshness not independently verified. Basis: USD/INR currency futures."
            usdinr_quote = market_data.usdinr_quote
            usd_rate_cached = any(
                message.startswith(_ERROR_PREFIX["usdinr"]) for message in errors
            )
            if usdinr_quote is not None:
                usd_rate_symbol = usdinr_quote.contract_trading_symbol
                usd_rate_retrieved_at = usdinr_quote.retrieved_at
            if quote is not None and usdinr_quote is not None:
                try:
                    conversion = convert_crudeoil_inr_to_indicative_usd(
                        quote.price,
                        usdinr_quote.price,
                        rate_source=usdinr_quote.source,
                        rate_instrument_type=usdinr_quote.instrument_type,
                    )
                except CurrencyConversionError:
                    usd_equivalent = "Unavailable"
                else:
                    usd_equivalent = f"{conversion.usd_per_barrel} USD/barrel"
            else:
                usd_equivalent = "Unavailable"
        if quote is None:
            cards.append(
                CommodityMarketCard(
                    section=section,
                    title=title,
                    subtitle=subtitle,
                    price_label=price_label,
                    price="Unavailable",
                    change="Unavailable",
                    contract_symbol="Unavailable",
                    retrieved_at=None,
                    cached=False,
                    error=error or "No successful retrieval yet.",
                    usd_equivalent=usd_equivalent,
                    usd_note=usd_note,
                    usd_rate_contract_symbol=usd_rate_symbol,
                    usd_rate_retrieved_at=usd_rate_retrieved_at,
                    usd_rate_cached=usd_rate_cached,
                )
            )
            continue

        cards.append(
            CommodityMarketCard(
                section=section,
                title=title,
                subtitle=subtitle,
                price_label=price_label,
                price=f"{quote.price} {quote.quotation_unit}",
                change=f"{quote.absolute_change} ({quote.percentage_change}%)",
                contract_symbol=quote.contract_trading_symbol,
                retrieved_at=quote.retrieved_at,
                cached=error is not None,
                error=error,
                usd_equivalent=usd_equivalent,
                usd_note=usd_note,
                usd_rate_contract_symbol=usd_rate_symbol,
                usd_rate_retrieved_at=usd_rate_retrieved_at,
                usd_rate_cached=usd_rate_cached,
            )
        )
    return tuple(cards)


def format_retrieved_at(value: datetime | None) -> str:
    """Format an aware retrieval timestamp in IST without implying exchange freshness."""
    if value is None:
        return "Unavailable"
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def safe_error_message(error: Exception, section: str) -> str:
    """Map internal failures to fixed UI messages without exposing exception details."""
    if isinstance(error, KotakConfigurationError):
        return "Kotak Neo is not configured. Check the local environment settings."
    if section == "market_movers" and isinstance(error, MarketMoversDataError):
        return "NIFTY 100 market movers unavailable; constituent data or quote coverage is invalid."
    if section == "year_high" and isinstance(error, MarketMoversDataError):
        return "NIFTY 100 52-week-high scan unavailable; constituent data or quote coverage is invalid."
    if section in _ERROR_PREFIX:
        if isinstance(error, (CommodityFuturesMasterError, UsdInrMasterError, FileNotFoundError)):
            return f"{_ERROR_PREFIX[section]}: local instrument master is unavailable or invalid."
        return f"{_ERROR_PREFIX[section]}: quote refresh failed."
    if section == "market":
        return "Market data could not be refreshed; any previous successful data is retained."
    if section == "indices":
        return "Index quotes could not be refreshed; any previous successful data is retained."
    return "Market data could not be refreshed; any previous successful data is retained."


def _local_now() -> datetime:
    """Return the current local time with timezone information."""
    return datetime.now().astimezone()


_ERROR_PREFIX = {
    "gold": "Gold futures",
    "silver": "Silver futures",
    "usdinr": "USD/INR futures",
    "crude_oil": "Crude Oil futures",
    "sectors": "NSE sector heatmap",
    "market_movers": "NIFTY 100 market movers",
    "year_high": "NIFTY 100 52-week-high scan",
}
