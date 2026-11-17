"""Tests for Morning PDF structure and source-safe quote presentation."""

import re
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest import TestCase
from zoneinfo import ZoneInfo

from marketpulse.reports.pdf_renderer import (
    PAGE_BG,
    CARD_BG,
    SECTOR_NEGATIVE,
    SECTOR_NEUTRAL,
    SECTOR_POSITIVE,
    ReportPdfRenderer,
    _SectorTile,
    _base_table_commands,
    _format_decimal,
    _format_percent,
    _format_signed_decimal,
)
from marketpulse.data.sector_performance_service import SectorPerformanceService
from tests.test_report_snapshot import _snapshot


class ReportPdfRendererTests(TestCase):
    """Verify the retained report sections render in Morning order."""

    def test_renders_market_data_and_last_trading_day_sections(self):
        """Include the approved indices, commodities, and prior-day data group."""
        pdf = ReportPdfRenderer().render(_snapshot())
        self.assertTrue(pdf.startswith(b"%PDF-"))
        for label in (
            b"Morning Market Pulse", b"Major Market Indices", b"Commodities",
            b"Last Trading Day Data",
            b"NIFTY 50", b"BANK NIFTY", b"NIFTY IT", b"NIFTY MIDCAP 100",
            b"NIFTY SMALLCAP 100", b"SENSEX", b"Gold", b"Silver", b"Crude Oil",
            b"Sector Performance Heatmap",
            b"NIFTY 100 Top Gainers and Losers", b"NIFTY 100 Stocks at 52-Week High",
            b".960784 .960784 .941176",
        ):
            self.assertIn(label, pdf)
        self.assertNotIn(b"MarketPulse India", pdf)
        self.assertNotIn(b"Source: Kotak Neo", pdf)
        self.assertNotIn(b"Peak Profit Investment", pdf)
        self.assertNotIn(b"Sources, Coverage, and Freshness", pdf)
        self.assertNotIn(
            b"Read-only market data from Kotak Neo REST; market-data freshness is not independently verified.",
            pdf,
        )
        self.assertNotIn(b"F7EAE0", pdf)
        self.assertNotIn(b"INR", pdf)
        page_count = len(re.findall(rb"/Type /Page\b", pdf))
        self.assertGreater(page_count, 0)
        self.assertEqual(pdf.count(b"n 0 0 595.2756 841.8898 re f*"), page_count)
        self.assertIn(b"Change:", pdf)
        self.assertNotIn(b"Point change", pdf)
        self.assertNotIn(b"Percentage change", pdf)
        self.assertNotIn(b"+145.20", pdf)
        self.assertNotIn(b"1. Major Market Indices", pdf)
        self.assertNotIn(b"2. Commodities", pdf)
        self.assertEqual(PAGE_BG.hexval(), "0xf5f5f0")
        self.assertEqual(CARD_BG, PAGE_BG)
        commands = _base_table_commands()
        self.assertFalse(any(command[0] == "ROWBACKGROUNDS" for command in commands))
        self.assertTrue(all(
            command[-1] == PAGE_BG
            for command in commands if command[0] == "BACKGROUND"
        ))

    def test_financial_values_are_formatted_to_exactly_two_decimals(self):
        """Keep signs and grouping while applying the final two-place format."""
        self.assertEqual(_format_decimal(Decimal("25123.4")), "25,123.40")
        self.assertEqual(_format_signed_decimal(Decimal("145.2")), "+145.20")
        self.assertEqual(_format_signed_decimal(Decimal("-86.5")), "-86.50")
        self.assertEqual(_format_percent(Decimal("0.5")), "+0.50%")
        self.assertEqual(_format_percent(Decimal("-1.234")), "-1.23%")

    def test_news_headlines_use_the_adjusted_readable_font_size(self):
        """Increase news headline size and leading without changing its link styling."""
        renderer = ReportPdfRenderer()
        style = renderer._styles["article_headline"]

        self.assertEqual(style.fontSize, 8.3)
        self.assertEqual(style.leading, 10)
        self.assertEqual(style.fontName, renderer._unicode_font)
        self.assertEqual(style.leftIndent, 10)
        self.assertEqual(style.bulletIndent, 2)
        self.assertEqual(style.bulletFontName, renderer._unicode_font)
        self.assertEqual(style.bulletFontSize, 5.5)

    def test_sector_heatmap_uses_full_sign_based_fills_for_all_twelve_sectors(self):
        """Render ordered green, red, and neutral boxes with readable labels."""
        snapshot = _snapshot()
        source = snapshot.sectors.data
        self.assertEqual(tuple(_SectorTile(item).height for item in source), (56,) * 12)
        sectors = tuple(
            replace(item, percent_change=(Decimal("1.2"), Decimal("-0.4"), Decimal("0"))[index % 3])
            for index, item in enumerate(source)
        )
        snapshot = replace(snapshot, sectors=replace(snapshot.sectors, data=sectors))
        pdf = ReportPdfRenderer().render(snapshot)

        sector_start = pdf.index(b"Sector Performance Heatmap")
        sector_text = pdf[sector_start:]
        previous_position = -1
        for name, _ in SectorPerformanceService.SECTORS:
            position = sector_text.index(name.encode())
            self.assertGreater(position, previous_position)
            previous_position = position
        self.assertIn(b"+1.20%", pdf)
        self.assertIn(b"-0.40%", pdf)
        self.assertIn(b"0.00%", pdf)
        self.assertEqual(SECTOR_POSITIVE.hexval(), "0xa6cf98")
        self.assertEqual(SECTOR_NEGATIVE.hexval(), "0xff8080")
        self.assertEqual(SECTOR_NEUTRAL.hexval(), "0xe5e4dd")
        self.assertIn(b".65098 .811765 .596078 rg", pdf)
        self.assertIn(b"1 .501961 .501961 rg", pdf)
        self.assertIn(b".898039 .894118 .866667 rg", pdf)

    def test_index_cards_render_source_changes_with_sign_colors_and_quote_time(self):
        """Show six provider changes in the Morning PDF with accurate fallback states."""
        snapshot = _snapshot()
        signs = (Decimal("2"), Decimal("-1"), Decimal("0"))
        updated = tuple(
            replace(
                item,
                change=signs[index % 3],
                percent_change=signs[index % 3] / Decimal("2"),
                quote_timestamp=snapshot.generated_at,
            )
            for index, item in enumerate(snapshot.indices.data)
        )
        snapshot = replace(snapshot, indices=replace(snapshot.indices, data=updated))
        pdf = ReportPdfRenderer().render(snapshot)

        self.assertIn(b"Change:", pdf)
        self.assertNotIn(b"Point change", pdf)
        self.assertIn(b"+2.00", pdf)
        self.assertIn(b"-1.00", pdf)
        self.assertIn(b"0.00%", pdf)
        self.assertIn(b"Change:", pdf)
        self.assertNotIn(b"Quote updated:", pdf)
        self.assertIn(b".156863 .478431 .270588 rg", pdf)
        self.assertIn(b".760784 .227451 .227451 rg", pdf)
        self.assertIn(b".533333 .533333 .533333 rg", pdf)

    def test_missing_index_changes_render_as_neutral_dashes(self):
        """Keep missing quote changes neutral and avoid inferred previous closes."""
        pdf = ReportPdfRenderer().render(_snapshot())
        self.assertIn(b"Change:", pdf)
        self.assertNotIn(b"Quote update time unavailable", pdf)
        self.assertNotIn(b"prior completed-session status is not independently verified", pdf)

    def test_eight_am_report_keeps_report_time_without_extra_market_claims(self):
        """Retain the report timestamp without adding a session inference."""
        snapshot = _snapshot()
        quote_time = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
        quotes = tuple(replace(item, quote_timestamp=quote_time) for item in snapshot.indices.data)
        snapshot = replace(
            snapshot,
            report_date=date(2026, 11, 17),
            generated_at=datetime(2026, 11, 17, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
            indices=replace(snapshot.indices, data=quotes),
        )

        pdf = ReportPdfRenderer().render(snapshot)

        self.assertIn(b"Generated at: 17 Nov 2026 08:00:00 IST", pdf)
        self.assertNotIn(b"Quote updated:", pdf)
        self.assertNotIn(b"prior completed-session status is not independently verified", pdf)

