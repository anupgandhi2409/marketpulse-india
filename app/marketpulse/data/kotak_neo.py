from typing import Any

from neo_api_client import NeoAPI

from marketpulse.config.settings import Settings, settings as default_settings


class KotakConfigurationError(Exception):
    """Raised when Kotak Neo is not configured for provider initialization."""


class KotakNeoProvider:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings if settings is not None else default_settings

        if client is not None:
            self._client = client
            return

        if not self._settings.kotak_consumer_key:
            raise KotakConfigurationError(
                "KOTAK_CONSUMER_KEY must be configured to initialize KotakNeoProvider."
            )

        self._client = NeoAPI(
            consumer_key=self._settings.kotak_consumer_key,
            environment=self._settings.kotak_environment,
        )

    def get_scrip_master(self, exchange_segment: str | None = None) -> Any:
        return self._client.scrip_master(exchange_segment=exchange_segment)

    def search_instruments(
        self,
        exchange_segment: str = "",
        symbol: str = "",
        expiry: str | None = None,
        option_type: str | None = None,
        strike_price: str | None = None,
        ignore_50multiple: bool = True,
    ) -> Any:
        return self._client.search_scrip(
            exchange_segment=exchange_segment,
            symbol=symbol,
            expiry=expiry,
            option_type=option_type,
            strike_price=strike_price,
            ignore_50multiple=ignore_50multiple,
        )

    def get_quotes(
        self,
        instrument_tokens: list[dict[str, str]] | None = None,
        quote_type: str | None = None,
    ) -> Any:
        return self._client.quotes(instrument_tokens=instrument_tokens, quote_type=quote_type)

    def get_historical_data(
        self,
        neosymbol: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> Any:
        return self._client.historical_data(
            neosymbol=neosymbol,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
