from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.kotak_neo import KotakNeoProvider
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversService,
    NIFTY_100_UNIVERSE,
)


@dataclass(frozen=True)
class StockAt52WeekHigh:
    """Hold a source-neutral stock price and its reported 52-week high."""

    symbol: str
    last_traded_price: Decimal
    year_high: Decimal
    day_high: Decimal | None = None


@dataclass(frozen=True)
class StocksAt52WeekHighResult:
    """Return qualifying stocks with complete-universe retrieval metadata."""

    universe_name: str
    expected_constituent_count: int
    validated_quote_count: int
    coverage_complete: bool
    retrieved_at: datetime
    stocks: tuple[StockAt52WeekHigh, ...]


class Nifty10052WeekHighService:
    """Find up to ten NIFTY 100 stocks at or above their reported 52-week high."""

    MAX_RESULTS = 10

    def __init__(
        self,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Reuse configured NIFTY 100 quote resolution and batching dependencies."""
        self._quote_service = MarketMoversService(
            instrument_service,
            provider,
            universe=NIFTY_100_UNIVERSE,
            clock=clock,
        )
        self._clock = clock or _utc_now

    def get_stocks_at_52_week_high(
        self,
        quote_batches: Iterable[list[tuple[Instrument, dict[str, Any]]]] | None = None,
    ) -> StocksAt52WeekHighResult:
        """Validate NIFTY 100 quote batches and return only stocks meeting the high rule."""
        expected_count = NIFTY_100_UNIVERSE.expected_constituent_count
        qualifying: list[StockAt52WeekHigh] = []
        validated_count = 0
        batches = (
            quote_batches
            if quote_batches is not None
            else self._quote_service.iter_universe_quote_batches()
        )
        for batch_rows in batches:
            for instrument, row in batch_rows:
                if (
                    not isinstance(instrument, Instrument)
                    or not isinstance(row, dict)
                    or instrument.exchange_segment != "nse_cm"
                    or str(row.get("exchange_token")) != instrument.instrument_token
                    or row.get("display_symbol") != instrument.trading_symbol
                    or row.get("exchange") != "nse_cm"
                ):
                    raise MarketMoversDataError(
                        "Kotak quote identity does not match the requested instrument."
                    )
                ltp = self._positive_decimal(row.get("ltp"), "ltp")
                year_high = self._positive_decimal(row.get("year_high"), "year_high")
                ohlc = row.get("ohlc")
                day_high = self._optional_positive_decimal(
                    ohlc.get("high") if isinstance(ohlc, dict) else None
                )
                validated_count += 1
                if ltp >= year_high:
                    qualifying.append(
                        StockAt52WeekHigh(
                            symbol=instrument.symbol,
                            last_traded_price=ltp,
                            year_high=year_high,
                            day_high=day_high,
                        )
                    )

        if validated_count != expected_count:
            raise MarketMoversDataError("Quote coverage is incomplete; no result was produced.")

        qualifying.sort(key=lambda stock: stock.symbol)
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        return StocksAt52WeekHighResult(
            universe_name=NIFTY_100_UNIVERSE.name,
            expected_constituent_count=expected_count,
            validated_quote_count=validated_count,
            coverage_complete=True,
            retrieved_at=retrieved_at.astimezone(timezone.utc),
            stocks=tuple(qualifying[: self.MAX_RESULTS]),
        )

    @staticmethod
    def _positive_decimal(value: object, field: str) -> Decimal:
        """Parse a required positive finite Decimal quote field safely."""
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise MarketMoversDataError(
                f"Kotak quote has a missing or invalid {field} field."
            ) from None
        if not parsed.is_finite() or parsed <= 0:
            raise MarketMoversDataError(f"Kotak quote has a missing or invalid {field} field.")
        return parsed

    @staticmethod
    def _optional_positive_decimal(value: object) -> Decimal | None:
        """Return a valid positive daily high when the optional field is available."""
        if value is None or value == "":
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() and parsed > 0 else None


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC retrieval timestamp."""
    return datetime.now(timezone.utc)
