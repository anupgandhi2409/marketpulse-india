import unittest

from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_quality import InstrumentMasterQualityReporter


def make_instrument(**overrides):
    values = {
        "instrument_token": "101",
        "exchange_segment": "nse_cm",
        "symbol": "ALPHA",
        "trading_symbol": "ALPHA-EQ",
        "series": "EQ",
        "isin": "ISIN00000001",
        "description": "Alpha Limited",
        "market_segment": "CASH",
        "lot_size": "1",
    }
    values.update(overrides)
    return Instrument(**values)


class InstrumentMasterQualityReporterTests(unittest.TestCase):
    def setUp(self):
        self.reporter = InstrumentMasterQualityReporter()

    def test_reports_normal_distributions(self):
        instruments = [
            make_instrument(),
            make_instrument(
                instrument_token="102",
                symbol="BETA",
                trading_symbol="BETA-EQ",
                exchange_segment="nse_cm",
                market_segment="CASH",
                series="EQ",
                lot_size="100",
            ),
            make_instrument(
                instrument_token="103",
                symbol="GAMMA",
                trading_symbol="GAMMA-BE",
                exchange_segment="bse_cm",
                market_segment=None,
                series="BE",
                lot_size="10",
            ),
        ]

        report = self.reporter.analyze(instruments, ["EQ", " ", None])

        self.assertEqual(report.total_records, 3)
        self.assertEqual(
            report.exchange_segment_distribution,
            (("bse_cm", 1), ("nse_cm", 2)),
        )
        self.assertEqual(report.market_segment_distribution, ((None, 1), ("CASH", 2)))
        self.assertEqual(report.series_distribution, (("BE", 1), ("EQ", 2)))
        self.assertEqual(
            report.lot_size_distribution,
            (("1", 1), ("10", 1), ("100", 1)),
        )
        self.assertEqual(
            report.instrument_type_distribution,
            ((None, 2), ("EQ", 1)),
        )
        self.assertEqual(report.blank_instrument_type_count, 2)

    def test_reports_missing_required_and_optional_fields(self):
        instruments = [
            make_instrument(
                instrument_token="",
                exchange_segment=None,
                symbol=" ",
                trading_symbol="TS-A",
                series=None,
                isin="",
                description="  ",
                market_segment=None,
                lot_size="",
            ),
            make_instrument(instrument_token="102", symbol="BETA"),
        ]

        report = self.reporter.analyze(instruments, ["", "EQ"])

        self.assertEqual(
            report.missing_required_fields,
            (
                ("instrument_token", 1),
                ("exchange_segment", 1),
                ("symbol", 1),
                ("trading_symbol", 0),
            ),
        )
        self.assertEqual(
            report.missing_optional_fields,
            (
                ("series", 1),
                ("isin", 1),
                ("description", 1),
                ("market_segment", 1),
                ("lot_size", 1),
            ),
        )

    def test_reports_duplicate_identifier_group_statistics_and_ignores_blanks(self):
        instruments = [
            make_instrument(instrument_token="DUP", symbol="SAME", trading_symbol="TS", isin="I"),
            make_instrument(instrument_token="DUP", symbol="SAME", trading_symbol="TS", isin="I"),
            make_instrument(instrument_token="DUP", symbol="OTHER", trading_symbol="OTHER-TS", isin="I2"),
            make_instrument(instrument_token="DUP2", symbol="OTHER", trading_symbol="OTHER-TS", isin="I2"),
            make_instrument(instrument_token="", symbol="", trading_symbol=" ", isin=None),
            make_instrument(instrument_token=None, symbol=None, trading_symbol=None, isin=""),
        ]

        report = self.reporter.analyze(instruments, [None] * len(instruments))

        self.assertEqual(report.duplicate_instrument_tokens.distinct_duplicate_values, 1)
        self.assertEqual(report.duplicate_instrument_tokens.affected_record_count, 3)
        self.assertEqual(report.duplicate_instrument_tokens.sample_values, ("DUP",))
        self.assertEqual(report.duplicate_symbols.distinct_duplicate_values, 2)
        self.assertEqual(report.duplicate_symbols.affected_record_count, 4)
        self.assertEqual(report.duplicate_symbols.sample_values, ("OTHER", "SAME"))
        self.assertEqual(report.duplicate_trading_symbols.distinct_duplicate_values, 2)
        self.assertEqual(report.duplicate_trading_symbols.affected_record_count, 4)
        self.assertEqual(
            report.duplicate_trading_symbols.sample_values,
            ("OTHER-TS", "TS"),
        )
        self.assertEqual(report.duplicate_isins.distinct_duplicate_values, 2)
        self.assertEqual(report.duplicate_isins.affected_record_count, 4)
        self.assertEqual(report.duplicate_isins.sample_values, ("I", "I2"))

    def test_lot_size_distribution_preserves_values_and_reports_numeric_signs(self):
        lot_sizes = ("0", "0.0", "-1", " -2 ", "+3", "not-numeric", None)
        instruments = [
            make_instrument(instrument_token=str(index), lot_size=lot_size)
            for index, lot_size in enumerate(lot_sizes)
        ]

        report = self.reporter.analyze(instruments, [None] * len(instruments))

        self.assertEqual(
            report.lot_size_distribution,
            (
                (None, 1),
                (" -2 ", 1),
                ("+3", 1),
                ("-1", 1),
                ("0", 1),
                ("0.0", 1),
                ("not-numeric", 1),
            ),
        )
        self.assertEqual(report.numeric_zero_lot_size_count, 2)
        self.assertEqual(report.numeric_negative_lot_size_count, 2)
        self.assertEqual(report.total_records, len(lot_sizes))

    def test_rejects_mismatched_instrument_type_input_length(self):
        with self.assertRaisesRegex(ValueError, "one value for each instrument"):
            self.reporter.analyze([make_instrument()], [])

    def test_reports_anomalies_without_rejecting_or_mutating_records(self):
        anomalous = make_instrument(
            instrument_token="",
            exchange_segment="nse_cm",
            symbol="SAME",
            trading_symbol="SAME-EQ",
            isin="",
            lot_size="unknown",
        )
        duplicate = make_instrument(
            instrument_token="",
            exchange_segment="nse_cm",
            symbol="SAME",
            trading_symbol="SAME-EQ",
            isin="",
            lot_size="-1",
        )
        original_records = (anomalous, duplicate)

        report = self.reporter.analyze(original_records, ["", "EQ"])

        self.assertEqual(report.total_records, 2)
        self.assertEqual(report.missing_required_fields[0], ("instrument_token", 2))
        self.assertEqual(report.duplicate_instrument_tokens.distinct_duplicate_values, 0)
        self.assertEqual(report.duplicate_symbols.affected_record_count, 2)
        self.assertEqual(report.numeric_negative_lot_size_count, 1)
        self.assertEqual(anomalous.instrument_token, "")
        self.assertEqual(anomalous.lot_size, "unknown")
        self.assertEqual(duplicate.lot_size, "-1")


if __name__ == "__main__":
    unittest.main()
