import unittest

from marketpulse.data.instrument import Instrument


class InstrumentTests(unittest.TestCase):
    def test_maps_normal_equity_record(self):
        instrument = Instrument.from_csv_row(
            {
                "pSymbol": "2885",
                "pExchSeg": "nse_cm",
                "pSymbolName": "RELIANCE",
                "pTrdSymbol": "RELIANCE-EQ",
                "pGroup": "EQ",
                "pISIN": "INE002A01018",
                "pDesc": "RELIANCE INDUSTRIES LTD",
                "pSegment": "CASH",
                "lLotSize": "1",
            }
        )

        self.assertEqual(
            instrument,
            Instrument(
                instrument_token="2885",
                exchange_segment="nse_cm",
                symbol="RELIANCE",
                trading_symbol="RELIANCE-EQ",
                series="EQ",
                isin="INE002A01018",
                description="RELIANCE INDUSTRIES LTD",
                market_segment="CASH",
                lot_size="1",
            ),
        )

    def test_maps_etf_record_without_classifying_it(self):
        instrument = Instrument.from_csv_row(
            {
                "pSymbol": "16253",
                "pExchSeg": "nse_cm",
                "pSymbolName": "EBBETF0430",
                "pTrdSymbol": "EBBETF0430-EQ",
                "pGroup": "EQ",
                "pDesc": "EDELAMC - EBBETF0430",
                "pSegment": "CASH",
            }
        )

        self.assertEqual(instrument.symbol, "EBBETF0430")
        self.assertEqual(instrument.trading_symbol, "EBBETF0430-EQ")
        self.assertEqual(instrument.series, "EQ")
        self.assertEqual(instrument.description, "EDELAMC - EBBETF0430")
        self.assertIsNone(instrument.isin)
        self.assertIsNone(instrument.lot_size)

    def test_trims_whitespace_from_headers_and_values(self):
        instrument = Instrument.from_csv_row(
            {
                " pSymbol ": " 2885 ",
                " pExchSeg ": " nse_cm ",
                " pSymbolName ": " RELIANCE ",
                " pTrdSymbol ": " RELIANCE-EQ ",
                " pGroup ": " EQ ",
                " pDesc ": " RELIANCE INDUSTRIES LTD ",
            }
        )

        self.assertEqual(instrument.instrument_token, "2885")
        self.assertEqual(instrument.exchange_segment, "nse_cm")
        self.assertEqual(instrument.symbol, "RELIANCE")
        self.assertEqual(instrument.trading_symbol, "RELIANCE-EQ")
        self.assertEqual(instrument.series, "EQ")
        self.assertEqual(instrument.description, "RELIANCE INDUSTRIES LTD")

    def test_empty_optional_fields_become_none(self):
        instrument = Instrument.from_csv_row(
            {
                "pSymbol": "2885",
                "pExchSeg": "nse_cm",
                "pSymbolName": "RELIANCE",
                "pTrdSymbol": "RELIANCE-EQ",
                "pGroup": "",
                "pISIN": "   ",
                "pDesc": None,
                "pSegment": "CASH",
                "lLotSize": "",
            }
        )

        self.assertIsNone(instrument.series)
        self.assertIsNone(instrument.isin)
        self.assertIsNone(instrument.description)
        self.assertEqual(instrument.market_segment, "CASH")
        self.assertIsNone(instrument.lot_size)

    def test_missing_or_empty_required_fields_raise_clear_value_error(self):
        valid_row = {
            "pSymbol": "2885",
            "pExchSeg": "nse_cm",
            "pSymbolName": "RELIANCE",
            "pTrdSymbol": "RELIANCE-EQ",
        }
        required_columns = (
            "pSymbol",
            "pExchSeg",
            "pSymbolName",
            "pTrdSymbol",
        )

        for column in required_columns:
            with self.subTest(column=column, issue="missing"):
                row = {key: value for key, value in valid_row.items() if key != column}
                with self.assertRaisesRegex(ValueError, column):
                    Instrument.from_csv_row(row)

            with self.subTest(column=column, issue="empty"):
                row = dict(valid_row)
                row[column] = "  "
                with self.assertRaisesRegex(ValueError, column):
                    Instrument.from_csv_row(row)

    def test_preserves_string_values_including_lot_size(self):
        instrument = Instrument.from_csv_row(
            {
                "pSymbol": "001594",
                "pExchSeg": "nse_cm",
                "pSymbolName": "INFY",
                "pTrdSymbol": "INFY-EQ",
                "lLotSize": "0001",
            }
        )

        self.assertEqual(instrument.instrument_token, "001594")
        self.assertIsInstance(instrument.instrument_token, str)
        self.assertEqual(instrument.lot_size, "0001")
        self.assertIsInstance(instrument.lot_size, str)

    def test_rejects_duplicate_headers_after_trimming(self):
        with self.assertRaisesRegex(ValueError, "Duplicate instrument CSV header"):
            Instrument.from_csv_row(
                {
                    "pSymbol": "2885",
                    " pSymbol ": "other",
                    "pExchSeg": "nse_cm",
                    "pSymbolName": "RELIANCE",
                    "pTrdSymbol": "RELIANCE-EQ",
                }
            )


if __name__ == "__main__":
    unittest.main()
