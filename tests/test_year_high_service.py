import csv
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from marketpulse.data.instrument import Instrument
from marketpulse.data.market_movers_service import (
    MarketMoversDataError,
    MarketMoversProviderError,
    NIFTY_100_UNIVERSE,
)
from marketpulse.data.year_high_service import Nifty10052WeekHighService


class Nifty10052WeekHighServiceTests(unittest.TestCase):
    """Verify NIFTY 100 quote coverage and strict 52-week-high qualification."""

    def setUp(self):
        """Build provider and instrument fixtures from the local verified universe."""
        with NIFTY_100_UNIVERSE.constituent_csv.open(encoding="utf-8-sig", newline="") as handle:
            self.constituents = list(csv.DictReader(handle, strict=True))
        self.instruments = [
            Instrument(
                instrument_token=str(5000 + index),
                exchange_segment="nse_cm",
                symbol=row["Symbol"],
                trading_symbol=f"{row['Symbol']}-EQ",
                series=row["Series"],
                isin=row["ISIN Code"],
            )
            for index, row in enumerate(self.constituents)
        ]
        self.instrument_service = Mock()
        self.instrument_service.load_instruments.return_value = self.instruments
        self.provider = Mock()
        self.retrieved_at = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
        self.service = Nifty10052WeekHighService(
            self.instrument_service,
            self.provider,
            clock=lambda: self.retrieved_at,
        )

    def _response_for(self, instruments, qualify_indices=(), bad_field=None, reverse=True):
        """Build quote rows with controlled high qualification or field failures."""
        rows = []
        for instrument in instruments:
            index = int(instrument.instrument_token) - 5000
            year_high = "100" if index in qualify_indices else "101"
            row = {
                "exchange_token": instrument.instrument_token,
                "display_symbol": instrument.trading_symbol,
                "exchange": "nse_cm",
                "ltp": "100",
                "year_high": year_high,
                "ohlc": {"high": "102"},
            }
            if bad_field is not None and index == 0:
                if bad_field == "missing":
                    row.pop("year_high")
                else:
                    row["year_high"] = bad_field
            rows.append(row)
        return list(reversed(rows)) if reverse else rows

    def _configure_batches(self, qualify_indices=(), bad_field=None):
        """Configure four mock responses for the NIFTY 100 quote batches."""
        self.provider.get_quotes.side_effect = [
            self._response_for(
                self.instruments[start : start + 25],
                qualify_indices=qualify_indices,
                bad_field=bad_field,
            )
            for start in range(0, 100, 25)
        ]

    def test_no_qualifiers_returns_empty_list_with_complete_coverage(self):
        """Return no near-high substitutes when no stock meets the rule."""
        self._configure_batches()

        result = self.service.get_stocks_at_52_week_high()

        self.assertEqual(result.stocks, ())
        self.assertEqual(result.validated_quote_count, 100)
        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.universe_name, "NIFTY 100")

    def test_one_to_nine_qualifiers_include_equality_and_exclude_near_highs(self):
        """Include equality at the high and exclude stocks just below it."""
        self._configure_batches(qualify_indices={1, 3, 5})

        result = self.service.get_stocks_at_52_week_high()

        expected_symbols = sorted(self.instruments[index].symbol for index in (1, 3, 5))
        self.assertEqual([stock.symbol for stock in result.stocks], expected_symbols)
        self.assertTrue(all(stock.last_traded_price == stock.year_high for stock in result.stocks))
        self.assertEqual(len(result.stocks), 3)
        self.assertEqual(result.stocks[0].day_high, Decimal("102"))

    def test_more_than_ten_qualifiers_are_capped_deterministically(self):
        """Return the first ten qualifying symbols in stable alphabetical order."""
        qualifying = set(range(15))
        self._configure_batches(qualify_indices=qualifying)

        result = self.service.get_stocks_at_52_week_high()

        expected = sorted(self.instruments[index].symbol for index in qualifying)[:10]
        self.assertEqual([stock.symbol for stock in result.stocks], expected)
        self.assertEqual(len(result.stocks), 10)

    def test_invalid_missing_zero_and_nonfinite_year_high_fail_closed(self):
        """Reject every missing, zero, malformed, or nonfinite required high."""
        for value in ("missing", "", "0", "bad", "NaN", "Infinity"):
            with self.subTest(year_high=value):
                self._configure_batches(bad_field=value)
                with self.assertRaises(MarketMoversDataError):
                    self.service.get_stocks_at_52_week_high()

    def test_ltp_must_be_a_finite_positive_decimal(self):
        """Reject missing, zero, or nonfinite last traded prices."""
        for value in (None, "0", "Infinity"):
            with self.subTest(ltp=value):
                responses = [
                    self._response_for(self.instruments[start : start + 25])
                    for start in range(0, 100, 25)
                ]
                responses[0][0]["ltp"] = value
                self.provider.get_quotes.side_effect = responses

                with self.assertRaises(MarketMoversDataError):
                    self.service.get_stocks_at_52_week_high()

    def test_duplicate_missing_unexpected_and_malformed_quote_records_fail(self):
        """Reject quote batches that do not map one-to-one to requested tokens."""
        mutations = ("duplicate", "missing", "unexpected", "malformed")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                first = self._response_for(self.instruments[:25])
                if mutation == "duplicate":
                    first[0]["exchange_token"] = first[1]["exchange_token"]
                elif mutation == "missing":
                    first.pop()
                elif mutation == "unexpected":
                    first[0]["exchange_token"] = "not-requested"
                else:
                    first[0] = "invalid-record"
                self.provider.get_quotes.side_effect = [first]
                with self.assertRaises(MarketMoversDataError):
                    self.service.get_stocks_at_52_week_high()

    def test_identity_mismatch_fails_closed(self):
        """Reject quotes whose display symbol or exchange differs from the request."""
        for field, value in (("display_symbol", "WRONG-EQ"), ("exchange", "bse_cm")):
            with self.subTest(field=field):
                first = self._response_for(self.instruments[:25])
                first[0][field] = value
                self.provider.get_quotes.side_effect = [first]
                with self.assertRaises(MarketMoversDataError):
                    self.service.get_stocks_at_52_week_high()

    def test_four_batches_and_one_instrument_master_load(self):
        """Use four 25-token requests and load the instrument master only once."""
        self._configure_batches()

        result = self.service.get_stocks_at_52_week_high()

        self.assertEqual(self.provider.get_quotes.call_count, 4)
        self.instrument_service.load_instruments.assert_called_once_with("nse_cm")
        self.assertTrue(all(
            len(call.kwargs["instrument_tokens"]) == 25
            and call.kwargs["quote_type"] == "all"
            for call in self.provider.get_quotes.call_args_list
        ))
        self.assertEqual(result.retrieved_at, self.retrieved_at)

    def test_prevalidated_quote_batches_are_reused_without_provider_calls(self):
        """Consume shared validated batches without making a second quote request."""
        batches = []
        for start in range(0, 100, 25):
            instruments = self.instruments[start : start + 25]
            rows_by_token = {
                row["exchange_token"]: row
                for row in self._response_for(instruments, qualify_indices={start})
            }
            batches.append([
                (instrument, rows_by_token[instrument.instrument_token])
                for instrument in instruments
            ])

        result = self.service.get_stocks_at_52_week_high(tuple(batches))

        self.provider.get_quotes.assert_not_called()
        self.instrument_service.load_instruments.assert_not_called()
        self.assertEqual(result.validated_quote_count, 100)
        self.assertTrue(result.coverage_complete)
        self.assertEqual(len(result.stocks), 4)

    def test_provider_failure_is_sanitized(self):
        """Hide unexpected provider details when a quote batch fails."""
        self.provider.get_quotes.side_effect = RuntimeError("sensitive response text")

        with self.assertRaises(MarketMoversProviderError) as raised:
            self.service.get_stocks_at_52_week_high()

        self.assertNotIn("sensitive", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
