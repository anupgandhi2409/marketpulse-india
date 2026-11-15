import unittest
from decimal import Decimal

from marketpulse.analytics.market_movers import StockChange, rank_market_movers


class MarketMoversTests(unittest.TestCase):
    """Verify ordering, exclusion rules, and universe coverage labels."""

    def test_ranks_top_ten_in_each_direction(self):
        changes = [(f"S{index:02}", Decimal(index - 10)) for index in range(25)]

        result = rank_market_movers(changes)

        self.assertEqual(len(result.gainers), 10)
        self.assertEqual([row.symbol for row in result.gainers],
                         [f"S{index:02}" for index in range(24, 14, -1)])
        self.assertEqual([row.symbol for row in result.losers],
                         [f"S{index:02}" for index in range(10)])
        self.assertFalse(result.is_complete)

    def test_ties_use_symbol_order_deterministically(self):
        result = rank_market_movers(
            [("ZZZ", 1), ("AAA", 1), ("MMM", -1), ("BBB", -1)], limit=2
        )

        self.assertEqual([row.symbol for row in result.gainers], ["AAA", "ZZZ"])
        self.assertEqual([row.symbol for row in result.losers], ["BBB", "MMM"])

    def test_missing_invalid_and_non_finite_values_are_excluded(self):
        result = rank_market_movers([
            StockChange("A", Decimal("1.25")), ("B", None), ("C", "bad"),
            ("D", "NaN"), ("E", "Infinity"), (" ", 1),
        ])

        self.assertEqual([row.symbol for row in result.gainers], ["A"])
        self.assertEqual(result.observed_count, 1)
        self.assertFalse(result.is_complete)

    def test_insufficient_data_is_explicit_and_never_complete(self):
        result = rank_market_movers([("ONLY", 0)], universe_size=50)

        self.assertEqual(len(result.gainers), 1)
        self.assertEqual(len(result.losers), 1)
        self.assertEqual(result.observed_count, 1)
        self.assertFalse(result.is_complete)

    def test_complete_only_when_every_universe_member_has_valid_data(self):
        result = rank_market_movers([(f"S{index:02}", index) for index in range(3)], universe_size=3)

        self.assertTrue(result.is_complete)

    def test_duplicate_symbols_are_counted_once(self):
        result = rank_market_movers([("A", 1), ("A", -1)], universe_size=2)

        self.assertEqual(result.observed_count, 1)
        self.assertFalse(result.is_complete)


if __name__ == "__main__":
    unittest.main()
