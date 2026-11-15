import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.kotak_neo import KotakNeoProvider


class CommodityFuturesMasterError(ValueError):
    """Raised when the local commodity master cannot resolve an active future."""


class CommodityFuturesDataError(ValueError):
    """Raised when the selected future or its quote fails validation."""


class CommodityFuturesProviderError(RuntimeError):
    """Raised when Kotak quote retrieval fails without exposing provider details."""


class CommodityFuturesQuoteService:
    """Share contract selection and strict quote validation for MCX futures."""

    EXCHANGE_SEGMENT = "mcx_fo"
    QUOTE_TYPE = "all"
    SOURCE = "Kotak Neo"
    COMMODITY = ""
    QUOTATION_UNIT = ""
    QUOTE_MODEL = CommodityFuturesQuote
    INSTRUMENT_TYPE = "FUTCOM"

    def __init__(
        self,
        store: InstrumentMasterStore,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Set local master, parsed instrument service, quote provider, and clock."""
        self._store = store
        self._instrument_service = instrument_service
        self._provider = provider
        self._clock = clock or _utc_now

    def get_quote(self) -> CommodityFuturesQuote:
        """Select the nearest unexpired configured future and retrieve its quote."""
        now = self._retrieval_time()
        contract, expiry = self._select_contract(now)
        try:
            response = self._provider.get_quotes(
                instrument_tokens=[{
                    "exchange_segment": self.EXCHANGE_SEGMENT,
                    "instrument_token": contract.instrument_token,
                }],
                quote_type=self.QUOTE_TYPE,
            )
        except Exception as exc:
            raise CommodityFuturesProviderError(
                f"Kotak Neo {self.COMMODITY} quote request failed ({type(exc).__name__})."
            ) from exc

        row = self._quote_row(response, contract.instrument_token)
        if row.get("exchange") != self.EXCHANGE_SEGMENT:
            raise CommodityFuturesDataError("Kotak quote exchange does not match the selected contract.")
        if row.get("exchange_token") != contract.instrument_token:
            raise CommodityFuturesDataError("Kotak quote token does not match the selected contract.")

        price = _required_decimal(row.get("ltp"), "ltp", positive=True)
        change = _required_decimal(row.get("change"), "change")
        percent_change = _required_decimal(row.get("per_change"), "per_change")
        ohlc = row.get("ohlc")
        if not isinstance(ohlc, dict):
            ohlc = {}

        return self.QUOTE_MODEL(
            commodity=self.COMMODITY,
            exchange="MCX",
            contract_trading_symbol=contract.trading_symbol,
            instrument_token=contract.instrument_token,
            price=price,
            quotation_unit=self.QUOTATION_UNIT,
            absolute_change=change,
            percentage_change=percent_change,
            open=_optional_positive_decimal(ohlc.get("open")),
            high=_optional_positive_decimal(ohlc.get("high")),
            low=_optional_positive_decimal(ohlc.get("low")),
            contract_expiry_timestamp=expiry,
            retrieved_at=now,
            source=self.SOURCE,
        )

    def _retrieval_time(self) -> datetime:
        """Return the injected retrieval time normalized to UTC."""
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise CommodityFuturesDataError("The quote service clock must return a timezone-aware time.")
        return value.astimezone(timezone.utc)

    def _select_contract(self, now: datetime) -> tuple[Instrument, datetime]:
        """Resolve the nearest active configured future from the local MCX master."""
        csv_text = self._load_master_csv()
        raw_contracts = _parse_contract_rows(
            csv_text, now, self.EXCHANGE_SEGMENT, self.COMMODITY, self.INSTRUMENT_TYPE
        )
        if not raw_contracts:
            raise CommodityFuturesMasterError(
                f"No eligible unexpired standard {self.COMMODITY} futures contract exists."
            )

        try:
            instruments = self._instrument_service.load_instruments(self.EXCHANGE_SEGMENT)
        except FileNotFoundError:
            raise CommodityFuturesMasterError("The local MCX instrument master is missing.") from None
        except Exception:
            raise CommodityFuturesMasterError("The local MCX instrument master is invalid.") from None

        candidates: list[tuple[datetime, Instrument]] = []
        for expiry, raw in raw_contracts:
            token = raw["pSymbol"].strip()
            trading_symbol = raw["pTrdSymbol"].strip()
            matches = [
                instrument for instrument in instruments
                if instrument.instrument_token == token
                and instrument.exchange_segment == self.EXCHANGE_SEGMENT
                and instrument.symbol == self.COMMODITY
                and instrument.trading_symbol == trading_symbol
            ]
            if len(matches) > 1:
                raise CommodityFuturesMasterError(
                    f"An MCX {self.COMMODITY} instrument resolved ambiguously."
                )
            if len(matches) == 1:
                candidates.append((expiry, matches[0]))

        if not candidates:
            raise CommodityFuturesMasterError(
                f"No eligible {self.COMMODITY} futures contract resolved in the instrument service."
            )
        candidates.sort(key=lambda item: (item[0], item[1].trading_symbol, item[1].instrument_token))
        return candidates[0][1], candidates[0][0]

    def _load_master_csv(self) -> str:
        """Load the stored MCX master and translate storage failures safely."""
        try:
            return self._store.load_csv(self.EXCHANGE_SEGMENT)
        except FileNotFoundError:
            raise CommodityFuturesMasterError("The local MCX instrument master is missing.") from None
        except OSError:
            raise CommodityFuturesMasterError("The local MCX instrument master could not be read.") from None

    @staticmethod
    def _quote_row(response: Any, token: str) -> dict[str, Any]:
        """Extract exactly one quote row for the selected instrument token."""
        if isinstance(response, dict):
            raise CommodityFuturesProviderError("Kotak Neo returned a provider error response.")
        if not isinstance(response, list):
            raise CommodityFuturesDataError("Kotak MCX quote response must be a list.")
        if not response:
            raise CommodityFuturesDataError("Kotak Neo returned no quote for the selected contract.")
        if any(not isinstance(row, dict) for row in response):
            raise CommodityFuturesDataError("Kotak Neo returned a malformed quote record.")
        matching = [row for row in response if row.get("exchange_token") == token]
        if len(matching) > 1:
            raise CommodityFuturesDataError("Kotak Neo returned duplicate quotes for the selected contract.")
        if not matching:
            raise CommodityFuturesDataError("Kotak Neo quote identity does not match the selected contract.")
        if len(response) != 1:
            raise CommodityFuturesDataError("Kotak Neo returned unexpected additional quote records.")
        return matching[0]


def _parse_contract_rows(
    csv_text: str,
    now: datetime,
    exchange_segment: str,
    commodity: str,
    instrument_type: str,
) -> list[tuple[datetime, dict[str, str]]]:
    """Normalize MCX CSV headers and retain active futures for one commodity."""
    try:
        reader = csv.reader(io.StringIO(csv_text.removeprefix("\ufeff"), newline=""), strict=True)
        original_headers = next(reader, None)
        if not original_headers:
            raise CommodityFuturesMasterError("The local MCX instrument master has no header row.")
        headers = [header.strip() for header in original_headers]
        if len(headers) != len(set(headers)):
            raise CommodityFuturesMasterError("The local MCX instrument master has duplicate headers.")
        required = {"pSymbol", "pExchSeg", "pSymbolName", "pInstType", "pTrdSymbol", "lExpiryDate"}
        if not required.issubset(headers):
            raise CommodityFuturesMasterError("The local MCX instrument master lacks required contract fields.")

        records: list[tuple[datetime, dict[str, str]]] = []
        for values in reader:
            if len(values) != len(headers):
                raise CommodityFuturesMasterError("The local MCX instrument master contains malformed rows.")
            row = dict(zip(headers, values, strict=True))
            if (
                row["pExchSeg"].strip() != exchange_segment
                or row["pSymbolName"].strip() != commodity
                or row["pInstType"].strip() != instrument_type
            ):
                continue
            if not row["pSymbol"].strip() or not row["pTrdSymbol"].strip():
                raise CommodityFuturesMasterError(
                    f"An eligible {commodity} futures record has no instrument identity."
                )
            try:
                expiry = datetime.fromtimestamp(
                    int(row["lExpiryDate"].strip()), tz=timezone.utc
                )
            except (ValueError, OverflowError, OSError):
                raise CommodityFuturesMasterError(
                    f"An eligible {commodity} futures record has an invalid expiry timestamp."
                ) from None
            if expiry > now:
                records.append((expiry, row))
        return records
    except csv.Error:
        raise CommodityFuturesMasterError("The local MCX instrument master is malformed.") from None


def _required_decimal(value: Any, field: str, positive: bool = False) -> Decimal:
    """Parse a required finite decimal quote field, optionally requiring positivity."""
    number = _parse_decimal(value)
    if number is None or (positive and number <= 0):
        raise CommodityFuturesDataError(f"Kotak quote field {field!r} is missing or invalid.")
    return number


def _optional_positive_decimal(value: Any) -> Decimal | None:
    """Parse an optional finite positive OHLC price, returning None if unusable."""
    number = _parse_decimal(value)
    return number if number is not None and number > 0 else None


def _parse_decimal(value: Any) -> Decimal | None:
    """Convert a nonblank nonboolean value to a finite Decimal when possible."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)
