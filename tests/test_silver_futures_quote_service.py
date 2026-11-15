import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from marketpulse.config.settings import Settings
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.silver_futures_quote_service import (
    SilverFuturesDataError,
    SilverFuturesMasterError,
    SilverFuturesProviderError,
    SilverFuturesQuoteService,
)


class SilverFuturesQuoteServiceTests(unittest.TestCase):
    """Verify standard SILVER selection and safe Kotak quote parsing."""

    HEADERS = [
        "pSymbol", "pExchSeg", "pSymbolName", "pInstType", "pTrdSymbol",
        "pOptionType", "lExpiryDate ", "lLotSize", "pTrdUnits", "pPriceUnits",
        "pDeliveryUnits", "dPriceQuatation ", "pGroup",
    ]

    def setUp(self):
        """Create a temporary MCX master, fixed clock, and mocked provider."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(settings)
        self.provider = Mock()
        self.now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
        self.rows = [
            self._row("700", "SILVER04DEC26FUT", self.now + timedelta(days=19)),
            self._row("701", "SILVER05MAR27FUT", self.now + timedelta(days=110)),
            self._row("702", "SILVER05OCT26FUT", self.now - timedelta(days=40)),
            self._row("703", "SILVER04DEC26OPT", self.now + timedelta(days=19), pInstType="OPTFUT"),
            self._row("704", "SILVERM04DEC26FUT", self.now + timedelta(days=19), pSymbolName="SILVERM"),
            self._row("705", "SILVER04DEC26FUT-BSE", self.now + timedelta(days=19), pExchSeg="bse_fo"),
        ]
        self._save_rows(self.rows)
        self.instrument_service = InstrumentMasterService(self.store, InstrumentMasterParser())
        self.service = SilverFuturesQuoteService(
            self.store, self.instrument_service, self.provider, clock=lambda: self.now
        )

    def _row(self, token, trading_symbol, expiry, **overrides):
        """Build a raw MCX contract row with an expiry Unix timestamp."""
        row = {
            "pSymbol": token,
            "pExchSeg": "mcx_fo",
            "pSymbolName": "SILVER",
            "pInstType": "FUTCOM",
            "pTrdSymbol": trading_symbol,
            "pOptionType": "XX",
            "lExpiryDate ": str(int(expiry.timestamp())),
            "lLotSize": "30",
            "pTrdUnits": "KGS",
            "pPriceUnits": "KGS",
            "pDeliveryUnits": "KGS",
            "dPriceQuatation ": "1",
            "pGroup": "COM",
        }
        row.update(overrides)
        return row

    def _save_rows(self, rows):
        """Save fixture records as CSV with the source header whitespace intact."""
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=self.HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        self.store.save_csv("mcx_fo", stream.getvalue())

    @staticmethod
    def _quote_row(token="700", exchange="mcx_fo"):
        """Return a Silver quote fixture with independent display text and placeholder time."""
        return {
            "exchange_token": token,
            "exchange": exchange,
            "display_symbol": "SILVER04DEC26FUT-IN",
            "ltp": "176309",
            "change": "-2415",
            "per_change": "-1.3512",
            "ohlc": {"open": "178724", "high": "178800", "low": "175900", "close": "178724"},
            "lstup_time": "1900-01-01 00:00:00",
        }

    def test_selects_nearest_standard_future_and_uses_silver_quote_unit(self):
        """Normalize headers, retain XX, filter the standard contract, and make one request."""
        self.provider.get_quotes.return_value = [self._quote_row()]

        result = self.service.get_quote()

        self.assertIsInstance(result, SilverFuturesQuote)
        self.assertEqual(result.commodity, "SILVER")
        self.assertEqual(result.exchange, "MCX")
        self.assertEqual(result.contract_trading_symbol, "SILVER04DEC26FUT")
        self.assertEqual(result.instrument_token, "700")
        self.assertEqual(result.contract_expiry_timestamp, self.now + timedelta(days=19))
        self.assertEqual(result.quotation_unit, "INR per kg")
        self.assertEqual(result.price, Decimal("176309"))
        self.assertEqual(result.absolute_change, Decimal("-2415"))
        self.assertEqual(result.percentage_change, Decimal("-1.3512"))
        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[{"exchange_segment": "mcx_fo", "instrument_token": "700"}],
            quote_type="all",
        )

    def test_ignores_expired_options_other_products_and_segments(self):
        """Choose the next eligible Silver future after excluding nonstandard rows."""
        self._save_rows([self.rows[2], self.rows[3], self.rows[4], self.rows[5], self.rows[1]])
        self.provider.get_quotes.return_value = [self._quote_row(token="701")]
        result = self.service.get_quote()
        self.assertEqual(result.instrument_token, "701")

    def test_decimal_fields_and_optional_ohlc_are_parsed(self):
        """Preserve negative finite changes and expose only OHLC open high and low."""
        self.provider.get_quotes.return_value = [self._quote_row()]
        result = self.service.get_quote()
        self.assertEqual(result.open, Decimal("178724"))
        self.assertEqual(result.high, Decimal("178800"))
        self.assertEqual(result.low, Decimal("175900"))
        self.assertFalse(hasattr(result, "previous_close"))

    def test_invalid_or_missing_ltp_and_change_fields_are_rejected(self):
        """Require positive finite LTP and finite absolute and percentage changes."""
        for field, value in (
            ("ltp", "0"), ("ltp", "NaN"), ("ltp", None),
            ("change", "Infinity"), ("per_change", "broken"),
        ):
            with self.subTest(field=field, value=value):
                row = self._quote_row()
                row[field] = value
                self.provider.get_quotes.return_value = [row]
                with self.assertRaises(SilverFuturesDataError):
                    self.service.get_quote()

    def test_identity_mismatch_is_rejected(self):
        """Require the quote token and exchange to match the selected contract."""
        self.provider.get_quotes.return_value = [self._quote_row(token="wrong")]
        with self.assertRaisesRegex(SilverFuturesDataError, "identity"):
            self.service.get_quote()
        self.provider.get_quotes.return_value = [self._quote_row(exchange="nse_cm")]
        with self.assertRaisesRegex(SilverFuturesDataError, "exchange"):
            self.service.get_quote()

    def test_missing_duplicate_and_malformed_responses_are_rejected(self):
        """Reject absent, duplicate, extra, malformed, and non-list response shapes."""
        for response in ([], [self._quote_row(), self._quote_row()], ["bad"], {"data": []}):
            with self.subTest(response_type=type(response).__name__):
                self.provider.get_quotes.return_value = response
                with self.assertRaises((SilverFuturesDataError, SilverFuturesProviderError)):
                    self.service.get_quote()

    def test_provider_failures_are_sanitized(self):
        """Wrap provider errors without exposing exception messages."""
        self.provider.get_quotes.side_effect = RuntimeError("credential detail")
        with self.assertRaises(SilverFuturesProviderError) as caught:
            self.service.get_quote()
        self.assertNotIn("credential detail", str(caught.exception))

    def test_missing_master_and_no_active_contract_are_reported(self):
        """Fail before any quote request when the master or active future is absent."""
        self.store._path_for_segment("mcx_fo").unlink()
        with self.assertRaisesRegex(SilverFuturesMasterError, "missing"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

        self._save_rows([self.rows[2]])
        with self.assertRaisesRegex(SilverFuturesMasterError, "No eligible"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

    def test_1900_provider_timestamp_is_ignored(self):
        """Use the injected UTC retrieval time rather than Kotak's placeholder time."""
        self.provider.get_quotes.return_value = [self._quote_row()]
        result = self.service.get_quote()
        self.assertEqual(result.retrieved_at, self.now)
        self.assertFalse(hasattr(result, "market_data_timestamp"))


if __name__ == "__main__":
    unittest.main()
