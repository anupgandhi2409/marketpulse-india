"""Tests for independent Morning data collection and ET news injection."""

from dataclasses import replace
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from marketpulse.data.market_news import MarketNewsCollectionResult, MarketNewsItem, NewsSourceStatus
from marketpulse.data.news_sources.et_acquirer import ET_FEEDS
from marketpulse.reports.collector import ReportDataCollector, ReportDataServices
from marketpulse.reports.snapshot import ReportArticleStatus, SectionStatus
from tests.test_report_snapshot import _snapshot

NOW = datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc)


class ReportDataCollectorTests(TestCase):
    """Verify retained collection behavior and opt-in ET collection."""

    def setUp(self):
        """Prepare typed fixture results for each retained market service."""
        source = _snapshot()
        self.services = ReportDataServices(
            indices=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
            gold=Mock(), silver=Mock(), crude_oil=Mock(),
        )
        self.services.indices.get_quotes.return_value = source.indices.data
        self.services.sectors.get_sector_performance.return_value = source.sectors.data
        self.services.market_movers.iter_universe_quote_batches.return_value = ("shared-batch",)
        self.services.market_movers.get_market_movers.return_value = source.market_movers.data
        self.services.year_high.get_stocks_at_52_week_high.return_value = source.stocks_at_52_week_high.data
        self.services.gold.get_quote.return_value = source.gold.data
        self.services.silver.get_quote.return_value = source.silver.data
        self.services.crude_oil.get_quote.return_value = source.crude_oil.data

    def test_collects_morning_snapshot_and_shared_nifty100_batches(self):
        """Collect required sections and fetch one shared NIFTY 100 batch set."""
        snapshot = ReportDataCollector(self.services, clock=lambda: NOW).collect()
        self.assertEqual(snapshot.report_date.isoformat(), "2026-11-17")
        self.assertEqual(snapshot.generated_at, NOW.astimezone(ZoneInfo("Asia/Kolkata")))
        self.assertEqual(snapshot.indices.status, SectionStatus.AVAILABLE)
        self.services.market_movers.iter_universe_quote_batches.assert_called_once_with()
        self.services.market_movers.get_market_movers.assert_called_once_with(("shared-batch",))
        self.services.year_high.get_stocks_at_52_week_high.assert_called_once_with(("shared-batch",))
        self.assertIsNone(snapshot.source_news)

    def test_service_failure_is_isolated_to_its_section(self):
        """Retain successful data while representing a failed quote section safely."""
        self.services.gold.get_quote.side_effect = RuntimeError("sensitive provider response")
        snapshot = ReportDataCollector(self.services, clock=lambda: NOW).collect()
        self.assertEqual(snapshot.gold.status, SectionStatus.UNAVAILABLE)
        self.assertNotIn("sensitive", snapshot.gold.unavailable_reason)
        self.assertEqual(snapshot.silver.status, SectionStatus.AVAILABLE)

    def test_explicit_et_acquirer_is_called_once_and_maps_three_sections(self):
        """Attach category-correct ET items only when an acquirer is injected."""
        retrieved = NOW
        published = datetime(2026, 11, 16, 19, 0, tzinfo=timezone.utc)
        results = []
        for config in ET_FEEDS.values():
            item = MarketNewsItem(
                config.source_id, "The Economic Times", f"Synthetic {config.report_section}",
                f"https://economictimes.indiatimes.com/synthetic/{config.report_section}.cms",
                config.category, published, retrieved,
            )
            results.append(MarketNewsCollectionResult(config.source_id, retrieved, NewsSourceStatus.AVAILABLE, (item,)))
        acquirer = Mock()
        acquirer.collect.return_value = tuple(results)
        services = replace(self.services, et_news_acquirer=acquirer)
        snapshot = ReportDataCollector(services, clock=lambda: NOW).collect()
        acquirer.collect.assert_called_once_with(retrieved)
        self.assertEqual(snapshot.source_news.stock_market.status, ReportArticleStatus.AVAILABLE)
        self.assertEqual(snapshot.source_news.economic.status, ReportArticleStatus.AVAILABLE)
        self.assertEqual(snapshot.source_news.company.status, ReportArticleStatus.AVAILABLE)
        self.assertEqual(len(snapshot.source_news.stock_market.articles), 1)

    def test_unexpected_et_failure_does_not_interrupt_market_collection(self):
        """Convert an injected ET failure to independent unavailable listing states."""
        acquirer = Mock()
        acquirer.collect.side_effect = RuntimeError("raw detail")
        services = replace(self.services, et_news_acquirer=acquirer)
        snapshot = ReportDataCollector(services, clock=lambda: NOW).collect()
        self.assertEqual(snapshot.indices.status, SectionStatus.AVAILABLE)
        self.assertTrue(all(section.status is ReportArticleStatus.UNAVAILABLE for section in (
            snapshot.source_news.stock_market, snapshot.source_news.economic, snapshot.source_news.company
        )))
