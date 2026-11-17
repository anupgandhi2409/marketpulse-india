"""Compatibility tests for the retained Economic Times article mapping."""

from unittest import TestCase

from marketpulse.reports.news import build_report_article_section
from marketpulse.reports.snapshot import (
    ReportArticle,
    ReportArticleCategory,
    ReportArticlePublisher,
    ReportArticleStatus,
)


class ReportNewsTests(TestCase):
    """Verify that one optional ET article remains source-attributed."""

    def test_builds_supported_article_section(self):
        """Map one source article while preserving link and attribution."""
        from datetime import datetime, timezone
        from marketpulse.data.market_news import MarketNewsCategory, MarketNewsItem

        retrieved = datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc)
        item = MarketNewsItem(
            "et_stock_market_news", "The Economic Times", "Synthetic market headline",
            "https://economictimes.indiatimes.com/markets/stocks/synthetic.cms",
            MarketNewsCategory.INDIAN_MARKETS, retrieved, retrieved,
        )
        article = ReportArticle(
            item.headline, item.article_url, ReportArticleCategory.STOCK_MARKET_NEWS,
            item.retrieved_at, publisher=ReportArticlePublisher.ECONOMIC_TIMES,
        )
        section = build_report_article_section(
            ReportArticleCategory.STOCK_MARKET_NEWS,
            ReportArticleStatus.AVAILABLE,
            (article,),
            retrieved_at=retrieved,
        )
        self.assertEqual(section.articles, (article,))
        self.assertEqual(section.articles[0].publisher, ReportArticlePublisher.ECONOMIC_TIMES)
