import tempfile
import unittest
from pathlib import Path

from marketpulse.config.settings import Settings
from marketpulse.data.instrument_store import InstrumentMasterStore


class InstrumentMasterStoreTests(unittest.TestCase):
    SAMPLE_CSV = "symbol,instrument_token\nRELIANCE,1333\n"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        settings = Settings(project_root=Path(self.temp_dir.name))
        self.store = InstrumentMasterStore(settings=settings)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_saves_valid_csv(self):
        path = self.store.save_csv("nse_cm", self.SAMPLE_CSV)

        self.assertEqual(path, Path(self.temp_dir.name) / "data" / "instruments" / "nse_cm.csv")
        self.assertEqual(path.read_text(encoding="utf-8"), self.SAMPLE_CSV)

    def test_loads_saved_csv(self):
        self.store.save_csv("nse_cm", self.SAMPLE_CSV)

        self.assertEqual(self.store.load_csv("nse_cm"), self.SAMPLE_CSV)

    def test_uses_separate_files_for_exchange_segments(self):
        nse_csv = "symbol,token\nNIFTY,1\n"
        bse_csv = "symbol,token\nSENSEX,2\n"

        nse_path = self.store.save_csv("nse_cm", nse_csv)
        bse_path = self.store.save_csv("bse_cm", bse_csv)

        self.assertNotEqual(nse_path, bse_path)
        self.assertEqual(nse_path.name, "nse_cm.csv")
        self.assertEqual(bse_path.name, "bse_cm.csv")
        self.assertEqual(self.store.load_csv("nse_cm"), nse_csv)
        self.assertEqual(self.store.load_csv("bse_cm"), bse_csv)

    def test_creates_instrument_directory_only_when_saving(self):
        instrument_dir = Path(self.temp_dir.name) / "data" / "instruments"
        self.assertFalse(instrument_dir.exists())

        self.store.save_csv("nse_cm", self.SAMPLE_CSV)

        self.assertTrue(instrument_dir.is_dir())

    def test_rejects_empty_csv(self):
        for content in ("", " \n\t"):
            with self.subTest(content=repr(content)):
                with self.assertRaises(ValueError):
                    self.store.save_csv("nse_cm", content)

    def test_rejects_invalid_csv(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.store.save_csv("nse_cm", 'symbol,description\nABC,"unterminated\n')

    def test_rejects_csv_without_header(self):
        with self.assertRaisesRegex(ValueError, "header"):
            self.store.save_csv("nse_cm", ",\nRELIANCE,1333\n")

    def test_rejects_csv_without_data_rows(self):
        with self.assertRaisesRegex(ValueError, "at least one data row"):
            self.store.save_csv("nse_cm", "symbol,instrument_token\n")

    def test_rejects_inconsistent_row_lengths(self):
        content = "symbol,instrument_token\nRELIANCE,1333,extra\n"

        with self.assertRaisesRegex(ValueError, "consistent"):
            self.store.save_csv("nse_cm", content)

    def test_rejects_invalid_exchange_segment_values(self):
        for segment in ("../outside", "nse/cm", "nse cm", "", ".", "..", "nse_cm.csv"):
            with self.subTest(segment=segment):
                with self.assertRaises(ValueError):
                    self.store.save_csv(segment, self.SAMPLE_CSV)

    def test_failed_replacement_keeps_existing_valid_data_unchanged(self):
        path = self.store.save_csv("nse_cm", self.SAMPLE_CSV)

        with self.assertRaises(ValueError):
            self.store.save_csv("nse_cm", "symbol,instrument_token\nBROKEN\n")

        self.assertEqual(path.read_text(encoding="utf-8"), self.SAMPLE_CSV)


if __name__ == "__main__":
    unittest.main()
