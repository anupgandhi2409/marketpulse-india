from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class IndexQuote:
    """Source-neutral snapshot of a market index value."""

    index_name: str
    exchange: str
    source: str
    index_value: Decimal
    instrument_identifier: str | None = None
    change: Decimal | None = None
    percent_change: Decimal | None = None
    quote_timestamp: datetime | None = None
