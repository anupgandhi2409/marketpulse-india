"""Tests for the Morning-only Streamlit entry point."""

import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
import pandas as pd
from marketpulse.analytics.market_movers import StockChange
from marketpulse.dashboard import app
from marketpulse.dashboard.app import (
    _center_dataframe,
    _format_index_change,
    _market_card_html,
    _market_mover_change_style,
    _styled_market_mover_frame,
)
from marketpulse.dashboard.market_data import (
    DashboardMarketData,
    MarketMoverTableRow,
    MarketMoversTables,
)
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.market_movers_service import MarketMoversResult, Nifty50Quote
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import SectorPerformanceService
from marketpulse.data.year_high_service import StockAt52WeekHigh, StocksAt52WeekHighResult
from marketpulse.reports.snapshot import ReportSection, SectionStatus
from tests.test_report_snapshot import _snapshot


def _synthetic_market_movers() -> MarketMoversResult:
    """Build a complete, ordered NIFTY 100 ranking for dashboard tests."""
    quotes = tuple(
        Nifty50Quote(
            str(index), f"S{index:03d}", f"S{index:03d}-EQ", "nse_cm",
            Decimal("10000") + index, Decimal(index - 50) / Decimal("10"),
            Decimal(index - 50) / Decimal("10"),
        )
        for index in range(100)
    )


def _synthetic_year_highs() -> StocksAt52WeekHighResult:
    """Build a complete synthetic high scan with ordered display rows."""
    return StocksAt52WeekHighResult(
        "NIFTY 100", 100, 100, True,
        datetime(2026, 11, 17, 4, tzinfo=timezone.utc),
        (
            StockAt52WeekHigh("AAA", Decimal("456.78"), Decimal("456.78")),
            StockAt52WeekHigh("BBB", Decimal("123.45"), Decimal("123.40")),
        ),
    )
    gainers = sorted(quotes, key=lambda quote: (-quote.percent_change, quote.symbol))[:10]
    losers = sorted(quotes, key=lambda quote: (quote.percent_change, quote.symbol))[:10]
    return MarketMoversResult(
        tuple(StockChange(item.symbol, item.percent_change) for item in gainers),
        tuple(StockChange(item.symbol, item.percent_change) for item in losers),
        "NIFTY 100", 100, 100, True,
        datetime(2026, 11, 17, 4, tzinfo=timezone.utc), quotes,
    )


