import csv
import unittest
from pathlib import Path

from marketpulse.config.settings import Settings
from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_quality import (
    InstrumentMasterQualityReporter,
    InstrumentQualityReport,
)
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "instruments" / "nse_cm.csv"


@unittest.skipUnless(SNAPSHOT_PATH.is_file(), "local NSE Cash snapshot is unavailable")
class RealInstrumentMasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv_text = SNAPSHOT_PATH.read_text(encoding="utf-8")

    def setUp(self):
        self.parser = InstrumentMasterParser()
        self.instruments = self.parser.parse(self.csv_text)

    def find_by_symbol(self, symbol):
        return next(
            (instrument for instrument in self.instruments if instrument.symbol == symbol),
            None,
        )

    def test_snapshot_parses_to_a_nonempty_result(self):
        instruments = self.parser.parse(self.csv_text)

        self.assertTrue(instruments)

    def test_every_instrument_has_required_fields(self):
        required_fields = (
            "instrument_token",
            "exchange_segment",
            "symbol",
            "trading_symbol",
        )

        for row_number, instrument in enumerate(self.instruments, start=1):
            with self.subTest(row_number=row_number, symbol=instrument.symbol):
                for field in required_fields:
                    self.assertTrue(getattr(instrument, field).strip(), field)

    def test_known_instruments_are_found_by_exact_symbol(self):
        for symbol in ("RELIANCE", "INFY", "TCS", "HDFCBANK"):
            with self.subTest(symbol=symbol):
                instrument = self.find_by_symbol(symbol)

                self.assertIsNotNone(instrument)
                self.assertEqual(instrument.symbol, symbol)
                for field in (
                    "instrument_token",
                    "exchange_segment",
                    "symbol",
                    "trading_symbol",
                ):
                    self.assertTrue(getattr(instrument, field).strip(), field)

    def test_negative_lot_size_is_preserved_when_present(self):
        negative_lot_instruments = [
            instrument for instrument in self.instruments if instrument.lot_size == "-1"
        ]
        if not negative_lot_instruments:
            self.skipTest('snapshot has no instrument with lot_size == "-1"')

        for instrument in negative_lot_instruments:
            with self.subTest(symbol=instrument.symbol):
                self.assertEqual(instrument.lot_size, "-1")
                self.assertIsInstance(instrument.lot_size, str)

    def test_etf_named_record_uses_generic_instrument_mapping(self):
        instrument = self.find_by_symbol("EBBETF0430")
        self.assertIsNotNone(instrument)
        self.assertIsInstance(instrument, Instrument)

        with SNAPSHOT_PATH.open("r", encoding="utf-8-sig", newline="") as csv_file:
            source_rows = csv.DictReader(csv_file)
            source_row = next(
                row
                for row in source_rows
                if row.get("pSymbolName", "").strip() == "EBBETF0430"
            )

        normalized_row = {
            header.strip(): value.strip() if value is not None else None
            for header, value in source_row.items()
        }
        mapped_fields = {
            "pSymbol": "instrument_token",
            "pExchSeg": "exchange_segment",
            "pSymbolName": "symbol",
            "pTrdSymbol": "trading_symbol",
            "pGroup": "series",
            "pISIN": "isin",
            "pDesc": "description",
            "pSegment": "market_segment",
            "lLotSize": "lot_size",
        }
        for source_field, model_field in mapped_fields.items():
            source_value = normalized_row.get(source_field) or None
            with self.subTest(source_field=source_field, model_field=model_field):
                self.assertEqual(getattr(instrument, model_field), source_value)

    def test_service_quality_report_integrates_with_local_snapshot(self):
        original_bytes = SNAPSHOT_PATH.read_bytes()
        settings = Settings(project_root=PROJECT_ROOT)
        store = InstrumentMasterStore(settings=settings)
        parser = InstrumentMasterParser()
        reporter = InstrumentMasterQualityReporter()
        service = InstrumentMasterService(
            store=store,
            parser=parser,
            quality_reporter=reporter,
        )

        parsed_snapshot = parser.parse(original_bytes.decode("utf-8"))
        report = service.get_quality_report("nse_cm")

        self.assertIsInstance(report, InstrumentQualityReport)
        self.assertGreater(report.total_records, 0)
        self.assertEqual(report.total_records, len(parsed_snapshot))
        self.assertTrue(
            all(missing_count == 0 for _, missing_count in report.missing_required_fields)
        )
        self.assertEqual(
            sum(count for _, count in report.instrument_type_distribution),
            report.total_records,
        )
        self.assertEqual(
            dict(report.instrument_type_distribution).get(None, 0),
            report.blank_instrument_type_count,
        )
        self.assertEqual(SNAPSHOT_PATH.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
