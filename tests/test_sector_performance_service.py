import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from marketpulse.data.sector_performance_service import (
    SectorPerformanceDataError,
    SectorPerformanceProviderError,
    SectorPerformanceService,
)


class SectorPerformanceServiceTests(unittest.TestCase):
    """Verify complete sector quote retrieval and validation."""

    def setUp(self):
        """Create a service with a mocked Kotak quote provider."""
        self.provider = Mock()
        self.retrieved_at = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
        self.service = SectorPerformanceService(
            self.provider, clock=lambda: self.retrieved_at
        )
        self.rows = [
            self.row(identifier, str(index + 100), str(index - 5), display=f"{identifier}-IN")
            for index, (_, identifier) in enumerate(self.service.SECTORS)
        ]

    @staticmethod
    def row(identifier, ltp, change, exchange="nse_cm", display=None):
        """Build a mock quote with an independently formatted display label."""
        return {
            "exchange_token": identifier,
            "display_symbol": display or identifier,
            "exchange": exchange,
            "ltp": ltp,
            "per_change": change,
            "lstup_time": "1900-01-01 00:00:00",
        }

    def test_retrieves_all_sectors_once_and_returns_display_order(self):
        """Validate the one-batch request, full coverage, values, and ordering."""
        self.provider.get_quotes.return_value = list(reversed(self.rows))

        result = self.service.get_sector_performance()

        self.provider.get_quotes.assert_called_once_with(
            instrument_tokens=[
                {"exchange_segment": "nse_cm", "instrument_token": identifier}
                for _, identifier in self.service.SECTORS
            ],
            quote_type="all",
        )
        self.assertEqual([item.sector_name for item in result], [n for n, _ in self.service.SECTORS])
        self.assertEqual(result[0].index_value, Decimal("100"))
        self.assertEqual(result[0].percent_change, Decimal("-5"))
        self.assertEqual(result[5].percent_change, Decimal("0"))
        self.assertEqual(result[-1].percent_change, Decimal("6"))
        self.assertEqual(result[0].source, "Kotak Neo")
        self.assertTrue(all(item.retrieved_at == self.retrieved_at for item in result))

    def test_missing_or_duplicate_rows_are_rejected(self):
        """Reject incomplete coverage and repeated instrument identities."""
        self.provider.get_quotes.return_value = self.rows[:-1]
        with self.assertRaisesRegex(SectorPerformanceDataError, "12 records"):
            self.service.get_sector_performance()
        duplicate_rows = self.rows[:-1] + [self.rows[0]]
        self.provider.get_quotes.return_value = duplicate_rows
        with self.assertRaisesRegex(SectorPerformanceDataError, "duplicate"):
            self.service.get_sector_performance()

    def test_wrong_exchange_or_unexpected_token_is_rejected(self):
        """Reject records that do not match the requested token and exchange."""
        self.rows[0]["exchange"] = "bse_cm"
        self.provider.get_quotes.return_value = self.rows
        with self.assertRaisesRegex(SectorPerformanceDataError, "unexpected identity"):
            self.service.get_sector_performance()
        self.rows[0]["exchange"] = "nse_cm"
        self.rows[0]["exchange_token"] = "Other Index"
        self.provider.get_quotes.return_value = self.rows
        with self.assertRaisesRegex(SectorPerformanceDataError, "unexpected identity"):
            self.service.get_sector_performance()

    def test_malformed_or_missing_numeric_fields_are_rejected(self):
        """Require finite numeric LTP and percentage-change values."""
        for field, value in (("ltp", "NaN"), ("ltp", "0"), ("per_change", "Infinity"), ("per_change", None)):
            with self.subTest(field=field, value=value):
                rows = [dict(row) for row in self.rows]
                rows[0][field] = value
                self.provider.get_quotes.return_value = rows
                with self.assertRaises(SectorPerformanceDataError):
                    self.service.get_sector_performance()

    def test_non_list_response_and_provider_failure_are_sanitized(self):
        """Reject unexpected response structures and suppress provider details."""
        self.provider.get_quotes.return_value = {"stat": "Not_Ok", "emsg": "secret"}
        with self.assertRaises(SectorPerformanceProviderError) as caught:
            self.service.get_sector_performance()
        self.assertNotIn("secret", str(caught.exception))
        self.provider.get_quotes.return_value = "unexpected"
        with self.assertRaises(SectorPerformanceDataError):
            self.service.get_sector_performance()
        self.provider.get_quotes.side_effect = RuntimeError("credential detail")
        with self.assertRaises(SectorPerformanceProviderError) as caught:
            self.service.get_sector_performance()
        self.assertNotIn("credential detail", str(caught.exception))

    def test_placeholder_provider_timestamp_is_not_exposed(self):
        """Use retrieval time and omit the invalid 1900 provider placeholder."""
        self.provider.get_quotes.return_value = self.rows
        result = self.service.get_sector_performance()
        self.assertTrue(all(item.retrieved_at.year == 2026 for item in result))
        self.assertFalse(any(hasattr(item, "market_data_timestamp") for item in result))


if __name__ == "__main__":
    unittest.main()
