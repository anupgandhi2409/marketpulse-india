"""Tests for safe local persistence of rendered report PDFs."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from marketpulse.config.settings import Settings
from marketpulse.data.kotak_neo import KotakNeoProvider
from marketpulse.reports.file_store import ReportFileStore, ReportFileStoreError
from tests.test_report_snapshot import _snapshot


PDF_FIXTURE = b"%PDF-1.4\n% deterministic file-store fixture\n%%EOF\n"


class ReportFileStoreTests(TestCase):
    """Verify report naming, no-clobber writes, replacement, and cleanup."""

    def setUp(self):
        """Create an isolated project root and deterministic report snapshot."""
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        config = Settings(project_root=Path(self.temporary_directory.name))
        self.store = ReportFileStore(config)
        self.snapshot = _snapshot()

    def test_evening_filename_uses_snapshot_kind_and_report_date(self):
        """Use the evening identity and date from the supplied snapshot."""
        path = self.store.save(self.snapshot, PDF_FIXTURE)

        self.assertEqual(
            path,
            Path(self.temporary_directory.name)
            / "data"
            / "reports"
            / "marketpulse_evening_2026-11-17.pdf",
        )

    def test_directory_is_created_lazily_and_pdf_bytes_are_saved(self):
        """Create the reports folder only during save and preserve exact bytes."""
        reports_directory = self.store._config.reports_dir
        self.assertFalse(reports_directory.exists())

        path = self.store.save(self.snapshot, PDF_FIXTURE)

        self.assertTrue(reports_directory.is_dir())
        self.assertEqual(path.read_bytes(), PDF_FIXTURE)

    def test_existing_file_is_not_overwritten_by_default(self):
        """Reject duplicate report names without changing the first file."""
        path = self.store.save(self.snapshot, PDF_FIXTURE)

        with self.assertRaisesRegex(ReportFileStoreError, "already exists"):
            self.store.save(self.snapshot, b"%PDF-1.7\nreplacement")

        self.assertEqual(path.read_bytes(), PDF_FIXTURE)

    def test_explicit_overwrite_replaces_the_existing_report(self):
        """Replace an existing report only when overwrite is explicitly true."""
        path = self.store.save(self.snapshot, PDF_FIXTURE)
        replacement = b"%PDF-1.7\nreplacement fixture\n%%EOF\n"

        self.assertEqual(self.store.save(self.snapshot, replacement, overwrite=True), path)
        self.assertEqual(path.read_bytes(), replacement)

    def test_failed_replacement_preserves_existing_report_and_cleans_temp(self):
        """Keep the original file intact and remove the staged file on failure."""
        path = self.store.save(self.snapshot, PDF_FIXTURE)
        with patch("marketpulse.reports.file_store.os.replace", side_effect=OSError):
            with self.assertRaisesRegex(ReportFileStoreError, "could not be saved"):
                self.store.save(self.snapshot, b"%PDF-1.7\nnew data", overwrite=True)

        self.assertEqual(path.read_bytes(), PDF_FIXTURE)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_empty_and_invalid_pdf_content_are_rejected(self):
        """Require bytes with a PDF signature before creating any output."""
        for content in (b"", b"not a pdf", bytearray(PDF_FIXTURE)):
            with self.subTest(content=content[:12]):
                with self.assertRaisesRegex(ReportFileStoreError, "PDF content"):
                    self.store.save(self.snapshot, content)
        self.assertFalse(self.store._config.reports_dir.exists())

    def test_failed_staging_write_cleans_temporary_file(self):
        """Remove the temporary output after a staged write fails."""
        with patch("marketpulse.reports.file_store.os.fsync", side_effect=OSError):
            with self.assertRaisesRegex(ReportFileStoreError, "could not be saved"):
                self.store.save(self.snapshot, PDF_FIXTURE)

        reports_directory = self.store._config.reports_dir
        self.assertEqual(list(reports_directory.iterdir()), [])

    def test_racing_writer_cannot_be_overwritten_without_permission(self):
        """Preserve a target created after staging but before exclusive publish."""
        path = self.store._config.reports_dir / "marketpulse_evening_2026-11-17.pdf"
        racing_bytes = b"%PDF-1.4\nracing writer\n%%EOF\n"

        def create_racing_target(source, target):
            """Simulate another process winning the atomic target-name claim."""
            Path(target).write_bytes(racing_bytes)
            raise FileExistsError

        with patch("marketpulse.reports.file_store.os.link", side_effect=create_racing_target):
            with self.assertRaisesRegex(ReportFileStoreError, "already exists"):
                self.store.save(self.snapshot, PDF_FIXTURE)

        self.assertEqual(path.read_bytes(), racing_bytes)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_saving_needs_no_provider_or_streamlit_integration(self):
        """Save fixture bytes without requesting quotes or importing Streamlit."""
        with patch.object(KotakNeoProvider, "get_quotes") as quote_request:
            path = self.store.save(self.snapshot, PDF_FIXTURE)

        quote_request.assert_not_called()
        self.assertEqual(path.parent, self.store._config.reports_dir)
        self.assertNotIn("streamlit", sys.modules[ReportFileStore.__module__].__dict__)

