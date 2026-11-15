import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from zoneinfo import ZoneInfo

from marketpulse.data.currency_futures_quote import CurrencyFuturesQuote
from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.kotak_neo import KotakNeoProvider


class UsdInrMasterError(ValueError):
    """Raised when the local currency master cannot resolve an active future."""


class UsdInrDataError(ValueError):
    """Raised when contract metadata or a returned quote fails validation."""


class UsdInrProviderError(RuntimeError):
    """Raised when Kotak quote retrieval fails without exposing provider details."""


class UsdInrFuturesQuoteService:
    """Retrieve and validate the nearest active USD/INR currency futures quote."""

    EXCHANGE_SEGMENT = "cde_fo"
    CURRENCY_PAIR = "USD/INR"
    INSTRUMENT_TYPE = "FUTCUR"
    QUOTATION_UNIT = "INR per USD"
    SOURCE = "Kotak Neo"
    EXPIRY_EPOCH_OFFSET_SECONDS = 315_511_200
    CONTRACT_SIZE_USD = Decimal("1000")
    IST = ZoneInfo("Asia/Kolkata")

    def __init__(
        self,
        store: InstrumentMasterStore,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Set the local master, parser service, provider, and injectable clock."""
        self._store = store
        self._instrument_service = instrument_service
        self._provider = provider
        self._clock = clock or _utc_now

    def get_quote(self) -> CurrencyFuturesQuote:
        """Select the nearest active USDINR future and fetch exactly one quote."""
        retrieved_at = self._retrieval_time()
        contract, expiry, lot_size = self._select_contract(retrieved_at)
        try:
            response = self._provider.get_quotes(
                instrument_tokens=[{
                    "exchange_segment": self.EXCHANGE_SEGMENT,
                    "instrument_token": contract.instrument_token,
                }],
                quote_type="all",
            )
        except Exception as exc:
            raise UsdInrProviderError(
                f"Kotak Neo USD/INR quote request failed ({type(exc).__name__})."
            ) from exc

        row = _quote_row(response, contract.instrument_token)
        if row.get("exchange") != self.EXCHANGE_SEGMENT:
            raise UsdInrDataError("Kotak quote exchange does not match the selected contract.")
        if row.get("exchange_token") != contract.instrument_token:
            raise UsdInrDataError("Kotak quote token does not match the selected contract.")

        return CurrencyFuturesQuote(
            currency_pair=self.CURRENCY_PAIR,
            instrument_type="currency futures",
            exchange_segment=self.EXCHANGE_SEGMENT,
            contract_trading_symbol=contract.trading_symbol,
            instrument_token=contract.instrument_token,
            price=_required_decimal(row.get("ltp"), "ltp", positive=True),
            quotation_unit=self.QUOTATION_UNIT,
            lot_size=lot_size,
            contract_size_usd=self.CONTRACT_SIZE_USD,
            absolute_change=_required_decimal(row.get("change"), "change"),
            percentage_change=_required_decimal(row.get("per_change"), "per_change"),
            contract_expiry_timestamp=expiry,
            retrieved_at=retrieved_at,
            source=self.SOURCE,
        )

    def _retrieval_time(self) -> datetime:
        """Return an aware clock value normalized to UTC."""
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise UsdInrDataError("The quote service clock must return a timezone-aware time.")
        return now.astimezone(timezone.utc)

    def _select_contract(self, now_utc: datetime) -> tuple[Instrument, datetime, int]:
        """Resolve the nearest unexpired USDINR/FUTCUR contract from local files."""
        csv_text = self._load_master()
        rows = _parse_usdinr_future_rows(csv_text, now_utc)
        if not rows:
            raise UsdInrMasterError("No eligible unexpired USDINR futures contract exists.")

        try:
            instruments = self._instrument_service.load_instruments(self.EXCHANGE_SEGMENT)
        except FileNotFoundError:
            raise UsdInrMasterError("The local cde_fo instrument master is missing.") from None
        except Exception:
            raise UsdInrMasterError("The local cde_fo instrument master is invalid.") from None

        candidates: list[tuple[datetime, Instrument, int]] = []
        for expiry, raw, lot_size in rows:
            token = raw["pSymbol"].strip()
            trading_symbol = raw["pTrdSymbol"].strip()
            matches = [
                instrument for instrument in instruments
                if instrument.instrument_token == token
                and instrument.exchange_segment == self.EXCHANGE_SEGMENT
                and instrument.symbol == "USDINR"
                and instrument.trading_symbol == trading_symbol
            ]
            if len(matches) > 1:
                raise UsdInrMasterError("A USDINR futures instrument resolved ambiguously.")
            if len(matches) == 1:
                candidates.append((expiry, matches[0], lot_size))

        if not candidates:
            raise UsdInrMasterError("No eligible USDINR future resolved in the instrument service.")
        candidates.sort(key=lambda item: (item[0], item[1].trading_symbol, item[1].instrument_token))
        expiry, instrument, lot_size = candidates[0]
        return instrument, expiry, lot_size

    def _load_master(self) -> str:
        """Load the saved cde_fo master and report storage failures safely."""
        try:
            return self._store.load_csv(self.EXCHANGE_SEGMENT)
        except FileNotFoundError:
            raise UsdInrMasterError("The local cde_fo instrument master is missing.") from None
        except OSError:
            raise UsdInrMasterError("The local cde_fo instrument master could not be read.") from None


def _parse_usdinr_future_rows(
    csv_text: str,
    now_utc: datetime,
) -> list[tuple[datetime, dict[str, str], int]]:
    """Trim headers and parse active USDINR currency futures with Kotak's expiry offset."""
    try:
        reader = csv.reader(io.StringIO(csv_text.removeprefix("\ufeff"), newline=""), strict=True)
        original_headers = next(reader, None)
        if not original_headers:
            raise UsdInrMasterError("The local cde_fo master has no header row.")
        headers = [header.strip() for header in original_headers]
        if len(headers) != len(set(headers)):
            raise UsdInrMasterError("The local cde_fo master has duplicate headers after trimming.")
        required = {
            "pExchSeg", "pSymbol", "pSymbolName", "pInstType", "pTrdSymbol",
            "pOptionType", "lExpiryDate", "lLotSize",
        }
        if not required.issubset(headers):
            raise UsdInrMasterError("The local cde_fo master lacks required futures fields.")

        rows: list[tuple[datetime, dict[str, str], int]] = []
        for values in reader:
            if len(values) != len(headers):
                raise UsdInrMasterError("The local cde_fo master contains malformed rows.")
            row = dict(zip(headers, values, strict=True))
            if (
                row["pExchSeg"].strip() != "cde_fo"
                or row["pSymbolName"].strip() != "USDINR"
                or row["pInstType"].strip() != "FUTCUR"
                or row["pOptionType"].strip() != "XX"
            ):
                continue
            if not row["pSymbol"].strip() or not row["pTrdSymbol"].strip():
                raise UsdInrMasterError("A USDINR futures row has no instrument identity.")
            try:
                expiry_epoch = int(row["lExpiryDate"].strip()) + UsdInrFuturesQuoteService.EXPIRY_EPOCH_OFFSET_SECONDS
                expiry = datetime.fromtimestamp(expiry_epoch, tz=timezone.utc).astimezone(
                    UsdInrFuturesQuoteService.IST
                )
                lot_decimal = Decimal(row["lLotSize"].strip())
            except (ValueError, InvalidOperation, OverflowError, OSError):
                raise UsdInrMasterError("A USDINR futures row has invalid expiry or lot metadata.") from None
            if not lot_decimal.is_finite() or lot_decimal <= 0 or lot_decimal != lot_decimal.to_integral_value():
                raise UsdInrMasterError("A USDINR futures row has invalid lot metadata.")
            if expiry.astimezone(timezone.utc) > now_utc:
                rows.append((expiry, row, int(lot_decimal)))
        return rows
    except csv.Error:
        raise UsdInrMasterError("The local cde_fo master is malformed.") from None


def _quote_row(response: Any, token: str) -> dict[str, Any]:
    """Require one quote record matching the selected USDINR instrument token."""
    if isinstance(response, dict):
        raise UsdInrProviderError("Kotak Neo returned a provider error response.")
    if not isinstance(response, list):
        raise UsdInrDataError("Kotak cde_fo quote response must be a list.")
    if len(response) != 1 or not isinstance(response[0], dict):
        raise UsdInrDataError("Kotak Neo must return exactly one valid quote record.")
    return response[0]


def _required_decimal(value: Any, field: str, positive: bool = False) -> Decimal:
    """Parse a finite required decimal field, optionally requiring a positive value."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise UsdInrDataError(f"Kotak quote field {field!r} is missing or invalid.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise UsdInrDataError(f"Kotak quote field {field!r} is missing or invalid.") from None
    if not number.is_finite() or (positive and number <= 0):
        raise UsdInrDataError(f"Kotak quote field {field!r} is missing or invalid.")
    return number


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