class DashboardAppTests(unittest.TestCase):
    """Verify dashboard order, manual PDF controls, and hidden news behavior."""

    def _stub(self):
        """Return a small Streamlit double for one render pass."""
        return SimpleNamespace(
            session_state={}, set_page_config=Mock(), title=Mock(), caption=Mock(),
            subheader=Mock(), checkbox=Mock(return_value=False),
            button=Mock(return_value=False), spinner=Mock(return_value=nullcontext()),
            warning=Mock(), error=Mock(), success=Mock(), download_button=Mock(),
            info=Mock(), dataframe=Mock(), columns=Mock(return_value=[]),
            markdown=Mock(), metric=Mock(),
        )

    def test_initial_render_is_read_only_and_shows_morning_download_area(self):
        """Do not create services or make requests before an explicit user click."""
        streamlit = self._stub()
        with patch.object(app, "st", streamlit), patch.object(app, "create_dashboard_services") as factory, patch.object(app, "_render_market_data") as render:
            app.main()
        factory.assert_not_called()
        render.assert_called_once_with(DashboardMarketData(), ())
        labels = [call.args[0] for call in streamlit.subheader.call_args_list]
        self.assertEqual(labels, ["Generate / Download Morning PDF"])
        self.assertFalse(hasattr(streamlit, "selectbox"))

    def test_report_section_summary_counts_market_sections_only(self):
        """Summarize market-data availability without counting or exposing news."""
        snapshot = _snapshot()
        snapshot = __import__("dataclasses").replace(
            snapshot,
            crude_oil=ReportSection(SectionStatus.UNAVAILABLE, unavailable_reason="Unavailable."),
        )
        available, unavailable = app._report_section_summary(snapshot)
        self.assertEqual(available, 6)
        self.assertEqual(unavailable, ("Crude Oil futures",))

    def test_dashboard_layout_order_and_news_exclusion(self):
        """Render the actual app in-process and keep the approved section order."""
        script = Path(__file__).resolve().parents[1] / "app/marketpulse/dashboard/app.py"
        rendered = AppTest.from_file(str(script)).run()
        self.assertEqual(len(rendered.exception), 0)
        self.assertEqual([item.value for item in rendered.title], ["Morning Market Pulse"])
        self.assertEqual(
            [item.value for item in rendered.subheader],
            [
                "Major Market Indices", "Commodities", "Last Trading Day Data",
                "Sector Performance Heatmap", "NIFTY 100 Top Gainers and Losers",
                "NIFTY 100 Stocks at 52-Week High", "Generate / Download Morning PDF",
            ],
        )
        self.assertFalse(any("Economic Times" in item.value for item in rendered.subheader))
        self.assertEqual(len(rendered.metric), 0)
        card_markup = " ".join(item.value for item in rendered.markdown)
        self.assertEqual(card_markup.count("<article"), 9)

    def test_apptest_renders_synthetic_52_week_high_table(self):
        """Verify AppTest renders the synthetic 52-week-high HTML table."""
        script = Path(__file__).resolve().parents[1] / "app/marketpulse/dashboard/app.py"
        rendered = AppTest.from_file(str(script))
        rendered.session_state[app._DATA_STATE_KEY] = DashboardMarketData(
            market_movers=_synthetic_market_movers(),
            stocks_at_52_week_high=_synthetic_year_highs(),
        )
        rendered.session_state[app._ERROR_STATE_KEY] = ()
        rendered.run()

        self.assertEqual(len(rendered.exception), 0)
        markup = "\n".join(str(item.value) for item in rendered.markdown)
        self.assertEqual(markup.count('<table>'), 1)
        self.assertIn("<th>Stock symbol</th>", markup)
        self.assertIn("<td>AAA</td>", markup)
        self.assertIn("<td>456.78</td>", markup)
        self.assertIn("<td>123.45</td>", markup)

    def test_streamlit_apptest_renders_twelve_colored_sector_tiles(self):
        """Run the dashboard with a synthetic snapshot and verify its heatmap output."""
        script = Path(__file__).resolve().parents[1] / "app/marketpulse/dashboard/app.py"
        sectors = tuple(
            SectorPerformance(
                name,
                Decimal("100"),
                (Decimal("1"), Decimal("-1"), Decimal("0"))[index % 3],
                "Synthetic",
                datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
            )
            for index, (name, _) in enumerate(SectorPerformanceService.SECTORS)
        )
        index_quotes = tuple(
            IndexQuote(
                name,
                exchange,
                "Kotak Neo",
                Decimal("12345.60"),
                name,
                (Decimal("2"), Decimal("-1"), Decimal("0"))[index % 3],
                (Decimal("1"), Decimal("-0.5"), Decimal("0"))[index % 3],
                datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
            )
            for index, (name, exchange) in enumerate(app.INDEX_WATCHLIST)
        )
        rendered = AppTest.from_file(str(script))
        rendered.session_state[app._DATA_STATE_KEY] = DashboardMarketData(
            index_quotes=index_quotes,
            index_retrieved_at=datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
            sector_performance=sectors,
        )
        rendered.session_state[app._ERROR_STATE_KEY] = ()
        rendered.run()

        heatmap_markup = next(element.value for element in rendered.markdown if "minmax(190px" in element.value)
        tiles = heatmap_markup.split("<div style=\"background:")[1:]
        self.assertEqual(len(tiles), 12)
        self.assertIn("gap:8px", heatmap_markup)
        self.assertIn("border-radius:8px", heatmap_markup)
        self.assertIn("color:#292929", heatmap_markup)
        self.assertEqual(["#A6CF98", "#FF8080", "#E5E4DD"], [
            tile.split(";", 1)[0] for tile in tiles[:3]
        ])
        self.assertFalse(any("Economic Times" in element.value for element in rendered.subheader))
        cards_markup = " ".join(element.value for element in rendered.markdown)
        self.assertEqual(cards_markup.count("Change:"), 9)
        self.assertIn("font-weight:400", cards_markup)
        self.assertIn("color:#287A45", cards_markup)
        self.assertIn("color:#C23A3A", cards_markup)
        visible_copy = " ".join(item.value for item in rendered.caption)
        self.assertNotIn("prior completed-session", visible_copy)

    def test_market_mover_change_style_uses_each_value_sign(self):
        """Keep gain/loss cell colors in the Styler while alignment uses native config."""
        self.assertEqual(_market_mover_change_style("+1.25%"), "color: #287A45")
        self.assertEqual(_market_mover_change_style("-1.25%"), "color: #C23A3A")
        self.assertEqual(_market_mover_change_style(Decimal("0")), "color: #888888")
        self.assertEqual(_market_mover_change_style("—"), "color: #888888")
        frame = pd.DataFrame({
            "Point change": ["0.00", "-0.00"],
            "Percentage change": ["0.00%", "-0.00%"],
            "Provider quote value": ["100.00", "100.00"],
            "_direction": ["positive", "negative"],
        })
        rendered = _styled_market_mover_frame(frame).to_html()
        styles = rendered.split("</style>", maxsplit=1)[0]
        self.assertIn("#287A45", styles)
        self.assertIn("#C23A3A", styles)
        self.assertNotIn("_direction", rendered)

    def test_all_three_dashboard_tables_center_headers_and_cells(self):
        """Verify centered compact HTML, escaping, formatting, and colors in all tables."""
        rows = (
            MarketMoverTableRow(1, "SYN1", "+1.25%", "12,345.60", "positive"),
            MarketMoverTableRow(2, "<SYN2>", "-0.75%", "9,876.50", "negative"),
        )
        tables = MarketMoversTables(rows, rows, datetime(2026, 11, 17, 4, tzinfo=timezone.utc), False)
        columns = [SimpleNamespace(markdown=Mock()) for _ in range(2)]
        streamlit = SimpleNamespace(
            subheader=Mock(), caption=Mock(), columns=Mock(return_value=columns),
        )

        with patch.object(app, "st", streamlit), patch.object(
            app, "build_market_movers_tables", return_value=tables
        ):
            app._render_market_movers(DashboardMarketData())

        for column in columns:
            self.assertEqual(column.markdown.call_count, 2)
            markup = column.markdown.call_args.args[0]
            self.assertIn("<th>#</th><th>Stock</th><th>Change</th>", markup)
            self.assertIn('<th>Quote value (₹)</th>', markup)
            self.assertIn("text-align:center", markup)
            self.assertIn("<td>1</td><td>SYN1</td><td", markup)
            self.assertIn("+1.25%</td><td>12,345.60</td>", markup)
            self.assertIn("&lt;SYN2&gt;", markup)
            self.assertIn("-0.75%</td><td>9,876.50</td>", markup)
            self.assertIn('style="color:#287A45"', markup)
            self.assertIn('style="color:#C23A3A"', markup)
            self.assertTrue(column.markdown.call_args.kwargs["unsafe_allow_html"])

        html_table = Mock()
        streamlit = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), markdown=html_table,
        )
        with patch.object(app, "st", streamlit):
            app._render_year_highs(DashboardMarketData(
                stocks_at_52_week_high=_synthetic_year_highs()
            ))
        html_table.assert_called_once()
        high_markup = html_table.call_args.args[0]
        self.assertIn("<th>#</th><th>Stock symbol</th>", high_markup)
        self.assertIn('<th>LTP (₹)</th><th>Reported 52-week high (₹)</th>', high_markup)
        self.assertIn("text-align:center", high_markup)
        self.assertIn("<td>1</td><td>AAA</td><td>456.78</td><td>456.78</td>", high_markup)
        self.assertIn("<td>2</td><td>BBB</td><td>123.45</td><td>123.40</td>", high_markup)
        self.assertTrue(html_table.call_args.kwargs["unsafe_allow_html"])

    def test_index_card_changes_are_signed_colored_and_unavailable_safe(self):
        """Format index point and percentage changes independently to two decimals."""
        positive = IndexQuote("NIFTY 50", "NSE", "Kotak Neo", Decimal("100"), change=Decimal("1.2"), percent_change=Decimal("0.5"))
        negative = IndexQuote("NIFTY BANK", "NSE", "Kotak Neo", Decimal("100"), change=Decimal("-1.2"), percent_change=Decimal("-0.5"))
        zero = IndexQuote("NIFTY IT", "NSE", "Kotak Neo", Decimal("100"), change=Decimal("0"), percent_change=Decimal("0"))

        self.assertEqual(_format_index_change(positive.change), "+1.20")
        self.assertEqual(_format_index_change(positive.percent_change, percentage=True), "+0.50%")
        self.assertEqual(_format_index_change(Decimal("-0.004")), "-0.00")
        self.assertEqual(_format_index_change(Decimal("-0.00")), "0.00")
        self.assertEqual(_format_index_change(None), "—")
        positive_card = _market_card_html("NIFTY 50", "25,387.40", "+96.30 (+0.38%)", "positive")
        negative_card = _market_card_html("Gold", "₹134,502.00", "-37.00 (-0.03%)", "negative")
        zero_card = _market_card_html("Silver", "—", "— (—)", "neutral")
        self.assertIn("color:#287A45", positive_card)
        self.assertIn("color:#C23A3A", negative_card)
        self.assertIn("color:#888888", zero_card)
        self.assertIn("font-weight:400", positive_card)
        self.assertNotIn("<b>", positive_card)
        self.assertIn("Change: — (—)", zero_card)

    def test_dashboard_heatmap_renders_twelve_ordered_filled_tiles(self):
        """Render one full-color tile per validated sector in service order."""
        sectors = tuple(
            SectorPerformance(
                name,
                Decimal("12345.67"),
                (Decimal("1.2"), Decimal("-0.4"), Decimal("0"))[index % 3],
                "Synthetic",
                datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc),
            )
            for index, (name, _) in enumerate(SectorPerformanceService.SECTORS)
        )
        streamlit = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), markdown=Mock(),
        )
        market_data = DashboardMarketData(sector_performance=sectors)
        with patch.object(app, "st", streamlit):
            app._render_sector_heatmap(market_data)

        rendered = streamlit.markdown.call_args.args[0]
        self.assertEqual(rendered.count("border-radius:8px"), 12)
        expected_names = [name for name, _ in SectorPerformanceService.SECTORS]
        positions = [rendered.index(escape(name)) for name in expected_names]
        self.assertEqual(positions, sorted(positions))
        backgrounds = [part.split(";", 1)[0] for part in rendered.split("background:")[1:]]
        self.assertEqual(["#A6CF98", "#FF8080", "#E5E4DD"], backgrounds[:3])
        self.assertIn("+1.20%", rendered)
        self.assertIn("-0.40%", rendered)
        self.assertIn("Index value: 12,345.67", rendered)
        self.assertIn("grid-template-columns:repeat(auto-fit,minmax(190px,1fr))", rendered)
        self.assertIn("gap:8px", rendered)
        self.assertTrue(streamlit.markdown.call_args.kwargs["unsafe_allow_html"])

    def test_dashboard_heatmap_keeps_incomplete_snapshot_unavailable(self):
        """Do not render partial sector data as a complete heatmap."""
        streamlit = SimpleNamespace(subheader=Mock(), caption=Mock(), info=Mock())
        with patch.object(app, "st", streamlit):
            app._render_sector_heatmap(DashboardMarketData())
        streamlit.info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
