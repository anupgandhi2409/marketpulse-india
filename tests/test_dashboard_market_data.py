import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from marketpulse.analytics.market_movers import StockChange
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.index_quote_service import IndexQuoteProviderError
from marketpulse.data.kotak_neo import KotakConfigurationError
from marketpulse.data.market_movers_service import Nifty50Quote
from marketpulse.dashboard.market_data import (
    INDEX_NAMES,
    DashboardMarketData,
    _market_mover_row,
    refresh_market_data,
    safe_error_message,
)


class DashboardMarketDataTests(unittest.TestCase):
    """Verify index refresh and safe snapshot retention without stock watchlists."""

    def setUp(self):
        """Create a mocked index service and stable retrieval timestamp."""
        self.indices = Mock()
        self.clock = Mock(
            return_value=datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)
        )
        self.index_quote = IndexQuote(
            "NIFTY 50", "NSE", "Kotak Neo", Decimal("24500.50"), "Nifty 50"
        )

    def test_refresh_requests_only_indices_and_stores_retrieval_time(self):
        """Refresh the six configured indices once and retain their retrieval time."""
        self.indices.get_quotes.return_value = [self.index_quote]

        outcome = refresh_market_data(self.indices, clock=self.clock)

        self.indices.get_quotes.assert_called_once_with(list(INDEX_NAMES))
        self.assertEqual(outcome.market_data.index_quotes, (self.index_quote,))
        self.assertEqual(
            outcome.market_data.index_retrieved_at,
            datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc).astimezone(),
        )
        self.assertFalse(hasattr(outcome.market_data, "equity_quotes"))
        self.assertEqual(outcome.errors, ())

    def test_failed_index_refresh_retains_cached_snapshot_and_hides_provider_text(self):
        """Retain the prior index snapshot and suppress sensitive provider details."""
        old_time = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
        previous = DashboardMarketData(
            index_quotes=(self.index_quote,), index_retrieved_at=old_time
        )
        self.indices.get_quotes.side_effect = IndexQuoteProviderError(
            "authorization token must not appear"
        )

        outcome = refresh_market_data(self.indices, previous=previous, clock=self.clock)

        self.assertIs(outcome.market_data, previous)
        self.assertEqual(outcome.market_data.index_retrieved_at, old_time)
        self.assertEqual(len(outcome.errors), 1)
        self.assertNotIn("authorization token", " ".join(outcome.errors))

    def test_failed_index_batch_preserves_snapshot_with_fixed_error(self):
        """Map unexpected failures to fixed safe copy and keep all prior data."""
        previous = DashboardMarketData(index_quotes=(self.index_quote,))
        self.indices.get_quotes.side_effect = RuntimeError("raw index payload")

        outcome = refresh_market_data(self.indices, previous=previous, clock=self.clock)

        self.assertIs(outcome.market_data, previous)
        self.assertEqual(len(outcome.errors), 1)
        self.assertNotIn("raw index payload", " ".join(outcome.errors))
        self.assertIn("previous successful data is retained", outcome.errors[0])

    def test_safe_messages_cover_configuration_and_generic_market_failures(self):
        """Return fixed safe messages without preserving exception payloads."""
        self.assertIn(
            "local environment",
            safe_error_message(KotakConfigurationError("secret"), "market"),
        )
        self.assertNotIn(
            "secret",
            safe_error_message(RuntimeError("secret"), "unknown"),
        )

    def test_mover_table_values_use_exact_two_decimal_formatting(self):
        """Keep dashboard changes signed and prices consistently grouped."""
        quote = Nifty50Quote(
            "123", "SYNTH", "SYNTH-EQ", "nse_cm",
            Decimal("12345.6"), Decimal("1.2"), Decimal("0.5"),
        )
        row = _market_mover_row(1, StockChange("SYNTH", Decimal("1.234")), quote)
        self.assertEqual(row.percent_change, "+1.23%")
        self.assertEqual(row.last_traded_price, "12,345.60")


if __name__ == "__main__":
    unittest.main()
