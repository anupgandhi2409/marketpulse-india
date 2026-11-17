"""Verify source-neutral market-news model validation and identities."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from marketpulse.data.market_news import (
    MarketNewsCategory,
    MarketNewsCollectionResult,
    MarketNewsItem,
    MarketNewsModelError,
    NewsSourceStatus,
    NewsUnavailableReason,
)


NOW = datetime(2026, 11, 17, 4, 30, tzinfo=timezone.utc)


class MarketNewsItemTests(unittest.TestCase):
    """Check validation and deterministic per-source item identities."""

    def _item(self, **changes):
        """Create a valid item that tests can override with one changed field."""
        values = {
            "source_id": "rbi",
            "publisher": "Reserve Bank of India",
            "headline": "Policy update",
            "article_url": "https://www.rbi.org.in/press/1",
            "category": MarketNewsCategory.ECONOMY_POLICY,
            "published_at": NOW - timedelta(minutes=5),
            "retrieved_at": NOW,
        }
        values.update(changes)
        return MarketNewsItem(**values)

    def test_creates_valid_headline_item(self):
        """Preserve the validated headline and its source provenance."""
        item = self._item()

        self.assertEqual(item.source_id, "rbi")

    def test_accepts_every_supported_category(self):
        """Accept each declared category and reject values outside the enum."""
        for category in MarketNewsCategory:
            with self.subTest(category=category):
                self.assertEqual(self._item(category=category).category, category)
        with self.assertRaises(MarketNewsModelError):
            self._item(category="sentiment")

    def test_preserves_missing_publication_time(self):
        """Keep an unknown publication time as None instead of substituting retrieval time."""
        item = self._item(published_at=None)

        self.assertIsNone(item.published_at)
        self.assertNotEqual(item.published_at, item.retrieved_at)

    def test_rejects_naive_publication_and_retrieval_timestamps(self):
        """Require timezone-aware timestamps wherever time is present."""
        with self.assertRaisesRegex(MarketNewsModelError, "published_at"):
            self._item(published_at=datetime(2026, 11, 17, 4, 25))
        with self.assertRaisesRegex(MarketNewsModelError, "retrieved_at"):
            self._item(retrieved_at=datetime(2026, 11, 17, 4, 30))

    def test_rejects_blank_and_overlong_required_fields(self):
        """Reject blank required strings and headlines beyond the chosen bound."""
        for field in ("source_id", "publisher", "headline", "article_url"):
            with self.subTest(field=field), self.assertRaises(MarketNewsModelError):
                self._item(**{field: "  "})
        with self.assertRaisesRegex(MarketNewsModelError, "headline"):
            self._item(headline="x" * 501)

    def test_rejects_unsafe_or_malformed_urls(self):
        """Accept only HTTPS article links without embedded credentials."""
        unsafe_urls = (
            "http://example.com/article",
            "javascript:alert(1)",
            "https://user:password@example.com/article",
            "https://example.com:99999/article",
            "https://example.com/a b",
            "https://example.com\\@attacker.example/article",
            "https://[invalid/article",
        )
        for url in unsafe_urls:
            with self.subTest(url=url), self.assertRaises(MarketNewsModelError):
                self._item(article_url=url)

    def test_preserves_unicode_headline(self):
        """Retain legitimate Unicode without normalizing or interpreting it."""
        headline = "RBI: भारतीय अर्थव्यवस्था – café 📈"

        self.assertEqual(self._item(headline=headline).headline, headline)

    def test_guid_identity_is_stable_within_a_source(self):
        """Use source plus GUID even when duplicate records have different URLs."""
        first = self._item(source_item_id="story-42")
        duplicate = self._item(
            source_item_id="story-42", article_url="https://www.rbi.org.in/updated/42"
        )

        self.assertEqual(first.deduplication_identity, duplicate.deduplication_identity)

    def test_url_identity_removes_only_fragment_and_keeps_query(self):
        """Normalize host casing and fragments while preserving query parameters."""
        first = self._item(article_url="https://EXAMPLE.com/article?id=7#top")
        same = self._item(article_url="https://example.com/article?id=7#bottom")
        different = self._item(article_url="https://example.com/article?id=8")

        self.assertEqual(first.deduplication_identity, same.deduplication_identity)
        self.assertNotEqual(first.deduplication_identity, different.deduplication_identity)

    def test_identical_headlines_from_distinct_publishers_remain_distinct(self):
        """Keep publisher identity even when source, headline, and URL are the same."""
        first = self._item(source_id="aggregator", publisher="Publisher A")
        second = self._item(source_id="aggregator", publisher="Publisher B")

        self.assertEqual(first.headline, second.headline)
        self.assertNotEqual(first.deduplication_identity, second.deduplication_identity)


class MarketNewsCollectionResultTests(unittest.TestCase):
    """Check success, failure, item counts, provenance, and explicit windows."""

    def _item(self):
        """Create one valid item belonging to the sample source."""
        return MarketNewsItem(
            source_id="sebi",
            publisher="SEBI",
            headline="Circular issued",
            article_url="https://www.sebi.gov.in/circular/1",
            category=MarketNewsCategory.ECONOMY_POLICY,
            published_at=None,
            retrieved_at=NOW,
        )

    def test_successful_empty_collection_is_available_with_zero_items(self):
        """Represent a successful empty response independently from source failure."""
        result = MarketNewsCollectionResult(
            source_id="sebi", retrieved_at=NOW, status=NewsSourceStatus.AVAILABLE
        )

        self.assertEqual(result.items, ())
        self.assertEqual(result.item_count, 0)
        self.assertIsNone(result.unavailable_reason)

    def test_unavailable_source_uses_only_a_sanitized_reason(self):
        """Require a fixed safe reason and prevent raw exception messages."""
        result = MarketNewsCollectionResult(
            source_id="sebi",
            retrieved_at=NOW,
            status=NewsSourceStatus.UNAVAILABLE,
            unavailable_reason=NewsUnavailableReason.TIMEOUT,
        )

        self.assertEqual(result.unavailable_reason.value, "timeout")
        with self.assertRaisesRegex(MarketNewsModelError, "unavailable_reason"):
            MarketNewsCollectionResult(
                source_id="sebi",
                retrieved_at=NOW,
                status=NewsSourceStatus.UNAVAILABLE,
                unavailable_reason="SDK failure: secret token",
            )

    def test_collection_requires_matching_item_source(self):
        """Prevent results from mixing items returned by unrelated sources."""
        with self.assertRaisesRegex(MarketNewsModelError, "source_id"):
            MarketNewsCollectionResult(
                source_id="other",
                retrieved_at=NOW,
                status=NewsSourceStatus.AVAILABLE,
                items=(self._item(),),
            )

    def test_validates_explicit_news_window_boundaries(self):
        """Accept an aware ordered window and reject missing, naive, or reversed bounds."""
        start = NOW - timedelta(hours=2)
        result = MarketNewsCollectionResult(
            source_id="sebi",
            retrieved_at=NOW,
            status=NewsSourceStatus.AVAILABLE,
            items=(self._item(),),
            window_start=start,
            window_end=NOW,
        )
        self.assertEqual(result.item_count, 1)
        with self.assertRaisesRegex(MarketNewsModelError, "provided together"):
            replace(result, window_end=None)
        with self.assertRaisesRegex(MarketNewsModelError, "timezone-aware"):
            replace(result, window_start=datetime(2026, 11, 17, 2, 30), window_end=NOW)
        with self.assertRaisesRegex(MarketNewsModelError, "after its end"):
            replace(result, window_start=NOW, window_end=start)

    def test_unavailable_collection_cannot_claim_items(self):
        """Keep failed source attempts distinct from returned item collections."""
        with self.assertRaisesRegex(MarketNewsModelError, "cannot contain items"):
            MarketNewsCollectionResult(
                source_id="sebi",
                retrieved_at=NOW,
                status=NewsSourceStatus.UNAVAILABLE,
                items=(self._item(),),
                unavailable_reason=NewsUnavailableReason.SOURCE_ERROR,
            )


if __name__ == "__main__":
    unittest.main()
