"""Tests for Morning report snapshot validation."""

from dataclasses import fields
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest import TestCase

from marketpulse.analytics.market_movers import StockChange
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.market_movers_service import MarketMoversResult, Nifty50Quote
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import SectorPerformanceService
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.year_high_service import StocksAt52WeekHighResult
from marketpulse.reports.snapshot import (
    CoverageMetadata,
    ReportSection,
    ReportSnapshot,
    ReportSnapshotError,
    SectionStatus,
)

NOW = datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc)


def _section(data, *, status=SectionStatus.AVAILABLE, coverage=None, retrieved_at=NOW):
    """Build a fixture section with stable source and retrieval metadata."""
    return ReportSection(status, data, "Kotak Neo", retrieved_at, coverage)


def _snapshot(**overrides):
    """Build a complete fixture snapshot from retained market-data models."""
    names = ("NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "SENSEX")
    indices = tuple(IndexQuote(name, "NSE", "Kotak Neo", Decimal("100"), name) for name in names)
    sectors = tuple(SectorPerformance(name, Decimal("100"), Decimal("0"), "Kotak Neo", NOW) for name, _ in SectorPerformanceService.SECTORS)
    quotes = tuple(Nifty50Quote(str(i), f"S{i}", f"S{i}-EQ", "nse_cm", Decimal("100"), Decimal("1"), Decimal("1")) for i in range(100))
    movers = MarketMoversResult((StockChange("S0", Decimal("1")),), (StockChange("S1", Decimal("-1")),), "NIFTY 100", 100, 100, True, NOW, quotes)
    highs = StocksAt52WeekHighResult("NIFTY 100", 100, 100, True, NOW, ())
    gold = GoldFuturesQuote("GOLD", "MCX", "GOLD-FUT", "1", Decimal("70000"), "₹ per 10 grams", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo")
    silver = SilverFuturesQuote("SILVER", "MCX", "SILVER-FUT", "2", Decimal("170000"), "₹ per kg", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo")
    crude = CommodityFuturesQuote("CRUDEOIL", "MCX", "CRUDEOIL-FUT", "4", Decimal("8500"), "₹ per barrel", Decimal("10"), Decimal("0.1"), None, None, None, NOW, NOW, "Kotak Neo")
    sections = {
        "report_date": date(2026, 11, 17), "generated_at": NOW,
        "indices": _section(indices, coverage=CoverageMetadata(6, 6, True)),
        "sectors": _section(sectors, coverage=CoverageMetadata(12, 12, True)),
        "market_movers": _section(movers, coverage=CoverageMetadata(100, 100, True)),
        "stocks_at_52_week_high": _section(highs, coverage=CoverageMetadata(100, 100, True)),
        "gold": _section(gold), "silver": _section(silver), "crude_oil": _section(crude),
    }
    sections.update(overrides)
    return ReportSnapshot(**sections)


class ReportSnapshotTests(TestCase):
    """Verify required Morning sections and accurate source metadata."""

    def test_snapshot_contains_only_retained_sections(self):
        """Keep the Morning report model free of removed feature fields."""
        snapshot = _snapshot()
        names = {field.name for field in fields(snapshot)}
        self.assertEqual(len(names), 10)
        self.assertEqual(
            names,
            {
                "report_date", "generated_at", "indices", "sectors", "market_movers",
                "stocks_at_52_week_high", "gold", "silver", "crude_oil", "source_news",
            },
        )

    def test_naive_generation_timestamp_is_rejected(self):
        """Require a timezone-aware report timestamp."""
        with self.assertRaises(ReportSnapshotError):
            _snapshot(generated_at=datetime(2026, 11, 17, 7, 0))

    def test_all_required_sections_need_explicit_status(self):
        """Reject a snapshot missing a retained market-data section."""
        with self.assertRaises(ReportSnapshotError):
            _snapshot(sectors=None)

    def test_incomplete_coverage_cannot_be_available(self):
        """Keep incomplete sector and NIFTY 100 data from appearing complete."""
        snapshot = _snapshot()
        with self.assertRaises(ReportSnapshotError):
            _snapshot(sectors=ReportSection(SectionStatus.AVAILABLE, snapshot.sectors.data, "Kotak Neo", NOW, CoverageMetadata(12, 11, False)))

    def test_zero_qualifier_high_scan_is_valid(self):
        """Accept a fully covered high scan with no qualifying stocks."""
        self.assertEqual(_snapshot().stocks_at_52_week_high.data.stocks, ())

    def test_unavailable_sections_keep_safe_reason(self):
        """Represent failed collection explicitly without fabricated data."""
        section = ReportSection(SectionStatus.UNAVAILABLE, unavailable_reason="Quote unavailable.")
        self.assertIsNone(section.data)
