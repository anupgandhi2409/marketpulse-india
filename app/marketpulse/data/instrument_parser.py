import csv
import io

from marketpulse.data.instrument import Instrument


class InstrumentMasterParser:
    """Parse Kotak instrument-master CSV text into instrument records."""

    _REQUIRED_HEADERS = ("pSymbol", "pExchSeg", "pSymbolName", "pTrdSymbol")

    def parse(self, csv_text: str) -> list[Instrument]:
        """Parse CSV text, rejecting malformed rows instead of skipping them."""
        if not csv_text or not csv_text.strip():
            raise ValueError("Instrument master CSV must not be empty.")

        # A BOM may remain at the start of decoded text even when the caller
        # did not use the acquirer's utf-8-sig decoder.
        csv_text = csv_text.removeprefix("\ufeff")
        try:
            reader = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
            headers = next(reader, None)
            if headers is None or not headers or not any(header.strip() for header in headers):
                raise ValueError("Instrument master CSV is missing its header row.")

            normalized_headers = [header.strip() for header in headers]
            if len(set(normalized_headers)) != len(normalized_headers):
                raise ValueError("Instrument master CSV has duplicate headers after trimming.")

            missing_headers = [
                header for header in self._REQUIRED_HEADERS if header not in normalized_headers
            ]
            if missing_headers:
                missing = ", ".join(missing_headers)
                raise ValueError(f"Instrument master CSV is missing required headers: {missing}.")

            instruments: list[Instrument] = []
            for row_number, values in enumerate(reader, start=2):
                if len(values) != len(normalized_headers):
                    raise ValueError(
                        f"CSV row {row_number} has {len(values)} columns; "
                        f"expected {len(normalized_headers)}."
                    )
                row = dict(zip(normalized_headers, values, strict=True))
                try:
                    instruments.append(Instrument.from_csv_row(row))
                except ValueError as exc:
                    raise ValueError(f"Invalid instrument on CSV row {row_number}: {exc}") from exc

            if not instruments:
                raise ValueError("Instrument master CSV must contain at least one data row.")
            return instruments
        except csv.Error as exc:
            raise ValueError("Instrument master CSV is malformed.") from exc
