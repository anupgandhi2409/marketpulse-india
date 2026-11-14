from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Instrument:
    """A minimal, source-neutral representation of a market instrument."""

    instrument_token: str
    exchange_segment: str
    symbol: str
    trading_symbol: str
    series: str | None = None
    isin: str | None = None
    description: str | None = None
    market_segment: str | None = None
    lot_size: str | None = None

    @classmethod
    def from_csv_row(cls, row: Mapping[str, str | None]) -> "Instrument":
        """Build an instrument from a CSV row keyed by its original headers."""
        values: dict[str, str | None] = {}
        for header, value in row.items():
            if not isinstance(header, str):
                raise ValueError("Instrument CSV headers must be strings.")
            normalized_header = header.strip()
            if normalized_header in values:
                raise ValueError(
                    f"Duplicate instrument CSV header after trimming: {normalized_header!r}."
                )
            if value is not None and not isinstance(value, str):
                raise ValueError("Instrument CSV field values must be strings or None.")
            values[normalized_header] = value.strip() if value is not None else None

        def get_required_value(source_column: str) -> str:
            value = values.get(source_column)
            if value:
                return value
            raise ValueError(f"Instrument CSV is missing required field {source_column!r}.")

        def get_optional_value(source_column: str) -> str | None:
            value = values.get(source_column)
            return value if value else None

        return cls(
            instrument_token=get_required_value("pSymbol"),
            exchange_segment=get_required_value("pExchSeg"),
            symbol=get_required_value("pSymbolName"),
            trading_symbol=get_required_value("pTrdSymbol"),
            series=get_optional_value("pGroup"),
            isin=get_optional_value("pISIN"),
            description=get_optional_value("pDesc"),
            market_segment=get_optional_value("pSegment"),
            lot_size=get_optional_value("lLotSize"),
        )
