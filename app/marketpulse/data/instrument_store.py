import csv
import io
import os
import re
import tempfile
from pathlib import Path

from marketpulse.config.settings import Settings, settings as default_settings


class InstrumentMasterStore:
    """Store validated instrument-master CSV text by exchange segment."""

    _SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else default_settings
        self._instrument_dir = self._settings.instrument_dir

    def save_csv(self, exchange_segment: str, csv_text: str) -> Path:
        """Validate and atomically save a segment's CSV text; return its path."""
        target = self._path_for_segment(exchange_segment)
        self._validate_csv(csv_text)

        self._instrument_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self._instrument_dir,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(csv_text)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, target)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        return target

    def load_csv(self, exchange_segment: str) -> str:
        """Load the saved CSV text for an exchange segment."""
        return self._path_for_segment(exchange_segment).read_text(encoding="utf-8")

    def _path_for_segment(self, exchange_segment: str) -> Path:
        if (
            not isinstance(exchange_segment, str)
            or self._SEGMENT_PATTERN.fullmatch(exchange_segment) is None
        ):
            raise ValueError("exchange_segment must contain only letters, digits, '_' or '-'.")

        directory = Path(self._instrument_dir)
        target = directory / f"{exchange_segment}.csv"
        if target.resolve().parent != directory.resolve():
            raise ValueError("exchange_segment resolves outside the instrument directory.")
        return target

    @staticmethod
    def _validate_csv(csv_text: str) -> None:
        if not isinstance(csv_text, str) or not csv_text.strip():
            raise ValueError("CSV content must not be empty.")

        try:
            rows = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
            header = next(rows, None)
            if header is None or not header or not any(cell.strip() for cell in header):
                raise ValueError("CSV content must contain a header row.")

            expected_columns = len(header)
            data_row_count = 0
            for row in rows:
                if len(row) != expected_columns:
                    raise ValueError("CSV rows must have a consistent number of columns.")
                data_row_count += 1

            if data_row_count == 0:
                raise ValueError("CSV content must contain at least one data row.")
        except csv.Error as exc:
            raise ValueError("CSV content is malformed.") from exc
