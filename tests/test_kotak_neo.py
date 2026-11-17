import unittest
from unittest.mock import Mock, patch

from marketpulse.config.settings import Settings
from marketpulse.data.kotak_neo import KotakConfigurationError, KotakNeoProvider


class KotakNeoProviderTests(unittest.TestCase):
    def test_missing_consumer_key_raises_configuration_error(self):
        settings = Settings(kotak_consumer_key=None)

        with patch("marketpulse.data.kotak_neo.NeoAPI") as neo_api:
            with self.assertRaises(KotakConfigurationError):
                KotakNeoProvider(settings=settings)

        neo_api.assert_not_called()

    def test_constructs_sdk_client_with_configured_values(self):
        settings = Settings(kotak_consumer_key="test-consumer-key", kotak_environment="uat")

        with patch("marketpulse.data.kotak_neo.NeoAPI") as neo_api:
            provider = KotakNeoProvider(settings=settings)

        neo_api.assert_called_once_with(
            consumer_key="test-consumer-key",
            environment="uat",
        )
        self.assertIs(provider._client, neo_api.return_value)

    def test_injected_client_is_used_without_constructing_sdk(self):
        client = Mock()

        with patch("marketpulse.data.kotak_neo.NeoAPI") as neo_api:
            provider = KotakNeoProvider(client=client)

        neo_api.assert_not_called()
        self.assertIs(provider._client, client)

    def test_get_scrip_master_forwards_arguments_and_returns_response(self):
        response = {"data": ["instrument"]}
        client = Mock()
        client.scrip_master.return_value = response
        provider = KotakNeoProvider(client=client)

        result = provider.get_scrip_master(exchange_segment="NSECM")

        client.scrip_master.assert_called_once_with(exchange_segment="NSECM")
        self.assertIs(result, response)

    def test_search_instruments_forwards_arguments_and_returns_response(self):
        response = {"data": ["matching instrument"]}
        client = Mock()
        client.search_scrip.return_value = response
        provider = KotakNeoProvider(client=client)

        result = provider.search_instruments(
            exchange_segment="NSEFO",
            symbol="NIFTY",
            expiry="202612",
            option_type="CE",
            strike_price="25000",
            ignore_50multiple=False,
        )

        client.search_scrip.assert_called_once_with(
            exchange_segment="NSEFO",
            symbol="NIFTY",
            expiry="202612",
            option_type="CE",
            strike_price="25000",
            ignore_50multiple=False,
        )
        self.assertIs(result, response)

    def test_get_quotes_forwards_arguments_and_returns_response(self):
        response = {"data": ["quote"]}
        instrument_tokens = [{"exchange_segment": "nse_cm", "instrument_token": "1333"}]
        client = Mock()
        client.quotes.return_value = response
        provider = KotakNeoProvider(client=client)

        result = provider.get_quotes(instrument_tokens=instrument_tokens, quote_type="all")

        client.quotes.assert_called_once_with(
            instrument_tokens=instrument_tokens,
            quote_type="all",
        )
        self.assertIs(result, response)

if __name__ == "__main__":
    unittest.main()
