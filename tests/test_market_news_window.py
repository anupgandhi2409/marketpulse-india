"""Verify the Morning-only calendar-based IST news window."""

import unittest
from datetime import datetime, time, timezone

from marketpulse.data.market_news_window import (
    DEFAULT_MORNING_CUTOFF,
    IST,
    MarketNewsWindowPolicyError,
    build_market_news_window,
)


class MarketNewsWindowPolicyTests(unittest.TestCase):
    """Check explicit Morning boundaries without source access."""

    def test_uses_previous_calendar_day_cutoff(self):
        """Start the Morning window at 18:00 IST on the preceding day."""
        generated = datetime(2026, 11, 17, 7, 30, tzinfo=IST)
        start, end = build_market_news_window(generated)
        self.assertEqual(start, datetime(2026, 11, 16, 18, 0, tzinfo=IST))
        self.assertEqual(end, generated)

    def test_converts_utc_and_handles_year_boundary(self):
        """Convert generation time to IST before calculating calendar boundaries."""
        generated = datetime(2027, 1, 1, 2, 0, tzinfo=timezone.utc)
        start, end = build_market_news_window(generated)
        self.assertEqual(start, datetime(2026, 12, 31, 18, 0, tzinfo=IST))
        self.assertEqual(end, datetime(2027, 1, 1, 7, 30, tzinfo=IST))

    def test_accepts_custom_cutoff_and_rejects_invalid_window(self):
        """Apply a supplied local cutoff and reject naive or too-early times."""
        self.assertEqual(DEFAULT_MORNING_CUTOFF, time(18, 0))
        generated = datetime(2026, 11, 17, 8, 0, tzinfo=IST)
        start, end = build_market_news_window(generated, morning_cutoff=time(19, 0))
        self.assertEqual(start, datetime(2026, 11, 16, 19, 0, tzinfo=IST))
        self.assertEqual(end, generated)
        with self.assertRaises(MarketNewsWindowPolicyError):
            build_market_news_window(datetime(2026, 11, 17, 8))
        with self.assertRaises(MarketNewsWindowPolicyError):
            build_market_news_window(generated, morning_cutoff="18:00")

    def test_window_is_deterministic_and_half_open(self):
        """Return stable boundaries for identical explicit timestamps."""
        generated = datetime(2026, 11, 17, 8, 0, tzinfo=IST)
        first = build_market_news_window(generated)
        self.assertEqual(first, build_market_news_window(generated))
        self.assertLess(first[0], first[1])


if __name__ == "__main__":
    unittest.main()
