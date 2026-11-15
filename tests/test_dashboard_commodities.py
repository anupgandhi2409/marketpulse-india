import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from marketpulse.config.settings import Settings
from marketpulse.analytics.market_movers import StockChange, rank_market_movers
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.currency_futures_quote import CurrencyFuturesQuote
from marketpulse.data.crude_oil_futures_quote_service import CrudeOilFuturesQuoteService
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.gold_futures_quote_service import GoldFuturesQuoteService
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.silver_futures_quote_service import SilverFuturesQuoteService
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversResult,
    MarketMoversService,
    NIFTY_100_UNIVERSE,
    Nifty50Quote,
)
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.sector_performance_service import SectorPerformanceService
from marketpulse.data.usdinr_futures_quote_service import UsdInrFuturesQuoteService
from marketpulse.data.year_high_service import (
    Nifty10052WeekHighService,
    StockAt52WeekHigh,
    StocksAt52WeekHighResult,
)
from marketpulse.dashboard.market_data import (
    DashboardMarketData,
    DashboardServices,
    build_commodity_cards,
    build_market_movers_tables,
    build_sector_heatmap_tiles,
    build_year_high_table,
    create_dashboard_services,
    format_retrieved_at,
    refresh_dashboard_data,
    safe_error_message,
)


class DashboardCommodityTests(unittest.TestCase):
    """Verify commodity card formatting, caching, and independent refresh behavior."""

    def setUp(self):
        """Build representative source-neutral quote snapshots with fixed UTC timestamps."""
        self.retrieved = datetime(2026, 11, 15, 10, 30, tzinfo=timezone.utc)
        self.gold = GoldFuturesQuote(
            "GOLD", "MCX", "GOLD04DEC26FUT", "500", Decimal("136705"),
            "INR per 10 grams", Decimal("480"), Decimal("0.3524"),
            Decimal("136000"), Decimal("137000"), Decimal("135800"),
            datetime(2026, 12, 4, 23, 59, 59, tzinfo=timezone.utc),
            self.retrieved, "Kotak Neo",
        )
        self.silver = SilverFuturesQuote(
            "SILVER", "MCX", "SILVER04DEC26FUT", "501", Decimal("176309"),
            "INR per kg", Decimal("-2415"), Decimal("-1.3512"),
            None, None, None, datetime(2026, 12, 4, tzinfo=timezone.utc),
            self.retrieved, "Kotak Neo",
        )
        self.usdinr = CurrencyFuturesQuote(
            "USD/INR", "currency futures", "cde_fo", "USDINR26N20FUT", "2528",
            Decimal("95.005"), "INR per USD", 1, Decimal("1000"),
            Decimal("0"), Decimal("0"), datetime(2026, 11, 20, 14, tzinfo=timezone.utc),
            self.retrieved, "Kotak Neo",
        )
        self.crude = CommodityFuturesQuote(
            "CRUDEOIL", "MCX", "CRUDEOIL19NOV26FUT", "573422", Decimal("8369"),
            "INR per barrel", Decimal("69"), Decimal("0.8313"),
            Decimal("8299"), Decimal("8455"), Decimal("8278"),
            datetime(2026, 11, 19, 23, 59, 59, tzinfo=timezone.utc),
            self.retrieved, "Kotak Neo",
        )
        self.snapshot = DashboardMarketData(
            gold_quote=self.gold,
            silver_quote=self.silver,
            usdinr_quote=self.usdinr,
            crude_oil_quote=self.crude,
            sector_performance=self._sector_quotes(),
            market_movers=self._market_movers_result(),
            stocks_at_52_week_high=self._year_high_result(),
        )

    def _sector_quotes(self):
        """Build a fixed complete sector fixture in the service's display order."""
        changes = (Decimal("1.25"), Decimal("-0.50"), Decimal("0"))
        return tuple(
            SectorPerformance(
                name, Decimal(25000 + index), changes[index % len(changes)],
                "Kotak Neo", self.retrieved,
            )
            for index, (name, _) in enumerate(SectorPerformanceService.SECTORS)
        )

    def _market_movers_result(self):
        """Build a complete deterministic NIFTY 100 movers result fixture."""
        quotes = tuple(
            Nifty50Quote(
                instrument_token=str(8000 + index),
                symbol=f"S{index:03}",
                trading_symbol=f"S{index:03}-EQ",
                exchange_segment="nse_cm",
                last_traded_price=Decimal(100 + index),
                change=Decimal("1.00"),
                percent_change=Decimal(index - 50),
            )
            for index in range(NIFTY_100_UNIVERSE.expected_constituent_count)
        )
        rankings = rank_market_movers(
            [(quote.symbol, quote.percent_change) for quote in quotes], universe_size=100
        )
        return MarketMoversResult(
            gainers=rankings.gainers,
            losers=rankings.losers,
            universe_name="NIFTY 100",
            expected_constituent_count=100,
            validated_constituent_count=100,
            coverage_complete=True,
            retrieved_at=self.retrieved,
            quotes=quotes,
        )

    def _year_high_result(self, symbols=("AAA", "BBB", "CCC")):
        """Build complete-coverage high qualifiers with fixed retrieval time."""
        stocks = tuple(
            StockAt52WeekHigh(symbol, Decimal("125.50"), Decimal("125.50"))
            for symbol in symbols
        )
        return StocksAt52WeekHighResult(
            universe_name="NIFTY 100",
            expected_constituent_count=100,
            validated_quote_count=100,
            coverage_complete=True,
            retrieved_at=self.retrieved,
            stocks=stocks,
        )

    def test_four_cards_have_correct_labels_units_changes_and_symbols(self):
        """Format Gold, Silver, USDINR futures, and Crude Oil as four distinct cards."""
        cards = build_commodity_cards(self.snapshot)

        self.assertEqual(len(cards), 4)
        self.assertEqual([card.title for card in cards], ["Gold", "Silver", "USD/INR", "Crude Oil"])
        self.assertEqual(cards[0].price, "136705 INR per 10 grams")
        self.assertEqual(cards[0].change, "480 (0.3524%)")
        self.assertEqual(cards[0].contract_symbol, "GOLD04DEC26FUT")
        self.assertEqual(cards[1].price, "176309 INR per kg")
        self.assertEqual(cards[1].change, "-2415 (-1.3512%)")
        self.assertEqual(cards[1].contract_symbol, "SILVER04DEC26FUT")
        self.assertIn("Currency Futures", cards[2].subtitle)
        self.assertEqual(cards[2].price, "95.005 INR per USD")
        self.assertEqual(cards[2].change, "0 (0%)")
        self.assertEqual(cards[2].contract_symbol, "USDINR26N20FUT")
        self.assertEqual(cards[3].price, "8369 INR per barrel")
        self.assertEqual(cards[3].usd_equivalent, "88.09 USD/barrel")
        self.assertEqual(
            cards[3].usd_note,
            "Indicative conversion; market-data freshness not independently verified. "
            "Basis: USD/INR currency futures.",
        )
        self.assertEqual(cards[3].change, "69 (0.8313%)")
        self.assertEqual(cards[3].usd_rate_contract_symbol, "USDINR26N20FUT")

    def test_invalid_prices_make_only_usd_equivalent_unavailable(self):
        """Reject invalid source prices while preserving the raw INR quote display."""
        for crude_price, rate in (
            (Decimal("NaN"), self.usdinr.price),
            (self.crude.price, Decimal("0")),
            (self.crude.price, Decimal("Infinity")),
        ):
            with self.subTest(crude_price=crude_price, rate=rate):
                snapshot = DashboardMarketData(
                    crude_oil_quote=replace(self.crude, price=crude_price),
                    usdinr_quote=replace(self.usdinr, price=rate),
                )
                crude_card = next(
                    card for card in build_commodity_cards(snapshot)
                    if card.section == "crude_oil"
                )
                self.assertEqual(crude_card.usd_equivalent, "Unavailable")

    def test_missing_usdinr_leaves_crude_inr_available_without_conversion(self):
        """Keep Crude Oil INR display when the currency quote is unavailable."""
        snapshot = DashboardMarketData(crude_oil_quote=self.crude)
        cards = build_commodity_cards(snapshot)
        fx_card = next(card for card in cards if card.section == "usdinr")
        crude_card = next(card for card in cards if card.section == "crude_oil")
        self.assertEqual(fx_card.price, "Unavailable")
        self.assertEqual(crude_card.price, "8369 INR per barrel")
        self.assertEqual(crude_card.usd_equivalent, "Unavailable")

    def test_failed_fx_refresh_keeps_cached_rate_identified_for_conversion(self):
        """Keep the FX snapshot and its original timestamp visible after refresh failure."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        services.gold.get_quote.side_effect = RuntimeError()
        services.silver.get_quote.side_effect = RuntimeError()
        services.usdinr.get_quote.side_effect = RuntimeError()
        services.crude_oil.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.side_effect = RuntimeError()
        services.market_movers.get_market_movers.return_value = self._market_movers_result()
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()
        previous = DashboardMarketData(crude_oil_quote=self.crude, usdinr_quote=self.usdinr)

        outcome = refresh_dashboard_data(services, previous=previous)
        crude_card = next(
            card for card in build_commodity_cards(outcome.market_data, outcome.errors)
            if card.section == "crude_oil"
        )

        self.assertEqual(crude_card.usd_equivalent, "88.09 USD/barrel")
        self.assertTrue(crude_card.usd_rate_cached)
        self.assertEqual(crude_card.usd_rate_retrieved_at, self.retrieved)

    def test_formatting_conversion_does_not_call_provider(self):
        """Format already-loaded quotes without triggering additional market requests."""
        with (
            patch("marketpulse.dashboard.market_data.CrudeOilFuturesQuoteService.get_quote") as crude_fetch,
            patch("marketpulse.dashboard.market_data.UsdInrFuturesQuoteService.get_quote") as fx_fetch,
        ):
            cards = build_commodity_cards(self.snapshot)
        crude_fetch.assert_not_called()
        fx_fetch.assert_not_called()
        self.assertEqual(next(card for card in cards if card.section == "crude_oil").usd_equivalent,
                         "88.09 USD/barrel")

    def test_independent_failures_keep_other_quotes_and_identify_cache(self):
        """Retain successful snapshots independently and label failed refreshes as cached."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        index_sentinel = object()
        services.indices.get_quotes.return_value = [index_sentinel]
        services.gold.get_quote.return_value = self.gold
        services.silver.get_quote.side_effect = RuntimeError("sensitive SDK text")
        services.usdinr.get_quote.return_value = self.usdinr
        services.crude_oil.get_quote.return_value = self.crude
        services.sectors.get_sector_performance.return_value = self._sector_quotes()
        services.market_movers.get_market_movers.return_value = self._market_movers_result()
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()
        previous = DashboardMarketData(silver_quote=self.silver)

        outcome = refresh_dashboard_data(services, previous=previous)
        cards = build_commodity_cards(outcome.market_data, outcome.errors)
        silver_card = next(card for card in cards if card.section == "silver")

        self.assertEqual(outcome.market_data.gold_quote, self.gold)
        self.assertEqual(outcome.market_data.silver_quote, self.silver)
        self.assertTrue(silver_card.cached)
        self.assertEqual(silver_card.retrieved_at, self.retrieved)
        self.assertNotIn("sensitive SDK text", " ".join(outcome.errors))
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.assert_called_once_with()
        services.market_movers.iter_universe_quote_batches.assert_called_once_with()
        services.market_movers.get_market_movers.assert_called_once_with(())
        services.year_high.get_stocks_at_52_week_high.assert_called_once_with(())

    def test_all_four_initial_failures_are_independent_and_sanitized(self):
        """Report missing instrument masters without hiding other datasets or leaking details."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = FileNotFoundError("private path")
        services.sectors.get_sector_performance.side_effect = FileNotFoundError("private path")
        services.market_movers.get_market_movers.side_effect = MarketMoversDataError("private path")
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()

        outcome = refresh_dashboard_data(services)
        cards = build_commodity_cards(outcome.market_data, outcome.errors)

        self.assertEqual(len(outcome.errors), 6)
        self.assertTrue(all(card.price == "Unavailable" for card in cards))
        self.assertTrue(all("instrument master" in message for message in outcome.errors[:4]))
        self.assertIn("NSE sector heatmap", outcome.errors[4])
        self.assertIn("constituent data or quote coverage is invalid", outcome.errors[5])
        self.assertNotIn("private path", " ".join(outcome.errors))

    def test_timestamp_formatter_displays_ist_and_unavailable(self):
        """Convert UTC retrieval timestamps to IST without calling them last-traded times."""
        self.assertEqual(format_retrieved_at(self.retrieved), "2026-11-15 16:00:00 IST")
        self.assertEqual(format_retrieved_at(None), "Unavailable")

    def test_service_factory_shares_provider_store_and_instrument_service(self):
        """Construct all quote services over one provider and one local master service."""
        with (
            patch("marketpulse.dashboard.market_data.KotakNeoProvider") as provider_type,
            patch("marketpulse.dashboard.market_data.InstrumentMasterStore") as store_type,
            patch("marketpulse.dashboard.market_data.InstrumentMasterParser") as parser_type,
            patch("marketpulse.dashboard.market_data.InstrumentMasterService") as master_type,
            patch("marketpulse.dashboard.market_data.IndexQuoteService") as index_type,
            patch("marketpulse.data.quote_service.NSEEquityQuoteService") as equity_type,
            patch("marketpulse.dashboard.market_data.GoldFuturesQuoteService") as gold_type,
            patch("marketpulse.dashboard.market_data.SilverFuturesQuoteService") as silver_type,
            patch("marketpulse.dashboard.market_data.UsdInrFuturesQuoteService") as fx_type,
            patch("marketpulse.dashboard.market_data.CrudeOilFuturesQuoteService") as crude_type,
            patch("marketpulse.dashboard.market_data.SectorPerformanceService") as sectors_type,
            patch("marketpulse.dashboard.market_data.MarketMoversService") as movers_type,
            patch("marketpulse.dashboard.market_data.Nifty10052WeekHighService") as year_high_type,
        ):
            services = create_dashboard_services(Settings(project_root=Path("/tmp/demo")))

        provider_type.assert_called_once()
        store_type.assert_called_once()
        master_type.assert_called_once_with(store_type.return_value, parser_type.return_value)
        index_type.assert_called_once_with(provider_type.return_value)
        equity_type.assert_not_called()
        for service_type in (gold_type, silver_type, fx_type, crude_type):
            service_type.assert_called_once_with(
                store_type.return_value, master_type.return_value, provider_type.return_value
            )
        self.assertIs(services.gold, gold_type.return_value)
        sectors_type.assert_called_once_with(provider_type.return_value)
        self.assertIs(services.sectors, sectors_type.return_value)
        movers_type.assert_called_once_with(
            master_type.return_value, provider_type.return_value, universe=NIFTY_100_UNIVERSE
        )
        self.assertIs(services.market_movers, movers_type.return_value)
        self.assertFalse(hasattr(services, "equities"))
        year_high_type.assert_called_once_with(master_type.return_value, provider_type.return_value)
        self.assertIs(services.year_high, year_high_type.return_value)

    def test_heatmap_formatting_preserves_order_signs_values_and_colors(self):
        """Format all 12 ordered sectors with explicit signed percentages and directions."""
        tiles = build_sector_heatmap_tiles(self._sector_quotes())

        self.assertEqual(len(tiles), 12)
        self.assertEqual(
            [tile.sector_name for tile in tiles],
            [name for name, _ in SectorPerformanceService.SECTORS],
        )
        self.assertEqual([tile.index_value for tile in tiles[:3]], ["25000", "25001", "25002"])
        self.assertEqual([tile.percent_change for tile in tiles[:3]], ["+1.25%", "-0.50%", "0%"])
        self.assertEqual([tile.direction for tile in tiles[:3]], ["positive", "negative", "neutral"])

    def test_incomplete_or_malformed_heatmap_snapshot_is_not_renderable(self):
        """Do not expose partial or invalid sector data as a complete heatmap."""
        complete = self._sector_quotes()
        self.assertEqual(build_sector_heatmap_tiles(complete[:-1]), ())
        malformed = (replace(complete[0], percent_change=Decimal("NaN")), *complete[1:])
        self.assertEqual(build_sector_heatmap_tiles(malformed), ())

    def test_sector_refresh_failure_retains_complete_snapshot_as_cached(self):
        """Keep prior full coverage and its source timestamp when only sector refresh fails."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        index_sentinel = object()
        services.indices.get_quotes.return_value = [index_sentinel]
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.side_effect = RuntimeError("raw response")
        services.market_movers.get_market_movers.return_value = self._market_movers_result()
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()
        previous = DashboardMarketData(sector_performance=self._sector_quotes())

        outcome = refresh_dashboard_data(services, previous=previous)
        tiles = build_sector_heatmap_tiles(outcome.market_data.sector_performance, outcome.errors)

        self.assertEqual(outcome.market_data.index_quotes, (index_sentinel,))
        self.assertEqual(len(tiles), 12)
        self.assertTrue(all(tile.cached for tile in tiles))
        self.assertTrue(all(tile.retrieved_at == self.retrieved for tile in tiles))
        self.assertNotIn("raw response", " ".join(outcome.errors))
        services.sectors.get_sector_performance.assert_called_once_with()

    def test_incomplete_sector_service_result_retains_previous_snapshot(self):
        """Reject incomplete service returns and preserve the prior full sector snapshot."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.return_value = self._sector_quotes()[:-1]
        services.market_movers.get_market_movers.return_value = self._market_movers_result()
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()
        previous = DashboardMarketData(sector_performance=self._sector_quotes())

        outcome = refresh_dashboard_data(services, previous=previous)

        self.assertEqual(outcome.market_data.sector_performance, previous.sector_performance)
        self.assertTrue(any(message.startswith("NSE sector heatmap") for message in outcome.errors))

    def test_market_movers_tables_use_ranked_service_output_and_cached_status(self):
        """Show ten service-ranked rows per side with signed percentages and quote prices."""
        result = self._market_movers_result()
        tables = build_market_movers_tables(
            result, ("NIFTY 100 market movers: quote refresh failed.",)
        )

        self.assertIsNotNone(tables)
        assert tables is not None
        self.assertEqual(len(tables.gainers), 10)
        self.assertEqual(len(tables.losers), 10)
        self.assertEqual(tables.gainers[0].symbol, result.gainers[0].symbol)
        self.assertEqual(tables.gainers[0].rank, 1)
        self.assertEqual(tables.gainers[0].percent_change, "+49%")
        self.assertEqual(tables.gainers[0].last_traded_price, "199")
        self.assertEqual(tables.losers[0].symbol, result.losers[0].symbol)
        self.assertEqual(tables.losers[0].percent_change, "-50%")
        self.assertEqual(tables.losers[0].last_traded_price, "100")
        self.assertEqual(tables.gainers[0].direction, "positive")
        self.assertEqual(tables.losers[0].direction, "negative")
        self.assertTrue(tables.cached)
        self.assertEqual(tables.retrieved_at, self.retrieved)

    def test_zero_changes_keep_deterministic_symbol_order_and_neutral_format(self):
        """Preserve alphabetical service ties and display exact zero without a sign."""
        result = self._market_movers_result()
        quotes = tuple(replace(quote, percent_change=Decimal("0")) for quote in result.quotes)
        changes = tuple(StockChange(quote.symbol, Decimal("0")) for quote in sorted(quotes, key=lambda q: q.symbol)[:10])
        flat_result = replace(result, quotes=quotes, gainers=changes, losers=changes)

        tables = build_market_movers_tables(flat_result)

        self.assertIsNotNone(tables)
        assert tables is not None
        self.assertEqual([row.symbol for row in tables.gainers], sorted(row.symbol for row in tables.gainers))
        self.assertEqual(tables.gainers[0].percent_change, "0%")
        self.assertEqual(tables.gainers[0].direction, "neutral")

    def test_movers_formatter_rejects_partial_wrong_universe_or_malformed_result(self):
        """Hide incomplete coverage, NIFTY 50 results, and malformed service output."""
        result = self._market_movers_result()
        self.assertIsNone(build_market_movers_tables(None))
        self.assertIsNone(build_market_movers_tables(replace(result, coverage_complete=False)))
        self.assertIsNone(build_market_movers_tables(replace(result, universe_name="NIFTY 50")))
        self.assertIsNone(build_market_movers_tables(replace(result, gainers=())))
        self.assertIsNone(
            build_market_movers_tables(replace(result, quotes=result.quotes[:-1]))
        )

    def test_market_movers_failure_is_independent_and_retains_cached_result(self):
        """Keep other datasets refreshing while preserving old rankings after failure."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(),
            usdinr=Mock(), crude_oil=Mock(), sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.return_value = self._sector_quotes()
        services.market_movers.get_market_movers.side_effect = RuntimeError("private SDK payload")
        services.market_movers.iter_universe_quote_batches.return_value = ()
        services.year_high.get_stocks_at_52_week_high.return_value = self._year_high_result()
        previous = DashboardMarketData(market_movers=self._market_movers_result())

        outcome = refresh_dashboard_data(services, previous=previous)
        tables = build_market_movers_tables(outcome.market_data.market_movers, outcome.errors)

        self.assertEqual(outcome.market_data.market_movers, previous.market_movers)
        self.assertIsNotNone(tables)
        assert tables is not None
        self.assertTrue(tables.cached)
        self.assertEqual(tables.retrieved_at, previous.market_movers.retrieved_at)
        self.assertEqual(len(outcome.market_data.sector_performance), 12)
        self.assertNotIn("private SDK payload", " ".join(outcome.errors))

    def test_market_movers_formatting_does_not_make_provider_requests(self):
        """Format cached rankings without invoking market-movers or provider methods."""
        service = Mock(spec=MarketMoversService)
        tables = build_market_movers_tables(self.snapshot.market_movers)

        service.get_market_movers.assert_not_called()
        self.assertEqual(len(tables.gainers), 10)

    def test_year_high_table_preserves_qualified_order_and_formats_currency(self):
        """Render qualifying result rows in service order with rupee prices."""
        result = self._year_high_result(("AAA", "BBB", "CCC"))
        table = build_year_high_table(result)

        self.assertIsNotNone(table)
        assert table is not None
        self.assertEqual([row.rank for row in table.rows], [1, 2, 3])
        self.assertEqual([row.symbol for row in table.rows], ["AAA", "BBB", "CCC"])
        self.assertEqual(table.rows[0].last_traded_price, "125.50")
        self.assertEqual(table.rows[0].year_high, "125.50")
        self.assertEqual(table.retrieved_at, self.retrieved)
        self.assertFalse(table.cached)

    def test_empty_complete_scan_is_successful_but_incomplete_scan_is_unavailable(self):
        """Distinguish zero qualifying stocks from missing universe coverage."""
        empty_result = self._year_high_result(())
        empty_table = build_year_high_table(empty_result)
        self.assertIsNotNone(empty_table)
        assert empty_table is not None
        self.assertEqual(empty_table.rows, ())
        self.assertIsNone(
            build_year_high_table(replace(empty_result, coverage_complete=False))
        )
        self.assertIsNone(
            build_year_high_table(replace(empty_result, validated_quote_count=99))
        )

    def test_year_high_result_rejects_near_highs_duplicates_and_more_than_ten(self):
        """Reject false qualifiers, duplicate symbols, or rows beyond the service cap."""
        result = self._year_high_result()
        below_high = replace(result.stocks[0], last_traded_price=Decimal("125.49"))
        self.assertIsNone(build_year_high_table(replace(result, stocks=(below_high,))))
        duplicated = replace(result, stocks=(result.stocks[0], result.stocks[0]))
        self.assertIsNone(build_year_high_table(duplicated))
        too_many = replace(result, stocks=tuple(
            StockAt52WeekHigh(f"S{index:02}", Decimal("125"), Decimal("125"))
            for index in range(Nifty10052WeekHighService.MAX_RESULTS + 1)
        ))
        self.assertIsNone(build_year_high_table(too_many))

    def test_year_high_refresh_failure_keeps_prior_result_and_independent_sections(self):
        """Retain cached high results while other dashboard datasets refresh normally."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(), usdinr=Mock(), crude_oil=Mock(),
            sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.return_value = self._sector_quotes()
        quote_batch = [(Mock(), {"ltp": "125", "year_high": "125"})]
        services.market_movers.iter_universe_quote_batches.return_value = (quote_batch,)
        services.market_movers.get_market_movers.side_effect = RuntimeError("rank parse")
        services.year_high.get_stocks_at_52_week_high.side_effect = RuntimeError("provider secret")
        previous_result = self._year_high_result()
        previous = DashboardMarketData(stocks_at_52_week_high=previous_result)

        outcome = refresh_dashboard_data(services, previous=previous)
        table = build_year_high_table(outcome.market_data.stocks_at_52_week_high, outcome.errors)

        self.assertIsNotNone(table)
        assert table is not None
        self.assertTrue(table.cached)
        self.assertEqual(table.retrieved_at, self.retrieved)
        self.assertEqual(len(outcome.market_data.sector_performance), 12)
        self.assertNotIn("provider secret", " ".join(outcome.errors))
        services.market_movers.iter_universe_quote_batches.assert_called_once_with()
        services.market_movers.get_market_movers.assert_called_once_with((quote_batch,))
        services.year_high.get_stocks_at_52_week_high.assert_called_once_with((quote_batch,))

    def test_missing_constituent_file_is_sanitized_and_prior_high_result_is_cached(self):
        """Treat a missing NIFTY 100 CSV as unavailable without losing cached results."""
        services = DashboardServices(
            indices=Mock(), gold=Mock(), silver=Mock(), usdinr=Mock(), crude_oil=Mock(),
            sectors=Mock(), market_movers=Mock(), year_high=Mock(),
        )
        services.indices.get_quotes.return_value = []
        for service in (services.gold, services.silver, services.usdinr, services.crude_oil):
            service.get_quote.side_effect = RuntimeError()
        services.sectors.get_sector_performance.return_value = self._sector_quotes()
        services.market_movers.iter_universe_quote_batches.side_effect = MarketMoversDataError(
            "private missing CSV path"
        )
        previous_result = self._year_high_result()

        outcome = refresh_dashboard_data(
            services,
            previous=DashboardMarketData(stocks_at_52_week_high=previous_result),
        )
        table = build_year_high_table(
            outcome.market_data.stocks_at_52_week_high, outcome.errors
        )

        self.assertIsNotNone(table)
        assert table is not None
        self.assertTrue(table.cached)
        self.assertEqual(table.retrieved_at, self.retrieved)
        self.assertNotIn("private missing CSV path", " ".join(outcome.errors))
        self.assertTrue(any("52-week-high scan unavailable" in message for message in outcome.errors))
        services.year_high.get_stocks_at_52_week_high.assert_not_called()

    def test_safe_error_message_maps_master_and_configuration_failures(self):
        """Return fixed section-specific messages for missing masters and setup failures."""
        from marketpulse.data.commodity_futures_quote_service import CommodityFuturesMasterError
        from marketpulse.data.kotak_neo import KotakConfigurationError

        self.assertIn(
            "local instrument master",
            safe_error_message(CommodityFuturesMasterError("secret path"), "gold"),
        )
        self.assertIn(
            "local environment",
            safe_error_message(KotakConfigurationError("secret key"), "usdinr"),
        )
        self.assertIn(
            "constituent data or quote coverage is invalid",
            safe_error_message(MarketMoversDataError("private CSV path"), "market_movers"),
        )
        self.assertIn(
            "52-week-high scan unavailable",
            safe_error_message(MarketMoversDataError("private CSV path"), "year_high"),
        )


if __name__ == "__main__":
    unittest.main()
