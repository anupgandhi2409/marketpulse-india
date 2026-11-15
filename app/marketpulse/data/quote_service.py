from decimal import Decimal, InvalidOperation
from typing import Any

from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.quote import Quote
from marketpulse.data.kotak_neo import KotakNeoProvider


class InstrumentNotFoundError(LookupError):
    """The requested trading symbol is absent from the local NSE cash master."""


class QuoteProviderError(RuntimeError):
    """Kotak Neo rejected or failed the quote request."""


class QuoteResponseError(ValueError):
    """Kotak Neo returned a quote payload this adapter cannot interpret."""


class NSEEquityQuoteService:
    """Read NSE cash equity quotes using the locally stored instrument master."""

    EXCHANGE_SEGMENT = "nse_cm"
    QUOTE_TYPE = "all"
    _FIELD_MAP = {
        "last_traded_price": "ltp",
        "open": "op",
        "high": "h",
        "low": "lo",
    }

    def __init__(
        self,
        instrument_service: InstrumentMasterService,
        provider: KotakNeoProvider,
    ) -> None:
        self._instrument_service = instrument_service
        self._provider = provider

    def get_quote(self, trading_symbol: str) -> Quote:
        instrument = self._instrument_service.find_by_trading_symbol(
            self.EXCHANGE_SEGMENT, trading_symbol
        )
        if instrument is None:
            raise InstrumentNotFoundError(
                f"NSE cash instrument {trading_symbol!r} was not found in the local instrument master."
            )

        return self._fetch_quotes([instrument])[0]

    def get_quotes(self, trading_symbols: list[str]) -> list[Quote]:
        """Retrieve one quote per exact trading symbol with a single master load and API call."""
        if not trading_symbols:
            return []
        if len(set(trading_symbols)) != len(trading_symbols):
            raise ValueError("trading_symbols must not contain duplicates.")

        instruments = self._instrument_service.load_instruments(self.EXCHANGE_SEGMENT)
        instruments_by_symbol = {
            instrument.trading_symbol: instrument for instrument in instruments
        }
        missing = [symbol for symbol in trading_symbols if symbol not in instruments_by_symbol]
        if missing:
            raise InstrumentNotFoundError(
                f"NSE cash instruments were not found in the local instrument master: {missing!r}."
            )
        requested = [instruments_by_symbol[symbol] for symbol in trading_symbols]
        return self._fetch_quotes(requested)

    def _fetch_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        try:
            response = self._provider.get_quotes(
                instrument_tokens=[
                    {
                        "exchange_segment": self.EXCHANGE_SEGMENT,
                        "instrument_token": instrument.instrument_token,
                    }
                    for instrument in instruments
                ],
                quote_type=self.QUOTE_TYPE,
            )
        except Exception as exc:
            raise QuoteProviderError(f"Kotak Neo quote request failed: {exc}") from exc

        rows = self._extract_quote_rows(
            response, {instrument.instrument_token for instrument in instruments}
        )
        return [
            self._build_quote(instrument, *rows[instrument.instrument_token])
            for instrument in instruments
        ]

    @staticmethod
    def _build_quote(instrument: Instrument, row: dict[str, Any], is_live_format: bool) -> Quote:
        market_values: dict[str, Decimal | int | None] = {
            "previous_close": None,
            "volume": None,
        }
        if is_live_format:
            ohlc = row.get("ohlc")
            if ohlc is not None and not isinstance(ohlc, dict):
                raise QuoteResponseError("Kotak Neo quote field 'ohlc' must be an object.")
            ohlc = ohlc or {}
            market_values.update({
                "last_traded_price": NSEEquityQuoteService._market_value(
                    row, "ltp", "last_traded_price"
                ),
                "open": NSEEquityQuoteService._market_value(ohlc, "open", "open"),
                "high": NSEEquityQuoteService._market_value(ohlc, "high", "high"),
                "low": NSEEquityQuoteService._market_value(ohlc, "low", "low"),
            })
        else:
            market_values.update({
                field: NSEEquityQuoteService._market_value(row, sdk_key, field)
                for field, sdk_key in NSEEquityQuoteService._FIELD_MAP.items()
            })
        return Quote(
            instrument_token=instrument.instrument_token,
            trading_symbol=instrument.trading_symbol,
            exchange_segment=NSEEquityQuoteService.EXCHANGE_SEGMENT,
            **market_values,
        )

    @staticmethod
    def _extract_quote_rows(
        response: Any, instrument_tokens: set[str]
    ) -> dict[str, tuple[dict[str, Any], bool]]:
        is_live_format = isinstance(response, list)
        if isinstance(response, dict):
            if response.get("error") or response.get("Error"):
                raise QuoteProviderError(f"Kotak Neo quote request returned an error: {response}")
            data = response.get("data")
            is_live_format = False
        elif is_live_format:
            data = response
        else:
            raise QuoteResponseError("Kotak Neo quote response must be an object or list.")
        if not isinstance(data, list) or not data:
            raise QuoteResponseError("Kotak Neo quote response must contain a non-empty quote list.")
        if any(not isinstance(item, dict) for item in data):
            raise QuoteResponseError("Kotak Neo quote response contains a malformed quote row.")
        matching_rows: dict[str, tuple[dict[str, Any], bool]] = {}
        for row in data:
            if is_live_format:
                token = row.get("exchange_token")
            else:
                token = row.get("tk", row.get("instrument_token", ""))
            token = str(token)
            if token in instrument_tokens:
                if token in matching_rows:
                    raise QuoteResponseError(
                        f"Kotak Neo quote response contains duplicate records for token {token!r}."
                    )
                matching_rows[token] = (row, is_live_format)
        missing_tokens = instrument_tokens - matching_rows.keys()
        if missing_tokens:
            raise QuoteResponseError(
                f"Kotak Neo quote response did not contain instrument token(s): "
                f"{sorted(missing_tokens)!r}."
            )
        return matching_rows

    @staticmethod
    def _market_value(row: dict[str, Any], sdk_key: str, field: str) -> Decimal | int | None:
        value = row.get(sdk_key)
        if value is None or value == "":
            return None
        try:
            if field == "volume":
                volume = int(value)
                if Decimal(str(value)) != volume:
                    raise ValueError
                return volume
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise QuoteResponseError(
                f"Kotak Neo quote field {sdk_key!r} has invalid value {value!r}."
            ) from exc
