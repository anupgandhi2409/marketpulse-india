"""Save rendered report PDFs under the configured project data directory."""

import os
import tempfile
from pathlib import Path

from marketpulse.config.settings import Settings, settings
from marketpulse.reports.snapshot import ReportSnapshot


class ReportFileStoreError(ValueError):
    """Signal invalid report output or a safe local file operation failure."""


class ReportFileStore:
    """Persist snapshot PDFs with atomic publication and safe overwrite rules."""

    def __init__(self, config: Settings | None = None) -> None:
        """Use the configured project root to derive the sole reports directory."""
        self._config = config or settings

    def save(
        self,
        snapshot: ReportSnapshot,
        pdf_bytes: bytes,
        overwrite: bool = False,
    ) -> Path:
        """Save PDF bytes under a name derived from the snapshot identity."""
        if not isinstance(snapshot, ReportSnapshot):
            raise ReportFileStoreError("A valid report snapshot is required.")
        try:
            snapshot.__post_init__()
        except Exception:
            raise ReportFileStoreError("The report snapshot is invalid.") from None
        if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
            raise ReportFileStoreError("PDF content must start with a valid PDF signature.")
        if not isinstance(overwrite, bool):
            raise ReportFileStoreError("Overwrite must be explicitly enabled or disabled.")

        filename = f"marketpulse_evening_{snapshot.report_date.isoformat()}.pdf"
        directory = self._config.reports_dir
        target = directory / filename
        temporary_path: Path | None = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{filename}.", suffix=".tmp", dir=directory
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(pdf_bytes)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if overwrite:
                os.replace(temporary_path, target)
            else:
                # A hard link atomically claims an absent name and cannot clobber a racing writer.
                os.link(temporary_path, target)
            return target
        except FileExistsError:
            raise ReportFileStoreError("A report with this name already exists.") from None
        except OSError:
            raise ReportFileStoreError("The report PDF could not be saved.") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
