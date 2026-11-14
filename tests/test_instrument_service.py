import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from marketpulse.config.settings import Settings
from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_quality import (
    InstrumentMasterQualityReporter,
    InstrumentQualityReport,
)
from marketpulse.data.instrument_service import InstrumentMasterService
from marketpulse.data.instrument_store import InstrumentMasterStore


class InstrumentMasterServiceTests(unittest.TestCase):
    HEADERS = "pSymbol,pExchSeg,pSymbolName,pTrdSymbol,pGroup"
    NSE_CSV = (
        f"{HEADERS}\n"
        "2885,nse_cm,RELIANCE,RELIANCE-EQ,EQ\n"
        "11536,nse_cm,TCS,TCS-EQ,EQ\n"
    )
    BSE_CSV = f"{HEADERS}\n500325,bse_cm,RELIANCE,RELIANCE,BSE\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(settings=settings)
        self.parser = InstrumentMasterParser()
        self.service = InstrumentMasterService(store=self.store, parser=self.parser)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_loads_multiple_instruments_from_saved_csv(self):
        self.store.save_csv("nse_cm", self.NSE_CSV)

        instruments = self.service.load_instruments("nse_cm")

        self.assertEqual([instrument.symbol for instrument in instruments], ["RELIANCE", "TCS"])

    def test_finds_reliance_by_symbol(self):
        self.store.save_csv("nse_cm", self.NSE_CSV)

        instrument = self.service.find_by_symbol("nse_cm", "RELIANCE")

        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.symbol, "RELIANCE")
        self.assertEqual(instrument.trading_symbol, "RELIANCE-EQ")

    def test_finds_reliance_by_trading_symbol(self):
        self.store.save_csv("nse_cm", self.NSE_CSV)

        instrument = self.service.find_by_trading_symbol("nse_cm", "RELIANCE-EQ")

        self.assertIsNotNone(instrument)
        self.assertEqual(instrument.symbol, "RELIANCE")
        self.assertEqual(instrument.trading_symbol, "RELIANCE-EQ")

    def test_symbol_lookups_are_exact_and_return_none_when_unknown(self):
        self.store.save_csv("nse_cm", self.NSE_CSV)

        self.assertIsNone(self.service.find_by_symbol("nse_cm", "reliance"))
        self.assertIsNone(self.service.find_by_symbol("nse_cm", "UNKNOWN"))
        self.assertIsNone(self.service.find_by_trading_symbol("nse_cm", "RELIANCE"))
        self.assertIsNone(self.service.find_by_trading_symbol("nse_cm", "UNKNOWN-EQ"))

    def test_keeps_exchange_segments_separate(self):
        self.store.save_csv("nse_cm", self.NSE_CSV)
        self.store.save_csv("bse_cm", self.BSE_CSV)

        nse_instrument = self.service.find_by_symbol("nse_cm", "RELIANCE")
        bse_instrument = self.service.find_by_symbol("bse_cm", "RELIANCE")

        self.assertEqual(nse_instrument.exchange_segment, "nse_cm")
        self.assertEqual(nse_instrument.trading_symbol, "RELIANCE-EQ")
        self.assertEqual(bse_instrument.exchange_segment, "bse_cm")
        self.assertEqual(bse_instrument.trading_symbol, "RELIANCE")

    def test_propagates_missing_file_error(self):
        with self.assertRaises(FileNotFoundError):
            self.service.load_instruments("nse_cm")

    def test_propagates_parser_validation_error(self):
        csv_path = Path(self.temp_dir.name) / "data" / "instruments" / "nse_cm.csv"
        csv_path.parent.mkdir(parents=True)
        csv_path.write_text("pSymbol,pExchSeg,pSymbolName\n2885,nse_cm,RELIANCE\n")

        with self.assertRaisesRegex(ValueError, "missing required headers"):
            self.service.load_instruments("nse_cm")

    def test_uses_injected_store_and_parser(self):
        expected = Instrument(
            instrument_token="2885",
            exchange_segment="nse_cm",
            symbol="RELIANCE",
            trading_symbol="RELIANCE-EQ",
        )
        store = Mock()
        store.load_csv.return_value = "raw csv text"
        parser = Mock()
        parser.parse.return_value = [expected]
        service = InstrumentMasterService(store=store, parser=parser)

        instruments = service.load_instruments("nse_cm")

        store.load_csv.assert_called_once_with("nse_cm")
        parser.parse.assert_called_once_with("raw csv text")
        self.assertEqual(instruments, [expected])

    def test_gets_quality_report_from_one_csv_load_and_preserves_type_values(self):
        csv_text = (
            "\ufeffpSymbol,pExchSeg,pSymbolName,pTrdSymbol, pInstType \n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ,EQ\n"
            "11536,nse_cm,TCS,TCS-EQ, \n"
        )
        self.store.save_csv("nse_cm", csv_text)
        parser = Mock(wraps=self.parser)
        quality_reporter = Mock(wraps=InstrumentMasterQualityReporter())
        service = InstrumentMasterService(
            store=self.store,
            parser=parser,
            quality_reporter=quality_reporter,
        )

        with patch.object(self.store, "load_csv", wraps=self.store.load_csv) as load_csv:
            report = service.get_quality_report("nse_cm")

        instruments = self.parser.parse(csv_text)
        load_csv.assert_called_once_with("nse_cm")
        parser.parse.assert_called_once_with(csv_text)
        quality_reporter.analyze.assert_called_once_with(instruments, ["EQ", " "])
        self.assertIsInstance(report, InstrumentQualityReport)
        self.assertEqual(report.total_records, 2)
        self.assertEqual(report.blank_instrument_type_count, 1)
        self.assertEqual(report.instrument_type_distribution, ((None, 1), ("EQ", 1)))

    def test_quality_report_requires_p_inst_type_column(self):
        self.store.save_csv(
            "nse_cm",
            "pSymbol,pExchSeg,pSymbolName,pTrdSymbol\n"
            "2885,nse_cm,RELIANCE,RELIANCE-EQ\n",
        )

        with self.assertRaisesRegex(
            ValueError,
            "Instrument master CSV is missing required quality-report column 'pInstType'\\.",
        ):
            self.service.get_quality_report("nse_cm")


if __name__ == "__main__":
    unittest.main()
