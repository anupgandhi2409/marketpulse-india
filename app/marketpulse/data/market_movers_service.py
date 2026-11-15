import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from marketpulse.analytics.market_movers import StockChange, rank_market_movers
from marketpulse.config.settings import Settings
from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.kotak_neo import KotakNeoProvider


class MarketMoversDataError(ValueError):
    """Raised when a configured universe or its quote data fails validation."""


class MarketMoversProviderError(RuntimeError):
    """Raised when Kotak quote retrieval fails without exposing provider details."""


@dataclass(frozen=True)
class MarketUniverse:
    """Define a constituent file and expected coverage for one market universe."""

    name: str
    constituent_csv: Path
    expected_constituent_count: int

    def __post_init__(self) -> None:
        """Reject incomplete universe definitions before they are used."""
        if not self.name.strip() or self.expected_constituent_count <= 0:
            raise ValueError("A market universe needs a name and positive constituent count.")


@dataclass(frozen=True)
class Nifty50Quote:
    """Hold validated Kotak quote values for one index-universe constituent."""

    instrument_token: str
    symbol: str
    trading_symbol: str
    exchange_segment: str
    last_traded_price: Decimal
    change: Decimal
    percent_change: Decimal


@dataclass(frozen=True)
class MarketMoversResult:
    """Return complete market-mover rankings with source coverage metadata."""

    gainers: tuple[StockChange, ...]
    losers: tuple[StockChange, ...]
    universe_name: str
    expected_constituent_count: int
    validated_constituent_count: int
    coverage_complete: bool
    retrieved_at: datetime
    quotes: tuple[Nifty50Quote, ...]


_DATA_DIR = Settings().data_dir / "constituents"
NIFTY_50_UNIVERSE = MarketUniverse(
    "NIFTY 50", _DATA_DIR / "ind_nifty50list.csv", 50
)
NIFTY_100_UNIVERSE = MarketUniverse(
    "NIFTY 100", _DATA_DIR / "ind_nifty100list.csv", 100
)


