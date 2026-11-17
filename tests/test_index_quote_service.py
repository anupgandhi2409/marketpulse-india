import unittest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import Mock

from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.index_quote_service import (
    IndexNotSupportedError,
    IndexQuoteProviderError,
    IndexQuoteResponseError,
    IndexQuoteService,
)


class IndexQuoteServiceTests(unittest.TestCase):
    """Exercise Kotak REST index mappings and response validation."""

    def setUp(self):
        """Create the service with a mocked REST provider."""
        self.provider = Mock()
        self.service = IndexQuoteService(self.provider)

    @staticmethod
    def row(identifier, ltp, segment="nse_cm"):
        """Return a documented Kotak index quote row."""
        return {"exchange_token": identifier, "exchange": segment, "ltp": ltp}

    def test_all_six_indices_use_one_request_and_restore_requested_order(self):
        """Map all verified IDs and ignore the REST response's order."""
        self.provider.get_quotes.return_value = [
            self.row("SENSEX", "81234.50", "bse_cm"),
            self.row("NIFTY SMLCAP 100", "18456.25"),
            self.row("NIFTY MIDCAP 100", "58678.00"),
            self.row("Nifty IT", "42123.75"),
            self.row("Nifty Bank", "53456.50"),
            self.row("Nifty 50", "24567.25"),
        ]
        requested = [
            "SENSEX", "NIFTY SMALLCAP 100", "NIFTY MIDCAP 100",
            "NIFTY IT", "NIFTY BANK", "NIFTY 50",
        ]

        quotes = self.service.get_quotes(requested)

        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[
                {"exchange_segment": "bse_cm", "instrument_token": "SENSEX"},
                {"exchange_segment": "nse_cm", "instrument_token": "NIFTY SMLCAP 100"},
                {"exchange_segment": "nse_cm", "instrument_token": "NIFTY MIDCAP 100"},
                {"exchange_segment": "nse_cm", "instrument_token": "Nifty IT"},
                {"exchange_segment": "nse_cm", "instrument_token": "Nifty Bank"},
                {"exchange_segment": "nse_cm", "instrument_token": "Nifty 50"},
            ],
            quote_type="all",
        )
        self.assertEqual([quote.index_name for quote in quotes], requested)
        self.assertEqual([quote.index_value for quote in quotes], [
            Decimal("81234.50"), Decimal("18456.25"), Decimal("58678.00"),
            Decimal("42123.75"), Decimal("53456.50"), Decimal("24567.25"),
        ])
        self.assertEqual(quotes[0], IndexQuote(
            "SENSEX", "BSE", "Kotak Neo", Decimal("81234.50"), "SENSEX"
        ))

    def test_maps_optional_change_fields_and_exchange_timestamp_for_all_indices(self):
        """Preserve documented point, percentage, and quote-update fields by identity."""
        names = list(self.service._SUPPORTED_INDEXES)
        rows = []
        for index, name in enumerate(names):
            segment, identifier, _exchange = self.service._SUPPORTED_INDEXES[name]
            rows.append({
                "exchange_token": identifier,
                "exchange": segment,
                "ltp": "100.00",
                "change": str((index - 2) * 1.25),
                "per_change": str((index - 2) * 0.50),
                "ohlc": {"close": "99.00"},
                "lstup_time": "1794873600",
            })
        self.provider.get_quotes.return_value = list(reversed(rows))

        quotes = self.service.get_quotes(names)

        self.provider.get_quotes.assert_called_once()
        self.assertEqual([quote.index_name for quote in quotes], names)
        self.assertEqual(
            [quote.change for quote in quotes],
            [Decimal(value) for value in ("-2.50", "-1.25", "0.00", "1.25", "2.50", "3.75")],
        )
        self.assertEqual(
            [quote.percent_change for quote in quotes],
            [Decimal(value) for value in ("-1.00", "-0.50", "0.00", "0.50", "1.00", "1.50")],
        )
        self.assertTrue(all(quote.quote_timestamp == datetime(2026, 11, 17, tzinfo=timezone.utc) for quote in quotes))

    def test_missing_change_fields_remain_unavailable(self):
        """Keep absent change and exchange timestamp fields as None."""
        self.provider.get_quotes.return_value = [{
            **self.row("Nifty 50", "100"),
            "ohlc": {"close": "99"},
            "lstup_time": "invalid",
        }]
        quote = self.service.get_quotes(["NIFTY 50"])[0]
        self.assertIsNone(quote.change)
        self.assertIsNone(quote.percent_change)
        self.assertIsNone(quote.quote_timestamp)

    def test_malformed_change_values_fail_closed(self):
        """Reject nonnumeric, nonfinite, blank, and boolean change fields."""
        for field, value in (
            ("change", "bad"), ("change", "NaN"), ("change", "Infinity"),
            ("change", " "), ("change", True), ("per_change", "bad"),
            ("per_change", "-Infinity"),
        ):
            with self.subTest(field=field, value=value):
                self.provider.get_quotes.return_value = [
                    {**self.row("Nifty 50", "100"), field: value}
                ]
                with self.assertRaises(IndexQuoteResponseError):
                    self.service.get_quotes(["NIFTY 50"])

    def test_index_identity_mismatch_is_not_used(self):
        """Do not map otherwise valid changes from the wrong segment or identifier."""
        self.provider.get_quotes.return_value = [{
            "exchange_token": "Nifty 50", "exchange": "bse_cm", "ltp": "100",
            "change": "1", "per_change": "1",
        }]
        with self.assertRaisesRegex(IndexQuoteResponseError, "missing index quote"):
            self.service.get_quotes(["NIFTY 50"])

    def test_empty_input_does_not_call_provider(self):
        """Return an empty list without making a REST request."""
        self.assertEqual(self.service.get_quotes([]), [])
        self.provider.get_quotes.assert_not_called()

    def test_duplicate_indices_are_rejected_before_request(self):
        """Reject duplicate user-facing names before requesting data."""
        with self.assertRaises(ValueError):
            self.service.get_quotes(["NIFTY 50", "NIFTY 50"])
        self.provider.get_quotes.assert_not_called()

    def test_unsupported_index_is_rejected_before_request(self):
        """Reject unsupported benchmarks such as GIFT NIFTY without a call."""
        with self.assertRaises(IndexNotSupportedError):
            self.service.get_quotes(["GIFT NIFTY"])
        self.provider.get_quotes.assert_not_called()

    def test_missing_quote_row_is_reported(self):
        """Report a requested benchmark omitted from the response list."""
        self.provider.get_quotes.return_value = [self.row("Nifty 50", "100")]
        with self.assertRaisesRegex(IndexQuoteResponseError, "missing index quote"):
            self.service.get_quotes(["NIFTY 50", "NIFTY BANK"])

    def test_malformed_rows_and_ltp_are_reported(self):
        """Reject malformed response objects and invalid market values."""
        self.provider.get_quotes.return_value = ["bad"]
        with self.assertRaises(IndexQuoteResponseError):
            self.service.get_quotes(["NIFTY 50"])
        self.provider.get_quotes.return_value = [{"exchange_token": "Nifty 50", "ltp": "1"}]
        with self.assertRaisesRegex(IndexQuoteResponseError, "no identifier"):
            self.service.get_quotes(["NIFTY 50"])
        self.provider.get_quotes.return_value = [self.row("Nifty 50", "bad")]
        with self.assertRaisesRegex(IndexQuoteResponseError, "LTP is malformed"):
            self.service.get_quotes(["NIFTY 50"])

    def test_provider_errors_are_reported_without_echoing_provider_message(self):
        """Wrap provider failures without exposing their potentially sensitive text."""
        self.provider.get_quotes.side_effect = OSError("authorization secret")
        with self.assertRaisesRegex(IndexQuoteProviderError, "OSError") as caught:
            self.service.get_quotes(["NIFTY 50"])
        self.assertNotIn("authorization secret", str(caught.exception))

    def test_sdk_error_dictionary_is_treated_as_provider_failure(self):
        """Treat an SDK error envelope as a provider failure, not quote rows."""
        self.provider.get_quotes.return_value = {"stat": "Not_Ok", "emsg": "Invalid instrument/code"}
        with self.assertRaises(IndexQuoteProviderError):
            self.service.get_quotes(["NIFTY 50"])


if __name__ == "__main__":
    unittest.main()
