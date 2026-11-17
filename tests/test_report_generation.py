"""Tests for the Morning collector-renderer-store workflow."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from marketpulse.config.settings import Settings
from marketpulse.reports.file_store import ReportFileStore, ReportFileStoreError
from marketpulse.reports.generation import ReportGenerationError, generate_market_report
from marketpulse.reports.pdf_renderer import ReportPdfError, ReportPdfRenderer
from marketpulse.reports.snapshot import ReportSection, SectionStatus
from tests.test_report_snapshot import _snapshot

PDF_FIXTURE = b"%PDF-1.4\nfixture\n%%EOF\n"


class ReportGenerationTests(TestCase):
    """Verify the one Morning report path and safe write behavior."""

    def setUp(self):
        """Prepare injected collector, renderer, and file store doubles."""
        self.snapshot = _snapshot()
        self.collector = Mock()
        self.collector.collect.return_value = self.snapshot
        self.renderer = Mock()
        self.renderer.render.return_value = PDF_FIXTURE
        self.file_store = Mock()
        self.file_store.save.return_value = Path("report.pdf")

    def test_generation_calls_components_once_in_order(self):
        """Collect, render, and save one Morning snapshot in sequence."""
        events = []
        self.collector.collect.side_effect = lambda *args: events.append("collect") or self.snapshot
        self.renderer.render.side_effect = lambda value: events.append("render") or PDF_FIXTURE
        self.file_store.save.side_effect = lambda *args, **kwargs: events.append("save") or Path("report.pdf")
        result = generate_market_report(overwrite=True, collector=self.collector, renderer=self.renderer, file_store=self.file_store)
        self.assertEqual(events, ["collect", "render", "save"])
        self.assertEqual(result.snapshot, self.snapshot)
        self.collector.collect.assert_called_once_with(None)

    def test_date_validation_and_existing_file_protection(self):
        """Require a calendar date and preserve no-overwrite defaults."""
        with self.assertRaises(ReportGenerationError):
            generate_market_report(datetime_invalid(), collector=self.collector)
        self.file_store.save.side_effect = ReportFileStoreError("A report with this name already exists.")
        with self.assertRaises(ReportFileStoreError):
            generate_market_report(collector=self.collector, renderer=self.renderer, file_store=self.file_store)
        self.file_store.save.assert_called_once_with(self.snapshot, PDF_FIXTURE, overwrite=False)

    def test_renderer_failure_prevents_publication(self):
        """Do not save a PDF when rendering fails validation."""
        self.renderer.render.side_effect = ReportPdfError("Invalid snapshot.")
        with self.assertRaises(ReportPdfError):
            generate_market_report(collector=self.collector, renderer=self.renderer, file_store=self.file_store)
        self.file_store.save.assert_not_called()

    def test_real_renderer_and_store_work_without_providers(self):
        """Create an isolated Morning PDF from synthetic snapshot data."""
        with TemporaryDirectory() as temporary:
            result = generate_market_report(
                report_date=date(2026, 11, 17),
                config=Settings(project_root=Path(temporary)),
                collector=self.collector,
                renderer=ReportPdfRenderer(),
                file_store=ReportFileStore(Settings(project_root=Path(temporary))),
            )
            self.assertTrue(result.path.read_bytes().startswith(b"%PDF-"))
            self.assertIn("marketpulse_morning_2026-11-17.pdf", str(result.path))


def datetime_invalid():
    """Return a datetime value to exercise calendar-date validation."""
    from datetime import datetime
    return datetime(2026, 11, 17)
