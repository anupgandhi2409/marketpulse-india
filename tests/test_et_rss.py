"""Test the offline Economic Times RSS parser with synthetic XML only."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from marketpulse.data.market_news import (
    MarketNewsCategory,
    MarketNewsItem,
    NewsSourceStatus,
    NewsUnavailableReason,
)
from marketpulse.data.news_sources.et_rss import (
    ET_PUBLISHER,
    ET_SOURCE_ID,
    MAX_ITEMS,
    MAX_XML_BYTES,
    parse_et_rss,
)

RETRIEVED = datetime(2026, 11, 17, 12, 30, tzinfo=timezone.utc)
ARTICLE_URL = "https://economictimes.indiatimes.com/news/synthetic/sample.cms"


def _item(title="Synthetic ET headline", link=ARTICLE_URL, extra=""):
    """Build one original synthetic RSS item without publisher content."""
    return f"<item><title>{title}</title><link>{link}</link>{extra}</item>"


def _feed(*items):
    """Wrap supplied item fragments in one minimal valid RSS channel."""
    return ("<rss version='2.0'><channel><title>Synthetic ET feed</title>"
            + "".join(items) + "</channel></rss>").encode()


class EconomicTimesRssParserTests(unittest.TestCase):
    """Verify RSS mapping, bounded parsing, sanitization, and safe failures."""

    def test_multiple_articles_map_to_existing_models(self):
        """Map required and optional fields with source and retrieval provenance."""
        xml = _feed(
            _item(
                extra=(
                    "<guid>synthetic-guid-1</guid>"
                    "<pubDate>Tue, 17 Nov 2026 17:45:00 +0530</pubDate>"
                    "<description><![CDATA[<p>Synthetic description.</p>]]></description>"
                )
            ),
            _item("Second synthetic headline", "https://economictimes.indiatimes.com/news/synthetic/2.cms"),
        )

        result = parse_et_rss(xml, RETRIEVED, MarketNewsCategory.ECONOMY_POLICY)

        self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(result.source_id, ET_SOURCE_ID)
        self.assertEqual(len(result.items), 2)
        first, second = result.items
        self.assertIsInstance(first, MarketNewsItem)
        self.assertEqual(first.publisher, ET_PUBLISHER)
        self.assertEqual(first.headline, "Synthetic ET headline")
        self.assertEqual(first.article_url, ARTICLE_URL)
        self.assertEqual(first.category, MarketNewsCategory.ECONOMY_POLICY)
        self.assertEqual(first.source_item_id, "synthetic-guid-1")
        self.assertEqual(
            first.published_at,
            datetime(2026, 11, 17, 12, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(first.retrieved_at, RETRIEVED)
        self.assertIsNone(second.source_item_id)
        self.assertIsNone(second.published_at)

    def test_publication_dates_may_be_missing_or_invalid(self):
        """Preserve absent, invalid, and timezone-naive dates as unavailable."""
        result = parse_et_rss(_feed(
            _item(),
            _item("Invalid date", "https://economictimes.indiatimes.com/news/synthetic/2.cms",
                  "<pubDate>not-a-date</pubDate>"),
            _item("Naive date", "https://economictimes.indiatimes.com/news/synthetic/3.cms",
                  "<pubDate>Tue, 17 Nov 2026 17:45:00</pubDate>"),
        ), RETRIEVED)

        self.assertEqual([item.published_at for item in result.items], [None, None, None])

    def test_description_markup_is_ignored(self):
        """Ignore RSS descriptions because the report uses headlines only."""
        xml = _feed(_item(extra=(
            "<description><![CDATA[<p>  Synthetic&nbsp;description &amp; detail. </p>"
            "<script>secretSyntheticScript()</script><style>hiddenSyntheticStyle</style>"
            "<div>Second   sentence.</div>]]></description>"
        )))

        result = parse_et_rss(xml, RETRIEVED)

        self.assertEqual(result.items[0].headline, "Synthetic ET headline")

    def test_dtd_and_entity_declarations_are_rejected_without_expansion(self):
        """Return a sanitized invalid-response result for DTD or entity payloads."""
        payloads = (
            b'<!DOCTYPE rss [<!ENTITY sample "synthetic">]>'
            b'<rss><channel><item><title>&sample;</title></item></channel></rss>',
            b'<!DOCTYPE rss [<!ENTITY sample SYSTEM "https://invalid.example/entity">]>'
            b'<rss><channel><item><title>&sample;</title></item></channel></rss>',
        )

        for xml in payloads:
            with self.subTest(xml=xml):
                result = parse_et_rss(xml, RETRIEVED)
                self.assertEqual(result.status, NewsSourceStatus.UNAVAILABLE)
                self.assertEqual(result.unavailable_reason, NewsUnavailableReason.INVALID_RESPONSE)
                self.assertEqual(result.items, ())

    def test_oversized_xml_is_unavailable(self):
        """Reject XML payloads beyond the explicit parser byte limit."""
        result = parse_et_rss(b" " * (MAX_XML_BYTES + 1), RETRIEVED)

        self.assertEqual(result.status, NewsSourceStatus.UNAVAILABLE)
        self.assertEqual(result.unavailable_reason, NewsUnavailableReason.INVALID_RESPONSE)

    def test_item_limit_is_enforced(self):
        """Reject a feed that exceeds the maximum number of item elements."""
        xml = _feed(*(_item() for _ in range(MAX_ITEMS + 1)))

        result = parse_et_rss(xml, RETRIEVED)

        self.assertEqual(result.status, NewsSourceStatus.UNAVAILABLE)
        self.assertEqual(result.items, ())

    def test_wrong_root_and_missing_channel_are_unavailable(self):
        """Reject XML that is well-formed but does not have RSS channel structure."""
        wrong_root = b"<feed><channel></channel></feed>"
        missing_channel = b"<rss version='2.0'><title>No channel</title></rss>"

        for xml in (wrong_root, missing_channel):
            with self.subTest(xml=xml):
                result = parse_et_rss(xml, RETRIEVED)
                self.assertEqual(result.status, NewsSourceStatus.UNAVAILABLE)
                self.assertEqual(result.unavailable_reason, NewsUnavailableReason.INVALID_RESPONSE)

    def test_empty_valid_feed_is_available_with_zero_items(self):
        """Distinguish a successful empty channel from an invalid response."""
        result = parse_et_rss(_feed(), RETRIEVED)

        self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(result.items, ())
        self.assertEqual(result.item_count, 0)

    def test_invalid_article_urls_are_skipped(self):
        """Skip HTTP, foreign, lookalike, credential, and custom-port links safely."""
        invalid_urls = (
            "http://economictimes.indiatimes.com/news/synthetic/1.cms",
            "https://example.com/news/synthetic/2.cms",
            "https://economictimes.indiatimes.com.example.org/story",
            "https://evil.economictimes.indiatimes.com/story",
            "https://user:secret@economictimes.indiatimes.com/story",
            "https://economictimes.indiatimes.com:8443/story",
            "https://economictimes.indiatimes.com/story\x7funsafe",
        )
        xml = _feed(*(_item(f"Synthetic headline {index}", url)
                      for index, url in enumerate(invalid_urls)))

        result = parse_et_rss(xml, RETRIEVED)

        self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(result.items, ())

    def test_missing_or_invalid_headlines_are_skipped(self):
        """Skip records without usable titles or links instead of inventing values."""
        xml = _feed(
            "<item><title></title><link>" + ARTICLE_URL + "</link></item>",
            "<item><link>" + ARTICLE_URL + "</link></item>",
            "<item><title>Only title</title></item>",
            _item("<b>Synthetic markup title</b>"),
        )

        result = parse_et_rss(xml, RETRIEVED)

        self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(result.items, ())

    def test_unicode_headlines_and_whitespace_are_preserved_safely(self):
        """Normalize spacing while preserving legitimate Unicode headline text."""
        xml = _feed(_item("  संश्लेषित&#160;आर्थिक   समाचार  café "))

        result = parse_et_rss(xml, RETRIEVED)

        self.assertEqual(result.items[0].headline, "संश्लेषित आर्थिक समाचार café")

    def test_retrieval_timestamp_is_required_and_preserved(self):
        """Reject naive retrieval times and retain the exact aware input timestamp."""
        with self.assertRaises(ValueError):
            parse_et_rss(_feed(), datetime(2026, 11, 17, 12, 30))

        result = parse_et_rss(_feed(_item()), RETRIEVED)

        self.assertEqual(result.retrieved_at, RETRIEVED)
        self.assertEqual(result.items[0].retrieved_at, RETRIEVED)

    def test_parser_performs_no_network_calls(self):
        """Parse only supplied XML bytes without opening URLs or network transports."""
        with patch("urllib.request.urlopen", side_effect=AssertionError("network call")) as opener:
            result = parse_et_rss(_feed(_item()), RETRIEVED)

        opener.assert_not_called()
        self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)

    def test_invalid_timestamp_and_category_inputs_are_rejected(self):
        """Reject invalid parser metadata before interpreting the supplied payload."""
        with self.assertRaises(ValueError):
            parse_et_rss(_feed(), RETRIEVED, "economy_policy")


if __name__ == "__main__":
    unittest.main()
