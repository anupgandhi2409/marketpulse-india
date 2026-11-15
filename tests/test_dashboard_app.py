import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from marketpulse.dashboard import app
from marketpulse.dashboard.market_data import (
    CommodityMarketCard,
    DashboardMarketData,
    MarketMoverTableRow,
    MarketMoversTables,
    RefreshOutcome,
    YearHighTable,
    YearHighTableRow,
)
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import SectorPerformanceService


class DashboardAppRefreshTests(unittest.TestCase):
    """Verify Streamlit reruns do not fetch quotes without the manual refresh click."""

    def _streamlit_stub(self, button_value):
        """Create the minimal Streamlit surface needed to exercise the refresh branch."""
        return SimpleNamespace(
            session_state={},
            set_page_config=Mock(),
            title=Mock(),
            caption=Mock(),
            button=Mock(return_value=button_value),
            spinner=Mock(return_value=nullcontext()),
            warning=Mock(),
        )

    def test_rerun_without_refresh_button_does_not_create_services_or_request_data(self):
        """Render retained state without constructing providers or refreshing datasets."""
        streamlit_stub = self._streamlit_stub(False)
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "create_dashboard_services") as create_services,
            patch.object(app, "_render_market_data") as render,
        ):
            app.main()

        create_services.assert_not_called()
        render.assert_called_once_with(DashboardMarketData(), ())
        self.assertEqual(set(streamlit_stub.session_state), {app._DATA_STATE_KEY, app._ERROR_STATE_KEY})

    def test_button_click_refreshes_and_stores_the_new_snapshot(self):
        """Run one full refresh only after the user clicks and store its safe outcome."""
        streamlit_stub = self._streamlit_stub(True)
        services = Mock()
        snapshot = DashboardMarketData()
        errors = ("Gold futures: quote could not be refreshed; cached data is retained.",)
        outcome = RefreshOutcome(snapshot, errors)
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "create_dashboard_services", return_value=services) as create_services,
            patch.object(app, "refresh_dashboard_data", return_value=outcome) as refresh,
            patch.object(app, "_render_market_data") as render,
        ):
            app.main()

        create_services.assert_called_once_with()
        refresh.assert_called_once_with(services, previous=DashboardMarketData())
        self.assertIs(streamlit_stub.session_state[app._DATA_STATE_KEY], snapshot)
        self.assertEqual(streamlit_stub.session_state[app._ERROR_STATE_KEY], errors)
        render.assert_called_once_with(snapshot, errors)

    def test_crude_card_shows_conversion_basis_and_cached_fx_timestamp(self):
        """Identify cached conversion input and label the indicative USD unit and basis."""
        streamlit_stub = SimpleNamespace(
            markdown=Mock(), caption=Mock(), metric=Mock(), warning=Mock()
        )
        retrieved = datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)
        card = CommodityMarketCard(
            section="crude_oil", title="Crude Oil", subtitle="MCX Crude Oil Futures",
            price_label="Price", price="8369 INR per barrel", change="69 (0.8313%)",
            contract_symbol="CRUDEOIL19NOV26FUT", retrieved_at=retrieved, cached=False,
            usd_equivalent="88.09 USD/barrel",
            usd_note="Indicative conversion; market-data freshness not independently verified. "
                     "Basis: USD/INR currency futures.",
            usd_rate_contract_symbol="USDINR26N20FUT", usd_rate_retrieved_at=retrieved,
            usd_rate_cached=True,
        )
        with patch.object(app, "st", streamlit_stub):
            app._render_commodity_card(card)

        captions = [call.args[0] for call in streamlit_stub.caption.call_args_list]
        self.assertTrue(any("Indicative USD equivalent (USD/barrel): 88.09" in item for item in captions))
        self.assertTrue(any("USD/INR currency futures" in item for item in captions))
        self.assertTrue(any("Cached USD/INR rate · Retrieved at" in item for item in captions))
        self.assertTrue(any("freshness not independently verified" in item for item in captions))

    def test_heatmap_renders_ordered_tiles_with_direction_colors_and_ist_time(self):
        """Render 12 signed tiles in rows and show their retrieval time in IST."""
        columns = [SimpleNamespace(markdown=Mock()) for _ in range(4)]
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), columns=Mock(return_value=columns)
        )
        retrieved = datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)
        changes = (Decimal("1"), Decimal("-1"), Decimal("0"))
        sectors = tuple(
            SectorPerformance(
                name, Decimal(20000 + index), changes[index % 3], "Kotak Neo", retrieved
            )
            for index, (name, _) in enumerate(SectorPerformanceService.SECTORS)
        )
        with patch.object(app, "st", streamlit_stub):
            app._render_sector_heatmap(DashboardMarketData(sector_performance=sectors))

        self.assertEqual(streamlit_stub.subheader.call_args.args[0], "NSE Sector Heatmap")
        self.assertEqual(streamlit_stub.columns.call_count, 3)
        self.assertEqual(sum(column.markdown.call_count for column in columns), 12)
        rendered = [
            call.args[0]
            for column in columns
            for call in column.markdown.call_args_list
        ]
        self.assertIn("#166534", rendered[0])
        self.assertIn("+1%", rendered[0])
        self.assertIn("#991b1b", rendered[1])
        self.assertIn("-1%", rendered[1])
        self.assertIn("#374151", rendered[2])
        captions = [call.args[0] for call in streamlit_stub.caption.call_args_list]
        self.assertTrue(any("Retrieved at: 2026-11-15 16:00:00 IST" in item for item in captions))
        self.assertTrue(any("freshness is not independently verified" in item for item in captions))

    def test_heatmap_shows_unavailable_when_no_complete_snapshot_exists(self):
        """Show a clear unavailable state without rendering a partial tile grid."""
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), columns=Mock()
        )
        with patch.object(app, "st", streamlit_stub):
            app._render_sector_heatmap(DashboardMarketData())

        streamlit_stub.info.assert_called_once()
        streamlit_stub.columns.assert_not_called()

    def test_market_movers_tables_render_side_by_side_with_color_and_retrieval_time(self):
        """Render cached gainers and losers with signed colors and IST retrieval time."""
        columns = [SimpleNamespace(markdown=Mock()) for _ in range(2)]
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), columns=Mock(return_value=columns)
        )
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "build_market_movers_tables", return_value=MarketMoversTables(
                gainers=(MarketMoverTableRow(1, "GAIN", "+2.5%", "123.4", "positive"),),
                losers=(MarketMoverTableRow(1, "LOSS", "-1.5%", "98.7", "negative"),),
                retrieved_at=datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc),
                cached=True,
            )),
        ):
            app._render_market_movers(DashboardMarketData(market_movers=object()))

        self.assertEqual(streamlit_stub.subheader.call_args.args[0], "NIFTY 100 – Top Gainers & Losers")
        self.assertEqual(streamlit_stub.columns.call_args.args[0], 2)
        self.assertIn("GAIN", columns[0].markdown.call_args_list[1].args[0])
        self.assertIn("#15803d", columns[0].markdown.call_args_list[1].args[0])
        self.assertIn("+2.5%", columns[0].markdown.call_args_list[1].args[0])
        self.assertIn("LOSS", columns[1].markdown.call_args_list[1].args[0])
        self.assertIn("#b91c1c", columns[1].markdown.call_args_list[1].args[0])
        captions = [call.args[0] for call in streamlit_stub.caption.call_args_list]
        self.assertTrue(any("Cached data · Retrieved at: 2026-11-15 16:00:00 IST" in item for item in captions))
        self.assertTrue(any("freshness is not independently verified" in item for item in captions))

    def test_market_movers_without_valid_result_shows_unavailable(self):
        """Do not render a table when a complete NIFTY 100 result is unavailable."""
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), columns=Mock()
        )
        with patch.object(app, "st", streamlit_stub):
            app._render_market_movers(DashboardMarketData())

        streamlit_stub.info.assert_called_once()
        streamlit_stub.columns.assert_not_called()

    def test_active_dashboard_has_no_initial_equity_watchlist_section(self):
        """Render the retained market panels without any fixed-stock table or heading."""
        columns = [SimpleNamespace(container=Mock(return_value=nullcontext())) for _ in range(4)]
        streamlit_stub = SimpleNamespace(
            info=Mock(), subheader=Mock(), caption=Mock(), dataframe=Mock(),
            columns=Mock(return_value=columns), markdown=Mock(), metric=Mock(), warning=Mock(),
        )
        with patch.object(app, "st", streamlit_stub):
            app._render_market_data(DashboardMarketData())

        headings = [call.args[0] for call in streamlit_stub.subheader.call_args_list]
        self.assertNotIn("Initial Equity Watchlist", headings)
        self.assertFalse(any("Equity" in heading or "Watchlist" in heading for heading in headings))
        self.assertEqual(streamlit_stub.dataframe.call_count, 1)

    def test_year_high_table_shows_cached_prices_and_ist_retrieval_timestamp(self):
        """Render 52-week-high symbols and prices with the original cached retrieval time."""
        retrieved = datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), markdown=Mock()
        )
        table = YearHighTable(
            rows=(YearHighTableRow(1, "AAA", "125.50", "125.50"),),
            retrieved_at=retrieved,
            cached=True,
        )
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "build_year_high_table", return_value=table),
        ):
            app._render_year_highs(DashboardMarketData(stocks_at_52_week_high=object()))

        self.assertEqual(
            streamlit_stub.subheader.call_args.args[0],
            "NIFTY 100 – Stocks at 52-Week Highs",
        )
        html = streamlit_stub.markdown.call_args.args[0]
        self.assertIn("AAA", html)
        self.assertIn("125.50", html)
        self.assertIn("Reported 52-week high (₹)", html)
        captions = [call.args[0] for call in streamlit_stub.caption.call_args_list]
        self.assertTrue(any("Cached data · Retrieved at: 2026-11-15 16:00:00 IST" in value for value in captions))
        self.assertTrue(any("freshness is not independently verified" in value for value in captions))

    def test_year_high_complete_empty_result_has_successful_empty_state(self):
        """Show the requested message for a complete scan with no qualifying stocks."""
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), markdown=Mock()
        )
        table = YearHighTable((), datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc), False)
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "build_year_high_table", return_value=table),
        ):
            app._render_year_highs(DashboardMarketData())

        streamlit_stub.info.assert_called_once_with(
            "No NIFTY 100 stocks currently meet the 52-week-high criterion."
        )
        streamlit_stub.markdown.assert_not_called()

    def test_invalid_year_high_result_shows_unavailable_state(self):
        """Show unavailable rather than treating invalid coverage as an empty scan."""
        streamlit_stub = SimpleNamespace(
            subheader=Mock(), caption=Mock(), info=Mock(), markdown=Mock()
        )
        with (
            patch.object(app, "st", streamlit_stub),
            patch.object(app, "build_year_high_table", return_value=None),
        ):
            app._render_year_highs(DashboardMarketData())

        streamlit_stub.info.assert_called_once_with(
            "NIFTY 100 52-week-high scan unavailable · No complete validated result."
        )
        streamlit_stub.markdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