class MarketMoversService:
    """Retrieve and rank a configured constituent universe through Kotak Neo."""

    BATCH_SIZE = 25
    EXCHANGE_SEGMENT = "nse_cm"
    QUOTE_TYPE = "all"

    def __init__(
        self,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
        universe: MarketUniverse = NIFTY_50_UNIVERSE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Set quote dependencies and the constituent universe to retrieve."""
        self._instrument_service = instrument_service
        self._provider = provider
        self._universe = universe
        self._clock = clock or _utc_now

    def get_market_movers(
        self,
        quote_batches: Iterable[list[tuple[Instrument, dict[str, Any]]]] | None = None,
    ) -> MarketMoversResult:
        """Rank complete quote batches, fetching them when no validated batches are supplied."""
        quotes: list[Nifty50Quote] = []
        batches = quote_batches if quote_batches is not None else self.iter_universe_quote_batches()
        for batch_rows in batches:
            quotes.extend(
                self._parse_quote(instrument, row) for instrument, row in batch_rows
            )

        expected_count = self._universe.expected_constituent_count
        if len(quotes) != expected_count:
            raise MarketMoversDataError("Quote coverage is incomplete; no ranking was produced.")

        rankings = rank_market_movers(
            [StockChange(quote.symbol, quote.percent_change) for quote in quotes],
            universe_size=expected_count,
        )
        if not rankings.is_complete:
            raise MarketMoversDataError("Quote coverage is incomplete; no ranking was produced.")

        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        retrieved_at = retrieved_at.astimezone(timezone.utc)
        return MarketMoversResult(
            gainers=rankings.gainers,
            losers=rankings.losers,
            universe_name=self._universe.name,
            expected_constituent_count=expected_count,
            validated_constituent_count=len(quotes),
            coverage_complete=True,
            retrieved_at=retrieved_at,
            quotes=tuple(quotes),
        )

    def iter_universe_quote_batches(
        self,
    ) -> Iterator[list[tuple[Instrument, dict[str, Any]]]]:
        """Resolve once and yield each fully validated quote batch in order."""
        constituents = self._load_constituents()
        instruments = self._resolve_instruments(constituents)
        for start in range(0, len(instruments), self.BATCH_SIZE):
            batch = instruments[start : start + self.BATCH_SIZE]
            rows = self._fetch_batch_rows(batch)
            yield list(zip(batch, rows, strict=True))

    def _load_constituents(self) -> list[tuple[str, str, str]]:
        """Read and validate configured symbols, series, and ISIN values."""
        try:
            with self._universe.constituent_csv.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, strict=True)
                headers = reader.fieldnames
                rows = list(reader)
        except (OSError, UnicodeError, csv.Error):
            raise MarketMoversDataError("The local constituent CSV could not be read.") from None

        expected_count = self._universe.expected_constituent_count
        if (
            not headers
            or len(headers) != len(set(headers))
            or "Symbol" not in headers
            or "Series" not in headers
            or "ISIN Code" not in headers
            or any(None in row or any(value is None for value in row.values()) for row in rows)
            or len(rows) != expected_count
        ):
            raise MarketMoversDataError("The local constituent CSV is invalid.")

        constituents: list[tuple[str, str, str]] = []
        for row in rows:
            raw_symbol = row.get("Symbol")
            raw_series = row.get("Series")
            raw_isin = row.get("ISIN Code")
            symbol = raw_symbol.strip() if isinstance(raw_symbol, str) else ""
            series = raw_series.strip() if isinstance(raw_series, str) else ""
            isin = raw_isin.strip() if isinstance(raw_isin, str) else ""
            if (
                not symbol
                or raw_symbol != symbol
                or series != "EQ"
                or raw_series != series
                or not isin
                or raw_isin != isin
            ):
                raise MarketMoversDataError("The local constituent CSV contains invalid EQ data.")
            constituents.append((symbol, series, isin))

        symbols = [symbol for symbol, _, _ in constituents]
        if len(set(symbols)) != expected_count:
            raise MarketMoversDataError("Constituent symbols must be unique and nonblank.")
        return constituents

    def _resolve_instruments(
        self,
        constituents: list[tuple[str, str, str]],
    ) -> list[Instrument]:
        """Load the cash master once and resolve every constituent exactly."""
        try:
            master = self._instrument_service.load_instruments(self.EXCHANGE_SEGMENT)
        except Exception:
            raise MarketMoversDataError("The local NSE Cash instrument master could not be loaded.") from None

        resolved: list[Instrument] = []
        used_tokens: set[str] = set()
        for symbol, series, isin in constituents:
            candidates = [
                instrument
                for instrument in master
                if instrument.symbol == symbol
                and instrument.exchange_segment == self.EXCHANGE_SEGMENT
                and instrument.series == series
            ]
            if not candidates:
                raise MarketMoversDataError("A constituent has no eligible NSE Cash EQ instrument.")
            if len(candidates) != 1:
                raise MarketMoversDataError("A constituent has ambiguous NSE Cash EQ instruments.")

            instrument = candidates[0]
            if instrument.trading_symbol != f"{symbol}-EQ":
                raise MarketMoversDataError("A constituent trading symbol does not match the instrument master.")
            if not instrument.isin or instrument.isin != isin:
                raise MarketMoversDataError("A constituent ISIN does not match the instrument master.")
            if not instrument.instrument_token.strip():
                raise MarketMoversDataError("A constituent instrument token is missing.")
            if instrument.instrument_token in used_tokens:
                raise MarketMoversDataError("Constituent instruments do not have unique tokens.")
            used_tokens.add(instrument.instrument_token)
            resolved.append(instrument)
        return resolved

    def _fetch_batch(self, instruments: list[Instrument]) -> list[Nifty50Quote]:
        """Fetch at most 25 quotes and validate exact token coverage and identity."""
        rows = self._fetch_batch_rows(instruments)
        return [self._parse_quote(instrument, row) for instrument, row in zip(instruments, rows, strict=True)]

    def _fetch_batch_rows(self, instruments: list[Instrument]) -> list[dict[str, Any]]:
        """Fetch one batch and return its rows in requested instrument order."""
        try:
            response = self._provider.get_quotes(
                instrument_tokens=[
                    {
                        "instrument_token": instrument.instrument_token,
                        "exchange_segment": self.EXCHANGE_SEGMENT,
                    }
                    for instrument in instruments
                ],
                quote_type=self.QUOTE_TYPE,
            )
        except Exception:
            raise MarketMoversProviderError("Kotak quote retrieval failed.") from None

        if not isinstance(response, list) or len(response) != len(instruments):
            raise MarketMoversDataError("Kotak quote batch did not return its exact record count.")

        by_token = {instrument.instrument_token: instrument for instrument in instruments}
        rows_by_token: dict[str, dict[str, Any]] = {}
        for row in response:
            if not isinstance(row, dict):
                raise MarketMoversDataError("Kotak quote batch contains an invalid record.")
            token_value = row.get("exchange_token")
            token = str(token_value) if token_value is not None else ""
            if token not in by_token:
                raise MarketMoversDataError("Kotak quote batch contains an unexpected instrument.")
            if token in rows_by_token:
                raise MarketMoversDataError("Kotak quote batch contains a duplicate instrument.")
            rows_by_token[token] = row

        if set(rows_by_token) != set(by_token):
            raise MarketMoversDataError("Kotak quote batch is missing an expected instrument.")

        for token, instrument in by_token.items():
            row = rows_by_token[token]
            if (
                row.get("display_symbol") != instrument.trading_symbol
                or row.get("exchange") != self.EXCHANGE_SEGMENT
            ):
                raise MarketMoversDataError(
                    "Kotak quote identity does not match the requested instrument."
                )

        # Restore configured constituent order after validating response identity.
        return [rows_by_token[instrument.instrument_token] for instrument in instruments]

    def _parse_quote(self, instrument: Instrument, row: dict[str, Any]) -> Nifty50Quote:
        """Validate quote identity and parse finite Kotak price-change decimals."""
        if (
            str(row.get("exchange_token")) != instrument.instrument_token
            or row.get("display_symbol") != instrument.trading_symbol
            or row.get("exchange") != self.EXCHANGE_SEGMENT
        ):
            raise MarketMoversDataError("Kotak quote identity does not match the requested instrument.")
        return Nifty50Quote(
            instrument_token=instrument.instrument_token,
            symbol=instrument.symbol,
            trading_symbol=instrument.trading_symbol,
            exchange_segment=self.EXCHANGE_SEGMENT,
            last_traded_price=self._required_decimal(row, "ltp"),
            change=self._required_decimal(row, "change"),
            percent_change=self._required_decimal(row, "per_change"),
        )

    @staticmethod
    def _required_decimal(row: dict[str, Any], field: str) -> Decimal:
        """Return a finite Decimal for a required quote field or raise safely."""
        value = row.get(field)
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise MarketMoversDataError("Kotak quote contains a missing or invalid numeric field.") from None
        if not parsed.is_finite():
            raise MarketMoversDataError("Kotak quote contains a missing or invalid numeric field.")
        return parsed


class Nifty50MarketMoversService(MarketMoversService):
    """Preserve the original NIFTY 50 service interface and default behavior."""

    UNIVERSE_NAME = NIFTY_50_UNIVERSE.name
    UNIVERSE_SIZE = NIFTY_50_UNIVERSE.expected_constituent_count

    def __init__(
        self,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
        constituent_path: Path | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Keep the original constructor while delegating to shared retrieval logic."""
        universe = (
            replace(NIFTY_50_UNIVERSE, constituent_csv=constituent_path)
            if constituent_path is not None
            else NIFTY_50_UNIVERSE
        )
        super().__init__(instrument_service, provider, universe, clock)


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
