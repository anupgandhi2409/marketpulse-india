import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from marketpulse.config.settings import Settings
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.crude_oil_futures_quote_service import (
    CrudeOilFuturesDataError,
    CrudeOilFuturesMasterError,
    CrudeOilFuturesProviderError,
    CrudeOilFuturesQuoteService,
)
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore


class CrudeOilFuturesQuoteServiceTests(unittest.TestCase):
    """Verify standard CRUDEOIL selection and Kotak quote validation."""

    HEADERS = [
        "pSymbol", "pExchSeg", "pSymbolName", "pInstType", "pTrdSymbol",
        "pOptionType", "lExpiryDate ", "lLotSize", "pTrdUnits", "pPriceUnits",
        "pDeliveryUnits", "dPriceQuatation ",
    ]

    def setUp(self):
        """Create a temporary MCX master and fixed-time mocked provider."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(settings)
        self.provider = Mock()
        self.now = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
        self.rows = [
            self._row("573422", "CRUDEOIL19NOV26FUT", self.now + timedelta(days=4)),
            self._row("576264", "CRUDEOIL18DEC26FUT", self.now + timedelta(days=33)),
            self._row("573421", "CRUDEOIL13NOV26FUT", self.now - timedelta(days=2)),
            self._row("573423", "CRUDEOILM19NOV26FUT", self.now + timedelta(days=4), pSymbolName="CRUDEOILM"),
            self._row("573424", "CRUDEOIL19NOV26CE", self.now + timedelta(days=4), pInstType="OPTFUT", pOptionType="CE"),
            self._row("573425", "CRUDEOIL19NOV26FUT-BSE", self.now + timedelta(days=4), pExchSeg="bse_fo"),
        ]
        self._save_rows(self.rows)
        self.instrument_service = InstrumentMasterService(self.store, InstrumentMasterParser())
        self.service = CrudeOilFuturesQuoteService(
            self.store, self.instrument_service, self.provider, clock=lambda: self.now
        )

    def _row(self, token, trading_symbol, expiry, **overrides):
        """Build a raw MCX future row with a Unix-seconds expiry."""
        row = {
            "pSymbol": token,
            "pExchSeg": "mcx_fo",
            "pSymbolName": "CRUDEOIL",
            "pInstType": "FUTCOM",
            "pTrdSymbol": trading_symbol,
            "pOptionType": "XX",
            "lExpiryDate ": str(int(expiry.timestamp())),
            "lLotSize": "100",
            "pTrdUnits": "BBL",
            "pPriceUnits": "BBL",
            "pDeliveryUnits": "BBL",
            "dPriceQuatation ": "1",
        }
        row.update(overrides)
        return row

    def _save_rows(self, rows):
        """Persist CSV fixtures while preserving padded source headers."""
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=self.HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        self.store.save_csv("mcx_fo", output.getvalue())

    @staticmethod
    def _quote(token="573422", exchange="mcx_fo"):
        """Return a sanitized quote fixture including the known placeholder timestamp."""
        return {
            "exchange_token": token,
            "display_symbol": "CRUDEOIL19NOV26FUT-IN",
            "exchange": exchange,
            "ltp": "8369",
            "change": "69",
            "per_change": "0.8313",
            "ohlc": {"open": "8299", "high": "8455", "low": "8278", "close": "8300"},
            "lstup_time": "1900-01-01 00:00:00",
        }

    def test_selects_nearest_standard_contract_and_requests_once(self):
        """Trim headers, accept XX, filter variants, and request the selected token once."""
        self.provider.get_quotes.return_value = [self._quote()]
        result = self.service.get_quote()

        self.assertIsInstance(result, CommodityFuturesQuote)
        self.assertEqual(result.commodity, "CRUDEOIL")
        self.assertEqual(result.exchange, "MCX")
        self.assertEqual(result.contract_trading_symbol, "CRUDEOIL19NOV26FUT")
        self.assertEqual(result.instrument_token, "573422")
        self.assertEqual(result.contract_expiry_timestamp, self.now + timedelta(days=4))
        self.assertEqual(result.quotation_unit, "INR per barrel")
        self.assertEqual(result.price, Decimal("8369"))
        self.assertEqual(result.absolute_change, Decimal("69"))
        self.assertEqual(result.percentage_change, Decimal("0.8313"))
        self.assertEqual((result.open, result.high, result.low), tuple(map(Decimal, ("8299", "8455", "8278"))))
        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[{"exchange_segment": "mcx_fo", "instrument_token": "573422"}],
            quote_type="all",
        )

    def test_expired_and_nonstandard_contracts_are_excluded(self):
        """Ignore expired contracts, minis, options, and contracts from other segments."""
        self._save_rows([self.rows[2], self.rows[3], self.rows[4], self.rows[5]])
        with self.assertRaisesRegex(CrudeOilFuturesMasterError, "No eligible"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

    def test_expiry_metadata_must_be_valid(self):
        """Raise a sanitized master error when an eligible row has malformed expiry data."""
        bad = self._row("600", "CRUDEOILBADFUT", self.now + timedelta(days=8))
        bad["lExpiryDate "] = "not-an-epoch"
        self._save_rows([bad])
        with self.assertRaisesRegex(CrudeOilFuturesMasterError, "invalid expiry"):
            self.service.get_quote()
        self.provider.get_quotes.assert_not_called()

    def test_quote_identity_mismatch_is_rejected(self):
        """Require the provider token and exchange to match the selected contract."""
        self.provider.get_quotes.return_value = [self._quote(token="wrong")]
        with self.assertRaisesRegex(CrudeOilFuturesDataError, "identity"):
            self.service.get_quote()
        self.provider.get_quotes.return_value = [self._quote(exchange="nse_cm")]
        with self.assertRaisesRegex(CrudeOilFuturesDataError, "exchange"):
            self.service.get_quote()

    def test_zero_and_negative_changes_are_valid_finite_decimals(self):
        """Preserve signed and zero change values as Decimal numbers."""
        for change, percentage in (("0", "0"), ("-5", "-0.1")):
            with self.subTest(change=change):
                row = self._quote()
                row["change"] = change
                row["per_change"] = percentage
                self.provider.get_quotes.return_value = [row]
                result = self.service.get_quote()
                self.assertEqual(result.absolute_change, Decimal(change))
                self.assertEqual(result.percentage_change, Decimal(percentage))

    def test_missing_or_invalid_required_numeric_fields_are_rejected(self):
        """Require positive finite LTP and finite absolute and percentage changes."""
        for field, value in (
            ("ltp", "0"), ("ltp", "NaN"), ("ltp", None),
            ("change", "Infinity"), ("per_change", "invalid"),
        ):
            with self.subTest(field=field, value=value):
                row = self._quote()
                row[field] = value
                self.provider.get_quotes.return_value = [row]
                with self.assertRaises(CrudeOilFuturesDataError):
                    self.service.get_quote()

    def test_optional_ohlc_ignores_close_and_placeholder_time(self):
        """Parse optional open high low without treating close or placeholder time specially."""
        self.provider.get_quotes.return_value = [self._quote()]
        result = self.service.get_quote()
        self.assertEqual(result.open, Decimal("8299"))
        self.assertEqual(result.high, Decimal("8455"))
        self.assertEqual(result.low, Decimal("8278"))
        self.assertFalse(hasattr(result, "previous_close"))
        self.assertFalse(hasattr(result, "market_data_timestamp"))

    def test_missing_duplicate_and_unexpected_responses_are_rejected(self):
        """Reject missing, duplicate, malformed, and non-list quote responses."""
        for response in ([], [self._quote(), self._quote()], ["bad"], {"error": "hidden"}):
            with self.subTest(response_type=type(response).__name__):
                self.provider.get_quotes.return_value = response
                with self.assertRaises((CrudeOilFuturesDataError, CrudeOilFuturesProviderError)):
                    self.service.get_quote()

    def test_provider_failure_message_is_suppressed(self):
        """Wrap provider failures without including their potentially sensitive messages."""
        self.provider.get_quotes.side_effect = RuntimeError("credential detail")
        with self.assertRaises(CrudeOilFuturesProviderError) as caught:
            self.service.get_quote()
        self.assertNotIn("credential detail", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
