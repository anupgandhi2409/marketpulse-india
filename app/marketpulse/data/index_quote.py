from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IndexQuote:
    """Source-neutral snapshot of a market index value."""

    index_name: str
    exchange: str
    source: str
    index_value: Decimal
    instrument_identifier: str | None = None
