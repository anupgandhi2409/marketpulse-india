import unittest
from decimal import Decimal
from unittest.mock import Mock

from marketpulse.data.instrument import Instrument
from marketpulse.data.quote import Quote
from marketpulse.data.quote_service import (
    InstrumentNotFoundError,
    NSEEquityQuoteService,
    QuoteProviderError,
    QuoteResponseError,
)


class NSEEquityQuoteServiceTests(unittest.TestCase):
    WATCHLIST = [
        "ICICIBANK-EQ", "AXISBANK-EQ", "LT-EQ", "APOLLOHOSP-EQ",
        "ADANIENT-EQ", "BHARTIARTL-EQ", "BSE-EQ", "TITAN-EQ",
    ]

    def setUp(self):
        self.instrument_service = Mock()
        self.instrument_service.find_by_trading_symbol.return_value = Instrument(
            instrument_token="2885",
            exchange_segment="nse_cm",
            symbol="RELIANCE",
            trading_symbol="RELIANCE-EQ",
        )
        self.provider = Mock()
        self.service = NSEEquityQuoteService(self.instrument_service, self.provider)

    def test_resolves_local_instrument_and_maps_compact_price_keys(self):
        # Compact quote keys follow kotakneoapi's stock_key_mapping in 3.0.13.
        self.provider.get_quotes.return_value = {
            "data": [{
                "tk": "2885", "e": "nse_cm", "ts": "RELIANCE-EQ",
                "ltp": "1250.50", "op": "1240", "h": "1260.25",
                "lo": "1235", "c": "not-a-verified-close", "v": "not-verified-volume",
            }]
        }

        quote = self.service.get_quote("RELIANCE-EQ")

        self.instrument_service.find_by_trading_symbol.assert_called_once_with(
            "nse_cm", "RELIANCE-EQ"
        )
        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[{"exchange_segment": "nse_cm", "instrument_token": "2885"}],
            quote_type="all",
        )
        self.assertEqual(quote, Quote(
            instrument_token="2885", trading_symbol="RELIANCE-EQ", exchange_segment="nse_cm",
            last_traded_price=Decimal("1250.50"), open=Decimal("1240"),
            high=Decimal("1260.25"), low=Decimal("1235"),
            previous_close=None, volume=None,
        ))

    def test_parses_observed_top_level_list_response_and_matches_exchange_token(self):
        self.provider.get_quotes.return_value = [
            {
                "exchange_token": "9999", "display_symbol": "OTHER-EQ", "exchange": "nse_cm",
                "ltp": "1.0", "ohlc": {"open": "1", "high": "2", "low": "0.5", "close": "0.9"},
            },
            {
                "exchange_token": "2885", "display_symbol": "RELIANCE-EQ", "exchange": "nse_cm",
                "ltp": "1250.50",
                "ohlc": {"open": "1240", "high": "1260.25", "low": "1235", "close": "1245.75"},
                "last_volume": "789", "last_traded_quantity": "12",
            },
        ]

        quote = self.service.get_quote("RELIANCE-EQ")

        self.assertEqual(quote, Quote(
            instrument_token="2885", trading_symbol="RELIANCE-EQ", exchange_segment="nse_cm",
            last_traded_price=Decimal("1250.50"), open=Decimal("1240"),
            high=Decimal("1260.25"), low=Decimal("1235"),
            previous_close=None, volume=None,
        ))

    def test_live_list_missing_optional_market_fields_are_none(self):
        self.provider.get_quotes.return_value = [{"exchange_token": "2885", "ltp": "12.5"}]

        quote = self.service.get_quote("RELIANCE-EQ")

        self.assertEqual(quote.last_traded_price, Decimal("12.5"))
        self.assertIsNone(quote.open)
        self.assertIsNone(quote.high)
        self.assertIsNone(quote.low)
        self.assertIsNone(quote.previous_close)
        self.assertIsNone(quote.volume)

    def test_live_list_rejects_malformed_nested_ohlc(self):
        self.provider.get_quotes.return_value = [{"exchange_token": "2885", "ohlc": "malformed"}]

        with self.assertRaisesRegex(QuoteResponseError, "ohlc.*object"):
            self.service.get_quote("RELIANCE-EQ")

    def test_live_list_rejects_missing_or_incorrect_instrument_token(self):
        for row in ({"ltp": "10"}, {"exchange_token": "9999", "ltp": "10"}):
            with self.subTest(row=row):
                self.provider.get_quotes.return_value = [row]
                with self.assertRaises(QuoteResponseError):
                    self.service.get_quote("RELIANCE-EQ")

    def test_live_list_converts_invalid_nested_numeric_field_to_response_error(self):
        self.provider.get_quotes.return_value = [{
            "exchange_token": "2885", "ohlc": {"open": "not-a-price"}
        }]

        with self.assertRaisesRegex(QuoteResponseError, "'open'"):
            self.service.get_quote("RELIANCE-EQ")

    def test_missing_market_fields_are_none(self):
        self.provider.get_quotes.return_value = {"data": [{"tk": "2885", "ltp": "12.5"}]}

        quote = self.service.get_quote("RELIANCE-EQ")

        self.assertEqual(quote.last_traded_price, Decimal("12.5"))
        self.assertIsNone(quote.open)
        self.assertIsNone(quote.previous_close)
        self.assertIsNone(quote.volume)

    def test_missing_instrument_does_not_call_provider(self):
        self.instrument_service.find_by_trading_symbol.return_value = None

        with self.assertRaises(InstrumentNotFoundError):
            self.service.get_quote("UNKNOWN-EQ")

        self.provider.get_quotes.assert_not_called()

    def test_provider_exception_is_reported_as_quote_provider_error(self):
        self.provider.get_quotes.side_effect = OSError("network unavailable")

        with self.assertRaisesRegex(QuoteProviderError, "network unavailable"):
            self.service.get_quote("RELIANCE-EQ")

    def test_sdk_error_payload_is_reported_as_provider_error(self):
        self.provider.get_quotes.return_value = {"error": [{"message": "unauthorized"}]}

        with self.assertRaisesRegex(QuoteProviderError, "unauthorized"):
            self.service.get_quote("RELIANCE-EQ")

    def test_malformed_or_unmatched_response_is_reported(self):
        for response in (None, {"data": "not-a-list"}, {"data": [{"tk": "9"}]}):
            with self.subTest(response=response):
                self.provider.get_quotes.return_value = response
                with self.assertRaises(QuoteResponseError):
                    self.service.get_quote("RELIANCE-EQ")

    def test_invalid_market_value_is_reported(self):
        self.provider.get_quotes.return_value = {"data": [{"tk": "2885", "ltp": "bad"}]}

        with self.assertRaisesRegex(QuoteResponseError, "'ltp'"):
            self.service.get_quote("RELIANCE-EQ")

    def _configure_watchlist(self):
        self.watchlist_instruments = [
            Instrument(
                instrument_token=str(index + 100),
                exchange_segment="nse_cm",
                symbol=symbol.removesuffix("-EQ"),
                trading_symbol=symbol,
            )
            for index, symbol in enumerate(self.WATCHLIST)
        ]
        self.instrument_service.load_instruments.return_value = self.watchlist_instruments

    def test_batch_quotes_eight_symbols_with_one_load_and_one_provider_call(self):
        self._configure_watchlist()
        requested = self.WATCHLIST
        self.provider.get_quotes.return_value = [
            {
                "exchange_token": instrument.instrument_token,
                "display_symbol": instrument.trading_symbol,
                "exchange": "nse_cm",
                "ltp": str(index + 10),
                "ohlc": {
                    "open": str(index + 9), "high": str(index + 11),
                    "low": str(index + 8), "close": "malformed-but-unused",
                },
                "last_volume": "malformed-but-unused",
            }
            for index, instrument in reversed(list(enumerate(self.watchlist_instruments)))
        ]

        quotes = self.service.get_quotes(requested)

        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[
                {"exchange_segment": "nse_cm", "instrument_token": str(index + 100)}
                for index in range(8)
            ],
            quote_type="all",
        )
        self.assertEqual([quote.trading_symbol for quote in quotes], requested)
        self.assertEqual([quote.last_traded_price for quote in quotes], [
            Decimal(index + 10) for index in range(8)
        ])
        self.assertTrue(all(quote.previous_close is None for quote in quotes))
        self.assertTrue(all(quote.volume is None for quote in quotes))

    def test_empty_batch_makes_no_master_or_provider_calls(self):
        self.assertEqual(self.service.get_quotes([]), [])
        self.instrument_service.load_instruments.assert_not_called()
        self.provider.get_quotes.assert_not_called()

    def test_duplicate_symbols_are_rejected_before_loading_or_request(self):
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.service.get_quotes(["RELIANCE-EQ", "RELIANCE-EQ"])
        self.instrument_service.load_instruments.assert_not_called()
        self.provider.get_quotes.assert_not_called()

    def test_unknown_batch_instrument_is_rejected_before_provider_call(self):
        self._configure_watchlist()

        with self.assertRaises(InstrumentNotFoundError):
            self.service.get_quotes(["ICICIBANK-EQ", "UNKNOWN-EQ"])

        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        self.provider.get_quotes.assert_not_called()

    def test_missing_batch_quote_record_is_reported(self):
        self._configure_watchlist()
        self.provider.get_quotes.return_value = [{"exchange_token": "100", "ltp": "10"}]

        with self.assertRaisesRegex(QuoteResponseError, r"token\(s\)"):
            self.service.get_quotes(self.WATCHLIST[:2])

    def test_malformed_batch_response_is_reported(self):
        self._configure_watchlist()
        self.provider.get_quotes.return_value = {"data": [{"tk": "100"}, "bad-row"]}

        with self.assertRaisesRegex(QuoteResponseError, "malformed quote row"):
            self.service.get_quotes(self.WATCHLIST[:2])


if __name__ == "__main__":
    unittest.main()
