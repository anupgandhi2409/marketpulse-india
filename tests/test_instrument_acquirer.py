import io
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

from marketpulse.data.instrument_acquirer import (
    HttpsInstrumentDownloader,
    InstrumentAcquisitionError,
    InstrumentDownloadError,
    InstrumentMasterAcquirer,
    InstrumentResponseTooLargeError,
    _HttpsOnlyRedirectHandler,
)


class InstrumentMasterAcquirerTests(unittest.TestCase):
    URL = "https://cdn.example.test/instruments/nse_cm.csv"
    CSV_BYTES = b"symbol,token\nRELIANCE,1333\n"

    def setUp(self):
        self.provider = Mock()
        self.provider.get_scrip_master.return_value = self.URL
        self.store = Mock()
        self.store.save_csv.return_value = "/data/instruments/nse_cm.csv"
        self.downloader = Mock(return_value=self.CSV_BYTES)
        self.acquirer = InstrumentMasterAcquirer(
            provider=self.provider,
            store=self.store,
            downloader=self.downloader,
        )

    def test_successful_acquisition_and_saving(self):
        result = self.acquirer.acquire_and_save("nse_cm")

        self.assertEqual(result, "/data/instruments/nse_cm.csv")
        self.downloader.assert_called_once_with(self.URL)
        self.store.save_csv.assert_called_once_with(
            "nse_cm", "symbol,token\nRELIANCE,1333\n"
        )

    def test_forwards_segment_to_provider_and_store(self):
        self.acquirer.acquire_and_save("bse_cm")

        self.provider.get_scrip_master.assert_called_once_with(exchange_segment="bse_cm")
        self.store.save_csv.assert_called_once_with(
            "bse_cm", "symbol,token\nRELIANCE,1333\n"
        )

    def test_decodes_utf8_bom(self):
        self.downloader.return_value = b"\xef\xbb\xbfsymbol,token\nRELIANCE,1333\n"

        self.acquirer.acquire_and_save("nse_cm")

        self.store.save_csv.assert_called_once_with(
            "nse_cm", "symbol,token\nRELIANCE,1333\n"
        )

    def test_rejects_sdk_error_dictionaries_without_downloading(self):
        for response in (
            {"Error": "failed"},
            {"error": [{"message": "failed"}]},
            {"message": "failed"},
        ):
            with self.subTest(response=response):
                self.provider.get_scrip_master.return_value = response
                with self.assertRaises(InstrumentAcquisitionError):
                    self.acquirer.acquire_and_save("nse_cm")

        self.downloader.assert_not_called()
        self.store.save_csv.assert_not_called()

    def test_rejects_empty_or_unsupported_locations(self):
        for location in (None, "", "   ", b"https://example.test/file.csv", 123):
            with self.subTest(location=location):
                self.provider.get_scrip_master.return_value = location
                with self.assertRaises(InstrumentAcquisitionError):
                    self.acquirer.acquire_and_save("nse_cm")

    def test_rejects_relative_location(self):
        self.provider.get_scrip_master.return_value = "/path/instruments.csv"

        with self.assertRaisesRegex(InstrumentAcquisitionError, "absolute HTTPS URL"):
            self.acquirer.acquire_and_save("nse_cm")

        self.downloader.assert_not_called()

    def test_rejects_non_https_location(self):
        self.provider.get_scrip_master.return_value = "http://example.test/instruments.csv"

        with self.assertRaisesRegex(InstrumentAcquisitionError, "absolute HTTPS URL"):
            self.acquirer.acquire_and_save("nse_cm")

        self.downloader.assert_not_called()

    def test_rejects_location_with_embedded_credentials(self):
        self.provider.get_scrip_master.return_value = "https://user:secret@example.test/file.csv"

        with self.assertRaises(InstrumentAcquisitionError):
            self.acquirer.acquire_and_save("nse_cm")

        self.downloader.assert_not_called()

    def test_download_error_propagates_without_saving(self):
        self.downloader.side_effect = InstrumentDownloadError("download failed")

        with self.assertRaises(InstrumentDownloadError):
            self.acquirer.acquire_and_save("nse_cm")

        self.store.save_csv.assert_not_called()

    def test_rejects_invalid_utf8(self):
        self.downloader.return_value = b"symbol,token\n\xff,1\n"

        with self.assertRaisesRegex(InstrumentAcquisitionError, "valid UTF-8"):
            self.acquirer.acquire_and_save("nse_cm")

        self.store.save_csv.assert_not_called()

    def test_invalid_csv_is_rejected_by_store(self):
        self.store.save_csv.side_effect = ValueError("invalid CSV")

        with self.assertRaisesRegex(ValueError, "invalid CSV"):
            self.acquirer.acquire_and_save("nse_cm")

        self.store.save_csv.assert_called_once()


