"""Tests for the retained three-commodity dashboard cards."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.dashboard.market_data import DashboardMarketData, build_commodity_cards

NOW = datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc)


class DashboardCommodityTests(unittest.TestCase):
    """Verify three cards retain source labels, values, and retrieval times."""

    def test_only_gold_silver_and_crude_oil_cards_are_built(self):
        """Keep the approved commodity set and exclude the removed currency card."""
        quotes = DashboardMarketData(
            gold_quote=GoldFuturesQuote("GOLD", "MCX", "GOLD-FUT", "1", Decimal("70000"), "₹ per 10 grams", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo"),
            silver_quote=SilverFuturesQuote("SILVER", "MCX", "SILVER-FUT", "2", Decimal("170000"), "₹ per kg", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo"),
            crude_oil_quote=CommodityFuturesQuote("CRUDEOIL", "MCX", "CRUDE-FUT", "3", Decimal("8500"), "₹ per barrel", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo"),
        )
        cards = build_commodity_cards(quotes)
        self.assertEqual(tuple(card.title for card in cards), ("Gold", "Silver", "Crude Oil"))
        self.assertEqual(tuple(card.retrieved_at for card in cards), (NOW, NOW, NOW))

    def test_missing_and_cached_quotes_are_not_fabricated(self):
        """Show unavailable cards for missing quotes and retain fixed safe errors."""
        cards = build_commodity_cards(DashboardMarketData(), ("Gold futures: unavailable",))
        self.assertEqual(len(cards), 3)
        self.assertTrue(all(card.price == "—" for card in cards))
        self.assertEqual(cards[0].error, "Gold futures: unavailable")

    def test_card_values_use_compact_rupee_and_signed_change_format(self):
        """Keep card values concise and show the provider's validated signs."""
        quote = GoldFuturesQuote("GOLD", "MCX", "GOLD-FUT", "1", Decimal("70000"), "₹ per 10 grams", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo")
        card = build_commodity_cards(DashboardMarketData(gold_quote=quote))[0]
        self.assertEqual(card.price, "₹70,000.00")
        self.assertEqual(card.change, "+10.00 (+0.10%)")
        self.assertEqual(card.direction, "positive")


if __name__ == "__main__":
    unittest.main()
