"""Compose, render, and save MarketPulse reports without dashboard coupling."""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from marketpulse.config.settings import Settings
from marketpulse.reports.collector import ReportDataCollector, create_report_data_collector
from marketpulse.reports.file_store import ReportFileStore, ReportFileStoreError
from marketpulse.reports.pdf_renderer import ReportPdfError, ReportPdfRenderer
from marketpulse.reports.snapshot import ReportSnapshot


class ReportGenerationError(RuntimeError):
    """Signal a sanitized failure while collecting or rendering a report."""


@dataclass(frozen=True)
class ReportGenerationResult:
    """Return the validated snapshot together with its saved PDF path."""

    snapshot: ReportSnapshot
    path: Path


def generate_market_report(
    report_date: date | None = None,
    overwrite: bool = False,
    *,
    config: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    collector: ReportDataCollector | None = None,
    renderer: ReportPdfRenderer | None = None,
    file_store: ReportFileStore | None = None,
) -> ReportGenerationResult:
    """Collect the Morning snapshot, render its PDF, and save it using existing components."""
    if report_date is not None and (
        not isinstance(report_date, date) or isinstance(report_date, datetime)
    ):
        raise ReportGenerationError("Report date must be a calendar date.")
    if not isinstance(overwrite, bool):
        raise ReportGenerationError("Overwrite must be explicitly enabled or disabled.")

    try:
        data_collector = collector or create_report_data_collector(
            settings=config, clock=clock
        )
        snapshot = data_collector.collect(report_date)
    except Exception:
        raise ReportGenerationError("The report snapshot could not be collected safely.") from None

    pdf_renderer = renderer or ReportPdfRenderer()
    try:
        pdf_bytes = pdf_renderer.render(snapshot)
    except ReportPdfError:
        raise
    except Exception:
        raise ReportGenerationError("The report PDF could not be rendered safely.") from None

    output_store = file_store or ReportFileStore(config=config)
    try:
        path = output_store.save(snapshot, pdf_bytes, overwrite=overwrite)
    except ReportFileStoreError:
        raise
    except Exception:
        raise ReportGenerationError("The report PDF could not be saved safely.") from None
    return ReportGenerationResult(snapshot=snapshot, path=path)
