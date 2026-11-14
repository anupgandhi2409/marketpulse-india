import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from marketpulse.config.settings import Settings
from marketpulse.data.instrument_acquirer import InstrumentMasterAcquirer
from marketpulse.data.instrument_store import InstrumentMasterStore
from marketpulse.data.kotak_neo import KotakNeoProvider


class FakeDownloader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requested_urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested_urls.append(url)
        return self.content


class InstrumentAcquisitionIntegrationTests(unittest.TestCase):
    def test_provider_acquirer_and_store_complete_segment_flow(self):
        segment = "nse_cm"
        location = "https://cdn.example.test/instruments/nse_cm.csv"
        csv_text = "symbol,instrument_token\nRELIANCE,1333\n"
        sdk_client = Mock()
        sdk_client.scrip_master.return_value = location

        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = Settings(project_root=Path(temporary_directory))
            provider = KotakNeoProvider(settings=settings, client=sdk_client)
            store = InstrumentMasterStore(settings=settings)
            downloader = FakeDownloader(csv_text.encode("utf-8"))
            acquirer = InstrumentMasterAcquirer(
                provider=provider,
                store=store,
                downloader=downloader,
            )

            with patch.object(store, "save_csv", wraps=store.save_csv) as save_csv:
                saved_path = acquirer.acquire_and_save(segment)

            expected_path = settings.instrument_dir / f"{segment}.csv"
            self.assertEqual(saved_path, expected_path)
            self.assertTrue(expected_path.is_file())
            self.assertEqual(expected_path.read_text(encoding="utf-8"), csv_text)
            self.assertEqual(store.load_csv(segment), csv_text)

            sdk_client.scrip_master.assert_called_once_with(exchange_segment=segment)
            sdk_client.search_scrip.assert_not_called()
            self.assertEqual(downloader.requested_urls, [location])
            save_csv.assert_called_once_with(segment, csv_text)


if __name__ == "__main__":
    unittest.main()
