import csv
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from marketpulse.data.instrument import Instrument
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversProviderError,
    MarketMoversService,
    MarketUniverse,
    Nifty50MarketMoversService,
)


class Nifty50MarketMoversServiceTests(unittest.TestCase):
    """Verify complete-universe resolution, quote validation, and ranking."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "constituents.csv"
        self.symbols = [f"S{index:02}" for index in range(50)]
        self.instruments = [
            Instrument(
                instrument_token=str(1000 + index),
                exchange_segment="nse_cm",
                symbol=symbol,
                trading_symbol=f"{symbol}-EQ",
                series="EQ",
                isin=f"ISIN{index}",
            )
            for index, symbol in enumerate(self.symbols)
        ]
        self._write_csv()
        self.instrument_service = Mock()
        self.instrument_service.load_instruments.return_value = self.instruments
        self.provider = Mock()
        self.retrieved_at = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
        self.service = Nifty50MarketMoversService(
            self.instrument_service,
            self.provider,
            self.csv_path,
            clock=lambda: self.retrieved_at,
        )

    def _write_csv(self, symbols=None, series=None, extra_raw=""):
        """Write a local fixture CSV for the selected constituent set."""
        symbols = self.symbols if symbols is None else symbols
        series = series or ["EQ"] * len(symbols)
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Company Name", "Industry", "Symbol", "Series", "ISIN Code"])
            for index, symbol in enumerate(symbols):
                writer.writerow([
                    f"Company {index}", "Industry", symbol, series[index], f"ISIN{index}"
                ])
            if extra_raw:
                handle.write(extra_raw)

    def _response_for(self, instruments, reverse=True, pct_values=None):
        rows = []
        for instrument in instruments:
            index = int(instrument.instrument_token) - 1000
            percent = pct_values[index] if pct_values is not None else index - 25
            rows.append({
                "exchange_token": instrument.instrument_token,
                "display_symbol": instrument.trading_symbol,
                "exchange": "nse_cm",
                "ltp": "101.250",
                "change": "1.250",
                "per_change": str(percent),
                "last_volume": "ignored-unverified-volume",
            })
        return list(reversed(rows)) if reverse else rows

    def _configure_successful_batches(self, pct_values=None):
        self.provider.get_quotes.side_effect = [
            self._response_for(self.instruments[:25], pct_values=pct_values),
            self._response_for(self.instruments[25:], pct_values=pct_values),
        ]

    def test_two_batches_validate_and_rank_in_constituent_order(self):
        self._configure_successful_batches()

        result = self.service.get_market_movers()

        self.assertEqual(self.provider.get_quotes.call_count, 2)
        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        for call, instruments in zip(
            self.provider.get_quotes.call_args_list,
            (self.instruments[:25], self.instruments[25:]),
            strict=True,
        ):
            self.assertEqual(call.kwargs["quote_type"], "all")
            self.assertEqual(
                call.kwargs["instrument_tokens"],
                [
                    {"instrument_token": item.instrument_token, "exchange_segment": "nse_cm"}
                    for item in instruments
                ],
            )

        self.assertEqual([quote.symbol for quote in result.quotes], self.symbols)
        self.assertEqual(result.quotes[0].percent_change, Decimal("-25"))
        self.assertEqual(result.gainers[0].symbol, "S49")
        self.assertEqual(result.gainers[0].percent_change, Decimal("24"))
        self.assertEqual(result.losers[0].symbol, "S00")
        self.assertEqual(result.losers[0].percent_change, Decimal("-25"))
        self.assertEqual(result.universe_name, "NIFTY 50")
        self.assertEqual(result.expected_constituent_count, 50)
        self.assertEqual(result.validated_constituent_count, 50)
        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.retrieved_at, self.retrieved_at)

    def test_positive_negative_and_zero_values_are_preserved(self):
        values = [Decimal("0")] * 50
        values[0] = Decimal("-1.25")
        values[49] = Decimal("2.50")
        self._configure_successful_batches(values)

        result = self.service.get_market_movers()

        self.assertEqual(result.quotes[0].percent_change, Decimal("-1.25"))
        self.assertEqual(result.quotes[1].percent_change, Decimal("0"))
        self.assertEqual(result.quotes[49].percent_change, Decimal("2.50"))
        self.assertEqual(result.gainers[0].symbol, "S49")
        self.assertEqual(result.losers[0].symbol, "S00")

    def test_nifty100_uses_four_batches_and_one_master_load(self):
        """Verify NIFTY 100 uses four complete batches and shared ranking logic."""
        symbols = [f"N{index:03}" for index in range(100)]
        instruments = [
            Instrument(
                instrument_token=str(1000 + index),
                exchange_segment="nse_cm",
                symbol=symbol,
                trading_symbol=f"{symbol}-EQ",
                series="EQ",
                isin=f"ISIN{index}",
            )
            for index, symbol in enumerate(symbols)
        ]
        self._write_csv(symbols)
        self.instrument_service.load_instruments.return_value = instruments
        self.provider.get_quotes.side_effect = [
            self._response_for(instruments[start : start + 25])
            for start in range(0, 100, 25)
        ]
        service = MarketMoversService(
            self.instrument_service,
            self.provider,
            MarketUniverse("NIFTY 100", self.csv_path, 100),
            clock=lambda: self.retrieved_at,
        )

        quote_batches = tuple(service.iter_universe_quote_batches())
        result = service.get_market_movers(quote_batches)

        self.assertEqual(self.provider.get_quotes.call_count, 4)
        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        self.assertTrue(all(
            len(call.kwargs["instrument_tokens"]) == 25
            and call.kwargs["quote_type"] == "all"
            for call in self.provider.get_quotes.call_args_list
        ))
        self.assertEqual([quote.symbol for quote in result.quotes], symbols)
        self.assertEqual(result.universe_name, "NIFTY 100")
        self.assertEqual(result.expected_constituent_count, 100)
        self.assertEqual(result.validated_constituent_count, 100)
        self.assertTrue(result.coverage_complete)
        self.assertEqual([row.symbol for row in result.gainers], symbols[-1:-11:-1])
        self.assertEqual([row.symbol for row in result.losers], symbols[:10])

    def test_nifty100_incomplete_fourth_batch_returns_no_partial_result(self):
        """Reject a final incomplete batch instead of returning partial rankings."""
        symbols = [f"N{index:03}" for index in range(100)]
        instruments = [
            Instrument(
                instrument_token=str(1000 + index),
                exchange_segment="nse_cm",
                symbol=symbol,
                trading_symbol=f"{symbol}-EQ",
                series="EQ",
                isin=f"ISIN{index}",
            )
            for index, symbol in enumerate(symbols)
        ]
        self._write_csv(symbols)
        self.instrument_service.load_instruments.return_value = instruments
        batches = [self._response_for(instruments[start : start + 25]) for start in range(0, 100, 25)]
        batches[-1] = batches[-1][:-1]
        self.provider.get_quotes.side_effect = batches
        service = MarketMoversService(
            self.instrument_service,
            self.provider,
            MarketUniverse("NIFTY 100", self.csv_path, 100),
        )

        with self.assertRaisesRegex(MarketMoversDataError, "exact record count"):
            service.get_market_movers()

        self.assertEqual(self.provider.get_quotes.call_count, 4)

    def test_trading_symbol_series_and_isin_mismatches_fail_before_quotes(self):
        """Reject master records whose trading identity differs from the CSV."""
        mismatch_cases = (
            ("trading_symbol", "OTHER-EQ", "trading symbol"),
            ("series", "BE", "no eligible"),
            ("isin", "WRONG-ISIN", "ISIN"),
        )
        for field, value, message in mismatch_cases:
            with self.subTest(field=field):
                changed = list(self.instruments)
                changed[0] = Instrument(
                    instrument_token=changed[0].instrument_token,
                    exchange_segment=changed[0].exchange_segment,
                    symbol=changed[0].symbol,
                    trading_symbol=(value if field == "trading_symbol" else changed[0].trading_symbol),
                    series=(value if field == "series" else changed[0].series),
                    isin=(value if field == "isin" else changed[0].isin),
                )
                self.instrument_service.load_instruments.return_value = changed

                with self.assertRaisesRegex(MarketMoversDataError, message):
                    self.service.get_market_movers()
                self.provider.get_quotes.assert_not_called()
                self.provider.reset_mock()

    def test_missing_isin_in_constituent_csv_fails_validation(self):
        """Reject constituents that cannot be cross-checked against master ISINs."""
        self._write_csv()
        with self.csv_path.open("r", encoding="utf-8") as handle:
            content = handle.read().replace("ISIN0", "", 1)
        self.csv_path.write_text(content, encoding="utf-8")

        with self.assertRaises(MarketMoversDataError):
            self.service.get_market_movers()

        self.provider.get_quotes.assert_not_called()

    def test_missing_malformed_and_nonfinite_percentage_changes_fail_closed(self):
        for bad_value, remove_field in (("bad", False), ("NaN", False), (None, True)):
            with self.subTest(bad_value=bad_value, remove_field=remove_field):
                self.provider.reset_mock()
                first = self._response_for(self.instruments[:25])
                if remove_field:
                    first[0].pop("per_change")
                else:
                    first[0]["per_change"] = bad_value
                self.provider.get_quotes.side_effect = [
                    first,
                    self._response_for(self.instruments[25:]),
                ]
                with self.assertRaises(MarketMoversDataError):
                    self.service.get_market_movers()
                self.assertEqual(self.provider.get_quotes.call_count, 1)

    def test_duplicate_quote_token_is_rejected(self):
        first = self._response_for(self.instruments[:25])
        first[0]["exchange_token"] = first[1]["exchange_token"]
        self.provider.get_quotes.side_effect = [first]

        with self.assertRaisesRegex(MarketMoversDataError, "duplicate"):
            self.service.get_market_movers()

    def test_short_batch_is_rejected_as_missing_coverage(self):
        first = self._response_for(self.instruments[:25])[:-1]
        self.provider.get_quotes.side_effect = [first]

        with self.assertRaisesRegex(MarketMoversDataError, "exact record count"):
            self.service.get_market_movers()

    def test_unexpected_quote_token_is_rejected(self):
        first = self._response_for(self.instruments[:25])
        first[0]["exchange_token"] = "999999"
        self.provider.get_quotes.side_effect = [first]

        with self.assertRaisesRegex(MarketMoversDataError, "unexpected"):
            self.service.get_market_movers()

    def test_non_dictionary_quote_record_is_rejected(self):
        """Reject malformed provider rows before interpreting quote values."""
        first = self._response_for(self.instruments[:25])
        first[0] = "malformed"
        self.provider.get_quotes.side_effect = [first]

        with self.assertRaisesRegex(MarketMoversDataError, "invalid record"):
            self.service.get_market_movers()

    def test_mismatched_exchange_or_symbol_identity_is_rejected(self):
        for field, value in (("exchange", "bse_cm"), ("display_symbol", "WRONG-EQ")):
            with self.subTest(field=field):
                first = self._response_for(self.instruments[:25])
                first[0][field] = value
                self.provider.get_quotes.side_effect = [first]
                with self.assertRaisesRegex(MarketMoversDataError, "identity"):
                    self.service.get_market_movers()

    def test_invalid_constituent_count_duplicate_series_and_csv_rows_fail(self):
        invalid_sets = [
            (self.symbols[:-1], None),
            ([*self.symbols[:-1], self.symbols[0]], None),
            (self.symbols, ["EQ"] * 49 + ["BE"]),
        ]
        for symbols, series in invalid_sets:
            with self.subTest(symbol_count=len(symbols), series=series[-1] if series else None):
                self._write_csv(symbols, series)
                with self.assertRaises(MarketMoversDataError):
                    self.service.get_market_movers()
                self.provider.get_quotes.assert_not_called()

        self._write_csv(extra_raw="malformed,short,row\n")
        with self.assertRaises(MarketMoversDataError):
            self.service.get_market_movers()
        self.provider.get_quotes.assert_not_called()

    def test_ambiguous_instrument_resolution_fails_before_quotes(self):
        self.instrument_service.load_instruments.return_value = [
            *self.instruments,
            self.instruments[0],
        ]

        with self.assertRaises(MarketMoversDataError):
            self.service.get_market_movers()

        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        self.provider.get_quotes.assert_not_called()

    def test_duplicate_instrument_tokens_fail_before_quote_requests(self):
        """Reject repeated Kotak tokens even when the symbols are distinct."""
        changed = list(self.instruments)
        changed[1] = Instrument(
            instrument_token=changed[0].instrument_token,
            exchange_segment=changed[1].exchange_segment,
            symbol=changed[1].symbol,
            trading_symbol=changed[1].trading_symbol,
            series=changed[1].series,
            isin=changed[1].isin,
        )
        self.instrument_service.load_instruments.return_value = changed

        with self.assertRaisesRegex(MarketMoversDataError, "unique tokens"):
            self.service.get_market_movers()

        self.provider.get_quotes.assert_not_called()

    def test_second_batch_failure_returns_no_partial_ranking_and_hides_details(self):
        self.provider.get_quotes.side_effect = [
            self._response_for(self.instruments[:25]),
            RuntimeError("sensitive provider response"),
        ]

        with self.assertRaises(MarketMoversProviderError) as raised:
            self.service.get_market_movers()

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(self.provider.get_quotes.call_count, 2)


if __name__ == "__main__":
    unittest.main()
