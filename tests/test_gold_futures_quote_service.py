import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from marketpulse.config.settings import Settings
from marketpulse.data.gold_futures_quote_service import (
    GoldFuturesDataError,
    GoldFuturesMasterError,
    GoldFuturesProviderError,
    GoldFuturesQuoteService,
)
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore


class GoldFuturesQuoteServiceTests(unittest.TestCase):
    """Exercise local contract selection and strict read-only quote validation."""

    HEADERS = [
        "pSymbol", "pExchSeg", "pSymbolName", "pInstType", "pTrdSymbol",
        "pOptionType", "lExpiryDate ", "lLotSize", "pTrdUnits", "pPriceUnits",
        "pDeliveryUnits", "dPriceQuatation ", "pGroup",
    ]

    def setUp(self):
        """Create a temporary MCX master and a mocked Kotak provider."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(self.settings)
        self.provider = Mock()
        self.now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
        self.rows = [
            self._row("500", "GOLD04DEC26FUT", self.now + timedelta(days=19)),
            self._row("501", "GOLD04FEB27FUT", self.now + timedelta(days=81)),
            self._row("502", "GOLD04OCT26FUT", self.now - timedelta(days=40)),
            self._row("503", "GOLD04JAN27FUT", self.now + timedelta(days=47), pInstType="OPTFUT"),
            self._row("504", "GOLDM04DEC26FUT", self.now + timedelta(days=19), pSymbolName="GOLDM"),
        ]
        self._save_rows(self.rows)
        self.instrument_service = InstrumentMasterService(self.store, InstrumentMasterParser())
        self.service = GoldFuturesQuoteService(
            self.store, self.instrument_service, self.provider, clock=lambda: self.now
        )

    def _row(self, token, trading_symbol, expiry, **overrides):
        """Build one raw MCX master row with an expiry epoch in seconds."""
        row = {
            "pSymbol": token,
            "pExchSeg": "mcx_fo",
            "pSymbolName": "GOLD",
            "pInstType": "FUTCOM",
            "pTrdSymbol": trading_symbol,
            "pOptionType": "XX",
            "lExpiryDate ": str(int(expiry.timestamp())),
            "lLotSize": "1",
            "pTrdUnits": "KGS",
            "pPriceUnits": "GRMS",
            "pDeliveryUnits": "KGS",
            "dPriceQuatation ": "10",
            "pGroup": "COM",
        }
        row.update(overrides)
        return row

    def _save_rows(self, rows):
        """Save test rows as CSV while preserving padded source headers."""
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=self.HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        self.store.save_csv("mcx_fo", stream.getvalue())

    def _quote_row(self, token="500", exchange="mcx_fo"):
        """Return a sanitized mock quote with the verified provider fields."""
        return {
            "exchange_token": token,
            "exchange": exchange,
            "display_symbol": "GOLD04DEC26FUT-IN",
            "ltp": "136705",
            "change": "480",
            "per_change": "0.3524",
            "ohlc": {"open": "136000", "high": "137000", "low": "135800", "close": "136225"},
            "lstup_time": "1900-01-01 00:00:00",
        }

    def test_trims_headers_filters_futcom_and_keeps_option_type_xx(self):
        """Select the nearest standard future despite padded headers and XX marker."""
        self.provider.get_quotes.return_value = [self._quote_row()]

        result = self.service.get_quote()

        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[{"exchange_segment": "mcx_fo", "instrument_token": "500"}],
            quote_type="all",
        )
        self.assertEqual(result.contract_trading_symbol, "GOLD04DEC26FUT")
        self.assertEqual(result.instrument_token, "500")
        self.assertEqual(result.contract_expiry_timestamp, self.now + timedelta(days=19))
        self.assertEqual(result.price, Decimal("136705"))
        self.assertEqual(result.absolute_change, Decimal("480"))
        self.assertEqual(result.percentage_change, Decimal("0.3524"))
        self.assertEqual(result.quotation_unit, "INR per 10 grams")

    def test_expired_and_non_futures_are_not_selected(self):
        """Ignore expired contracts, options, and other Gold product variants."""
        self._save_rows([self.rows[2], self.rows[3], self.rows[4], self.rows[1]])
        self.provider.get_quotes.return_value = [self._quote_row(token="501")]
        result = self.service.get_quote()
        self.assertEqual(result.instrument_token, "501")

    def test_retrieval_time_is_utc_and_1900_provider_time_is_ignored(self):
        """Keep application retrieval time separate from the placeholder quote time."""
        self.provider.get_quotes.return_value = [self._quote_row()]
        result = self.service.get_quote()
        self.assertEqual(result.retrieved_at, self.now)
        self.assertFalse(hasattr(result, "market_data_timestamp"))

    def test_optional_ohlc_is_parsed_without_using_close_as_previous_close(self):
        """Return open high and low values while excluding OHLC close from the model."""
        self.provider.get_quotes.return_value = [self._quote_row()]
        result = self.service.get_quote()
        self.assertEqual(result.open, Decimal("136000"))
        self.assertEqual(result.high, Decimal("137000"))
        self.assertEqual(result.low, Decimal("135800"))
        self.assertFalse(hasattr(result, "previous_close"))

    def test_missing_master_and_missing_eligible_contract(self):
        """Report a missing local master and a master containing no active future."""
        self.store._path_for_segment("mcx_fo").unlink()
        with self.assertRaisesRegex(GoldFuturesMasterError, "missing"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

        self._save_rows([self.rows[2]])
        with self.assertRaisesRegex(GoldFuturesMasterError, "No eligible"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

    def test_missing_duplicate_and_unexpected_quotes_are_rejected(self):
        """Reject absent, duplicate, and additional response records."""
        for response, expected in (
            ([], "no quote"),
            ([self._quote_row(), self._quote_row()], "duplicate"),
            ([self._quote_row(), self._quote_row(token="other")], "additional"),
        ):
            with self.subTest(expected=expected):
                self.provider.get_quotes.return_value = response
                with self.assertRaisesRegex(GoldFuturesDataError, expected):
                    self.service.get_quote()

    def test_quote_identity_mismatch_is_rejected(self):
        """Require the returned token and exchange to match the selected future."""
        self.provider.get_quotes.return_value = [self._quote_row(token="wrong")]
        with self.assertRaisesRegex(GoldFuturesDataError, "identity"):
            self.service.get_quote()
        self.provider.get_quotes.return_value = [self._quote_row(exchange="nse_cm")]
        with self.assertRaisesRegex(GoldFuturesDataError, "exchange"):
            self.service.get_quote()

    def test_invalid_ltp_and_required_numeric_values_are_rejected(self):
        """Reject nonpositive or malformed LTP and invalid required change values."""
        for field, value in (("ltp", "0"), ("ltp", "NaN"), ("ltp", "bad"), ("change", "Infinity"), ("per_change", None)):
            with self.subTest(field=field, value=value):
                row = self._quote_row()
                row[field] = value
                self.provider.get_quotes.return_value = [row]
                with self.assertRaises(GoldFuturesDataError):
                    self.service.get_quote()

    def test_invalid_optional_ohlc_is_unavailable(self):
        """Treat malformed optional OHLC values as unavailable fields."""
        row = self._quote_row()
        row["ohlc"]["high"] = "NaN"
        self.provider.get_quotes.return_value = [row]
        self.assertIsNone(self.service.get_quote().high)

    def test_invalid_response_and_provider_failure_are_sanitized(self):
        """Reject malformed responses and avoid exposing provider exception details."""
        self.provider.get_quotes.return_value = {"stat": "Not_Ok", "emsg": "secret"}
        with self.assertRaises(GoldFuturesProviderError) as caught:
            self.service.get_quote()
        self.assertNotIn("secret", str(caught.exception))
        self.provider.get_quotes.side_effect = RuntimeError("credential detail")
        with self.assertRaises(GoldFuturesProviderError) as caught:
            self.service.get_quote()
        self.assertNotIn("credential detail", str(caught.exception))

    def test_timezone_aware_clock_is_required(self):
        """Reject naive clock values because they cannot safely gate expiry eligibility."""
        service = GoldFuturesQuoteService(
            self.store, self.instrument_service, self.provider,
            clock=lambda: datetime(2026, 11, 15),
        )
        with self.assertRaisesRegex(GoldFuturesDataError, "timezone-aware"):
            service.get_quote()
        self.provider.get_quotes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
