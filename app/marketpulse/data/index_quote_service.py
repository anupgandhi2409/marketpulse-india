from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any

from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.kotak_neo import KotakNeoProvider


class IndexNotSupportedError(LookupError):
    """Raised when a requested benchmark has no verified Kotak REST identifier."""


class IndexQuoteProviderError(RuntimeError):
    """Raised when Kotak Neo REST cannot retrieve index quotes."""


class IndexQuoteResponseError(ValueError):
    """Raised when Kotak Neo returns malformed or incomplete index quote data."""


class IndexQuoteService:
    """Retrieve verified NSE and BSE index values through Kotak Neo REST quotes."""

    SOURCE = "Kotak Neo"
    _SUPPORTED_INDEXES = {
        "NIFTY 50": ("nse_cm", "Nifty 50", "NSE"),
        "NIFTY BANK": ("nse_cm", "Nifty Bank", "NSE"),
        "NIFTY IT": ("nse_cm", "Nifty IT", "NSE"),
        "NIFTY MIDCAP 100": ("nse_cm", "NIFTY MIDCAP 100", "NSE"),
        "NIFTY SMALLCAP 100": ("nse_cm", "NIFTY SMLCAP 100", "NSE"),
        "SENSEX": ("bse_cm", "SENSEX", "BSE"),
    }

    def __init__(self, provider: KotakNeoProvider) -> None:
        """Create the service with the existing Kotak REST provider."""
        self._provider = provider

    def get_quotes(self, index_names: list[str]) -> list[IndexQuote]:
        """Fetch one REST batch and return values in the caller's requested order."""
        if not index_names:
            return []
        if len(set(index_names)) != len(index_names):
            raise ValueError("index_names must not contain duplicates.")

        unsupported = [name for name in index_names if name not in self._SUPPORTED_INDEXES]
        if unsupported:
            raise IndexNotSupportedError(f"Unsupported index names: {unsupported!r}.")

        request = [
            {"exchange_segment": self._SUPPORTED_INDEXES[name][0],
             "instrument_token": self._SUPPORTED_INDEXES[name][1]}
            for name in index_names
        ]
        try:
            response = self._provider.get_quotes(instrument_tokens=request, quote_type="all")
        except Exception as exc:
            # Provider exception messages may contain request or authentication details.
            raise IndexQuoteProviderError(
                f"Kotak Neo REST quote request failed ({type(exc).__name__})."
            ) from exc

        if isinstance(response, dict):
            category = "Not_Ok" if response.get("stat") == "Not_Ok" else "provider error"
            raise IndexQuoteProviderError(f"Kotak Neo REST quote request failed ({category}).")
        if not isinstance(response, list):
            raise IndexQuoteResponseError("Kotak Neo index quote response must be a list.")

        requested_by_id = {
            (self._SUPPORTED_INDEXES[name][0], self._SUPPORTED_INDEXES[name][1]): name
            for name in index_names
        }
        values: dict[tuple[str, str], tuple[Decimal, Decimal | None, Decimal | None, datetime | None]] = {}
        for row in response:
            if not isinstance(row, dict):
                raise IndexQuoteResponseError("Kotak Neo index quote row must be an object.")
            token = row.get("exchange_token")
            segment = row.get("exchange")
            if not isinstance(token, str) or not isinstance(segment, str):
                raise IndexQuoteResponseError("Kotak Neo index quote row has no identifier.")
            key = (segment, token)
            if key not in requested_by_id:
                continue
            if key in values:
                raise IndexQuoteResponseError(f"Duplicate Kotak Neo index quote for {token!r}.")
            ltp = row.get("ltp")
            if ltp is None or isinstance(ltp, bool):
                raise IndexQuoteResponseError(f"Kotak Neo index quote has no LTP for {token!r}.")
            try:
                value = Decimal(str(ltp))
            except (InvalidOperation, ValueError) as exc:
                raise IndexQuoteResponseError(
                    f"Kotak Neo index LTP is malformed for {token!r}."
                ) from exc
            if not value.is_finite():
                raise IndexQuoteResponseError(
                    f"Kotak Neo index LTP is malformed for {token!r}."
                )
            change = _optional_decimal(row, "change", token)
            percent_change = _optional_decimal(row, "per_change", token)
            quote_timestamp = _optional_epoch_timestamp(
                row.get("lstup_time", row.get("timestamp"))
            )
            values[key] = (value, change, percent_change, quote_timestamp)

        results: list[IndexQuote] = []
        for name in index_names:
            segment, identifier, exchange = self._SUPPORTED_INDEXES[name]
            key = (segment, identifier)
            if key not in values:
                raise IndexQuoteResponseError(
                    f"Kotak Neo response is missing index quote for {name!r}."
                )
            index_value, change, percent_change, quote_timestamp = values[key]
            results.append(IndexQuote(
                index_name=name,
                exchange=exchange,
                source=self.SOURCE,
                index_value=index_value,
                instrument_identifier=identifier,
                change=change,
                percent_change=percent_change,
                quote_timestamp=quote_timestamp,
            ))
        return results


def _optional_decimal(row: dict[str, Any], field: str, token: str) -> Decimal | None:
    """Parse an optional finite quote change, preserving missing values as unavailable."""
    if field not in row or row[field] is None:
        return None
    value = row[field]
    if isinstance(value, bool):
        raise IndexQuoteResponseError(f"Kotak Neo index {field} is malformed for {token!r}.")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise IndexQuoteResponseError(
            f"Kotak Neo index {field} is malformed for {token!r}."
        ) from exc
    if not result.is_finite():
        raise IndexQuoteResponseError(f"Kotak Neo index {field} is malformed for {token!r}.")
    return result


def _optional_epoch_timestamp(value: Any) -> datetime | None:
    """Convert a documented Unix quote-update time, leaving invalid values unavailable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        epoch = Decimal(str(value).strip())
        if not epoch.is_finite() or epoch <= 0:
            return None
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except (InvalidOperation, ValueError, OverflowError, OSError):
        return None