class _FakeResponse(io.BytesIO):
    def __init__(self, content=b"csv", status=200, final_url="https://cdn.example.test/file.csv"):
        super().__init__(content)
        self.status = status
        self.final_url = final_url
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def getcode(self):
        return self.status

    def geturl(self):
        return self.final_url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class _ShortReadResponse(_FakeResponse):
    def __init__(self, content, max_chunk_size, **kwargs):
        super().__init__(content=content, **kwargs)
        self.max_chunk_size = max_chunk_size

    def read(self, size=-1):
        if size < 0:
            size = self.max_chunk_size
        return super().read(min(size, self.max_chunk_size))


class HttpsInstrumentDownloaderTests(unittest.TestCase):
    URL = "https://cdn.example.test/file.csv"

    def test_download_uses_timeout_checks_success_and_returns_raw_bytes(self):
        response = _FakeResponse(content=b"a,b\n1,2\n")
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(timeout=7, opener=opener)

        result = downloader(self.URL)

        self.assertEqual(result, b"a,b\n1,2\n")
        request, = opener.open.call_args.args
        self.assertIsInstance(request, Request)
        self.assertEqual(request.full_url, self.URL)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 7)

    def test_rejects_http_error(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(self.URL, 503, "Unavailable", {}, None)
        downloader = HttpsInstrumentDownloader(opener=opener)

        with self.assertRaisesRegex(InstrumentDownloadError, "503"):
            downloader(self.URL)

    def test_rejects_unsuccessful_status(self):
        response = _FakeResponse(status=500)
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(opener=opener)

        with self.assertRaisesRegex(InstrumentDownloadError, "500"):
            downloader(self.URL)

    def test_rejects_redirect_or_final_response_to_non_https(self):
        response = _FakeResponse(final_url="http://cdn.example.test/file.csv")
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(opener=opener)

        with self.assertRaisesRegex(InstrumentDownloadError, "HTTPS"):
            downloader(self.URL)

        handler = _HttpsOnlyRedirectHandler()
        with self.assertRaisesRegex(InstrumentDownloadError, "redirect must use HTTPS"):
            handler.redirect_request(
                Request(self.URL), None, 302, "Found", {}, "http://cdn.example.test/file.csv"
            )

    def test_rejects_oversized_response(self):
        response = _FakeResponse(content=b"12345")
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(max_response_bytes=4, opener=opener)

        with self.assertRaises(InstrumentResponseTooLargeError):
            downloader(self.URL)

        self.assertEqual(response.read_sizes, [5])

    def test_returns_complete_response_across_multiple_short_reads(self):
        payload = b"123456789"
        response = _ShortReadResponse(payload, max_chunk_size=2)
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(max_response_bytes=12, opener=opener)

        self.assertEqual(downloader(self.URL), payload)
        self.assertGreater(len(response.read_sizes), 1)

    def test_rejects_limit_exceeded_across_multiple_short_reads(self):
        response = _ShortReadResponse(b"123456", max_chunk_size=2)
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(max_response_bytes=5, opener=opener)

        with self.assertRaises(InstrumentResponseTooLargeError):
            downloader(self.URL)

        self.assertGreater(len(response.read_sizes), 1)

    def test_accepts_response_exactly_at_limit_across_short_reads(self):
        payload = b"12345"
        response = _ShortReadResponse(payload, max_chunk_size=2)
        opener = Mock()
        opener.open.return_value = response
        downloader = HttpsInstrumentDownloader(max_response_bytes=5, opener=opener)

        self.assertEqual(downloader(self.URL), payload)
        self.assertGreater(len(response.read_sizes), 1)

    def test_handles_timeout(self):
        opener = Mock()
        opener.open.side_effect = TimeoutError()
        downloader = HttpsInstrumentDownloader(opener=opener)

        with self.assertRaisesRegex(InstrumentDownloadError, "timed out"):
            downloader(self.URL)

    def test_rejects_non_https_initial_url(self):
        opener = Mock()
        downloader = HttpsInstrumentDownloader(opener=opener)

        with self.assertRaisesRegex(InstrumentDownloadError, "absolute HTTPS URL"):
            downloader("http://cdn.example.test/file.csv")

        opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
