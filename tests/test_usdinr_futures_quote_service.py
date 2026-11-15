import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from marketpulse.config.settings import Settings
from marketpulse.data.currency_futures_quote import CurrencyFuturesQuote
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.usdinr_futures_quote_service import (
    UsdInrDataError,
    UsdInrFuturesQuoteService,
    UsdInrMasterError,
    UsdInrProviderError,
)


class UsdInrFuturesQuoteServiceTests(unittest.TestCase):
    """Verify USDINR contract selection and safe quote validation."""

    HEADERS = [
        "pSymbol", "pExchSeg", "pSymbolName", "pInstType", "pTrdSymbol",
        "pOptionType", "lExpiryDate ", "lLotSize", "pTrdUnits", "pPriceUnits",
    ]
    OFFSET = 315_511_200
    IST = ZoneInfo("Asia/Kolkata")

    def setUp(self):
        """Create a temporary cde_fo master and fixed-clock mocked provider."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(settings)
        self.provider = Mock()
        self.now = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
        self.expiry20 = datetime(2026, 11, 20, 14, 0, tzinfo=self.IST)
        self.expiry26 = datetime(2026, 11, 26, 14, 0, tzinfo=self.IST)
        self.rows = [
            self._row("2528", "USDINR26N20FUT", self.expiry20),
            self._row("1584", "USDINR26NOVFUT", self.expiry26),
            self._row("2529", "USDINR26N13FUT", self.now.astimezone(self.IST) - timedelta(days=2)),
            self._row("2530", "USDINR26N20CE", self.expiry20, pInstType="OPTCUR", pOptionType="CE"),
            self._row("2531", "EURINR26N20FUT", self.expiry20, pSymbolName="EURINR"),
            self._row("2532", "USDINR26N20FUT-OTHER", self.expiry20, pExchSeg="nse_fo"),
        ]
        self._save_rows(self.rows)
        self.instrument_service = InstrumentMasterService(self.store, InstrumentMasterParser())
        self.service = UsdInrFuturesQuoteService(
            self.store, self.instrument_service, self.provider, clock=lambda: self.now
        )

    def _row(self, token, trading_symbol, expiry, **overrides):
        """Build one source-shaped currency master row using Kotak's documented epoch offset."""
        row = {
            "pSymbol": token,
            "pExchSeg": "cde_fo",
            "pSymbolName": "USDINR",
            "pInstType": "FUTCUR",
            "pTrdSymbol": trading_symbol,
            "pOptionType": "XX",
            "lExpiryDate ": str(int(expiry.astimezone(timezone.utc).timestamp()) - self.OFFSET),
            "lLotSize": "1",
            "pTrdUnits": "",
            "pPriceUnits": "",
        }
        row.update(overrides)
        return row

    def _save_rows(self, rows):
        """Persist fixture rows with whitespace-padded headers like the downloaded master."""
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=self.HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        self.store.save_csv("cde_fo", stream.getvalue())

    @staticmethod
    def _quote(token="2528", exchange="cde_fo"):
        """Return one verified-shape USDINR quote with the observed weekend timestamp."""
        return {
            "exchange_token": token,
            "display_symbol": "USDINR26N20FUT",
            "exchange": exchange,
            "ltp": "95.005",
            "change": "0",
            "per_change": "0",
            "lstup_time": "Sat Nov 14 2026 12:11:00 GMT+0530 (India Standard Time)",
        }

    def test_selects_nearest_usdinr_future_and_makes_one_request(self):
        """Trim headers, apply cde expiry correction, and exclude options and other pairs."""
        self.provider.get_quotes.return_value = [self._quote()]
        result = self.service.get_quote()

        self.assertIsInstance(result, CurrencyFuturesQuote)
        self.assertEqual(result.currency_pair, "USD/INR")
        self.assertEqual(result.instrument_type, "currency futures")
        self.assertEqual(result.exchange_segment, "cde_fo")
        self.assertEqual(result.contract_trading_symbol, "USDINR26N20FUT")
        self.assertEqual(result.instrument_token, "2528")
        self.assertEqual(result.contract_expiry_timestamp, self.expiry20)
        self.assertEqual(result.quotation_unit, "INR per USD")
        self.assertEqual(result.lot_size, 1)
        self.assertEqual(result.contract_size_usd, Decimal("1000"))
        self.assertEqual(result.price, Decimal("95.005"))
        self.assertEqual(result.absolute_change, Decimal("0"))
        self.assertEqual(result.percentage_change, Decimal("0"))
        self.assertEqual(result.retrieved_at, self.now)
        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[{"exchange_segment": "cde_fo", "instrument_token": "2528"}],
            quote_type="all",
        )

    def test_expiry_offset_is_applied_before_ist_conversion(self):
        """Convert the encoded cde_fo epoch using Kotak's offset and IST timezone."""
        self.provider.get_quotes.return_value = [self._quote()]
        quote = self.service.get_quote()
        self.assertEqual(quote.contract_expiry_timestamp.isoformat(), "2026-11-20T14:00:00+05:30")

    def test_expired_futures_are_excluded(self):
        """Fail without requesting data when only an expired USDINR future remains."""
        self._save_rows([self.rows[2]])
        with self.assertRaisesRegex(UsdInrMasterError, "No eligible"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

    def test_quote_identity_mismatches_are_rejected(self):
        """Require both the selected token and cde_fo exchange in the quote."""
        self.provider.get_quotes.return_value = [self._quote(token="wrong")]
        with self.assertRaisesRegex(UsdInrDataError, "token"):
            self.service.get_quote()
        self.provider.get_quotes.return_value = [self._quote(exchange="nse_fo")]
        with self.assertRaisesRegex(UsdInrDataError, "exchange"):
            self.service.get_quote()

    def test_invalid_decimal_fields_are_rejected_and_zero_changes_are_allowed(self):
        """Accept finite zero changes but reject nonfinite, missing, or nonpositive prices."""
        self.provider.get_quotes.return_value = [self._quote()]
        self.assertEqual(self.service.get_quote().absolute_change, Decimal("0"))
        for field, value in (
            ("ltp", "0"), ("ltp", "NaN"), ("change", "Infinity"),
            ("per_change", "bad"), ("per_change", None),
        ):
            with self.subTest(field=field, value=value):
                row = self._quote()
                row[field] = value
                self.provider.get_quotes.return_value = [row]
                with self.assertRaises(UsdInrDataError):
                    self.service.get_quote()

    def test_malformed_missing_duplicate_and_unexpected_responses_are_rejected(self):
        """Reject non-list, empty, duplicate, extra, and malformed quote responses."""
        for response in (None, {}, [], [self._quote(), self._quote()], ["not a record"]):
            with self.subTest(response_type=type(response).__name__):
                self.provider.get_quotes.return_value = response
                with self.assertRaises((UsdInrDataError, UsdInrProviderError)):
                    self.service.get_quote()

    def test_weekend_provider_time_is_not_claimed_as_quote_timestamp(self):
        """Keep retrieval time separate and omit unverified provider timestamps."""
        self.provider.get_quotes.return_value = [self._quote()]
        quote = self.service.get_quote()
        self.assertEqual(quote.retrieved_at, self.now)
        self.assertFalse(hasattr(quote, "market_data_timestamp"))

    def test_provider_errors_are_sanitized(self):
        """Wrap provider exceptions without including their potentially sensitive message."""
        self.provider.get_quotes.side_effect = RuntimeError("authorization secret")
        with self.assertRaises(UsdInrProviderError) as caught:
            self.service.get_quote()
        self.assertNotIn("authorization secret", str(caught.exception))

    def test_missing_master_is_reported_before_request(self):
        """Raise a domain-specific error when the local cde_fo master is missing."""
        self.store._path_for_segment("cde_fo").unlink()
        with self.assertRaisesRegex(UsdInrMasterError, "missing"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
