import unittest

from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_parser import InstrumentMasterParser


class InstrumentMasterParserTests(unittest.TestCase):
    HEADERS = (
        "pSymbol,pExchSeg,pSymbolName,pTrdSymbol,pGroup,pISIN,pDesc,pSegment,lLotSize"
    )

    def setUp(self):
        self.parser = InstrumentMasterParser()

    def test_parses_multiple_equity_and_etf_records(self):
        csv_text = (
            f"{self.HEADERS}\n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ,EQ,INE002A01018,"
            "RELIANCE INDUSTRIES LTD,CASH,1\n"
            "11536,nse_cm,TCS,TCS-EQ,EQ,INE467B01029,TATA CONSULTANCY SERVICES,CASH,1\n"
            "16253,nse_cm,EBBETF0430,EBBETF0430-EQ,EQ,,EDELAMC - EBBETF0430,CASH,1\n"
        )

        instruments = self.parser.parse(csv_text)

        self.assertEqual(
            instruments,
            [
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
                Instrument(
                    instrument_token="11536",
                    exchange_segment="nse_cm",
                    symbol="TCS",
                    trading_symbol="TCS-EQ",
                    series="EQ",
                    isin="INE467B01029",
                    description="TATA CONSULTANCY SERVICES",
                    market_segment="CASH",
                    lot_size="1",
                ),
                Instrument(
                    instrument_token="16253",
                    exchange_segment="nse_cm",
                    symbol="EBBETF0430",
                    trading_symbol="EBBETF0430-EQ",
                    series="EQ",
                    isin=None,
                    description="EDELAMC - EBBETF0430",
                    market_segment="CASH",
                    lot_size="1",
                ),
            ],
        )

    def test_accepts_headers_with_trailing_whitespace(self):
        headers = ",".join(f"{header}  " for header in self.HEADERS.split(","))
        csv_text = (
            f"{headers}\n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ,EQ,INE002A01018,"
            "RELIANCE INDUSTRIES LTD,CASH,1\n"
        )

        instrument, = self.parser.parse(csv_text)

        self.assertEqual(instrument.instrument_token, "2885")
        self.assertEqual(instrument.trading_symbol, "RELIANCE-EQ")

    def test_accepts_utf8_bom(self):
        csv_text = (
            "\ufeff"
            f"{self.HEADERS}\n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ,EQ,INE002A01018,"
            "RELIANCE INDUSTRIES LTD,CASH,1\n"
        )

        instrument, = self.parser.parse(csv_text)

        self.assertEqual(instrument.symbol, "RELIANCE")

    def test_rejects_empty_csv(self):
        for csv_text in ("", " \n\t"):
            with self.subTest(csv_text=repr(csv_text)):
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    self.parser.parse(csv_text)

    def test_rejects_missing_header(self):
        with self.assertRaisesRegex(ValueError, "missing its header row"):
            self.parser.parse(",,\n")

    def test_rejects_missing_required_headers(self):
        with self.assertRaisesRegex(ValueError, "missing required headers.*pTrdSymbol"):
            self.parser.parse("pSymbol,pExchSeg,pSymbolName\n2885,nse_cm,RELIANCE\n")

    def test_rejects_duplicate_headers_after_trimming(self):
        csv_text = (
            "pSymbol, pSymbol ,pExchSeg,pSymbolName,pTrdSymbol\n"
            "2885,2885,nse_cm,RELIANCE,RELIANCE-EQ\n"
        )

        with self.assertRaisesRegex(ValueError, "duplicate headers after trimming"):
            self.parser.parse(csv_text)

    def test_rejects_rows_with_fewer_or_more_columns(self):
        for values in (
            "2885,nse_cm,RELIANCE",
            "2885,nse_cm,RELIANCE,RELIANCE-EQ,extra",
        ):
            with self.subTest(values=values):
                csv_text = f"pSymbol,pExchSeg,pSymbolName,pTrdSymbol\n{values}\n"
                with self.assertRaisesRegex(ValueError, "CSV row 2 has"):
                    self.parser.parse(csv_text)

    def test_identifies_csv_row_with_missing_required_value(self):
        csv_text = (
            "pSymbol,pExchSeg,pSymbolName,pTrdSymbol\n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ\n"
            "11536,nse_cm,TCS,\n"
        )

        with self.assertRaisesRegex(ValueError, "CSV row 3.*pTrdSymbol"):
            self.parser.parse(csv_text)

    def test_rejects_malformed_csv(self):
        csv_text = (
            "pSymbol,pExchSeg,pSymbolName,pTrdSymbol,pDesc\n"
            '2885,nse_cm,RELIANCE,RELIANCE-EQ,"unfinished\n'
        )

        with self.assertRaisesRegex(ValueError, "malformed"):
            self.parser.parse(csv_text)

    def test_preserves_strings_and_maps_empty_optional_values(self):
        csv_text = (
            "pSymbol,pExchSeg,pSymbolName,pTrdSymbol,pGroup,pISIN,pDesc,pSegment,lLotSize\n"
            "001594,nse_cm,INFY,INFY-EQ,,,,CASH,0001\n"
        )

        instrument, = self.parser.parse(csv_text)

        self.assertEqual(instrument.instrument_token, "001594")
        self.assertEqual(instrument.lot_size, "0001")
        self.assertIsInstance(instrument.instrument_token, str)
        self.assertIsInstance(instrument.lot_size, str)
        self.assertIsNone(instrument.series)
        self.assertIsNone(instrument.isin)
        self.assertIsNone(instrument.description)
        self.assertEqual(instrument.market_segment, "CASH")

    def test_does_not_return_empty_data_set(self):
        with self.assertRaisesRegex(ValueError, "at least one data row"):
            self.parser.parse(f"{self.HEADERS}\n")


if __name__ == "__main__":
    unittest.main()
