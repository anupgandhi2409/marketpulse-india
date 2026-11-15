from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class SectorPerformance:
    """Hold one source-neutral sector index performance snapshot."""

    sector_name: str
    index_value: Decimal
    percent_change: Decimal
    source: str
    retrieved_at: datetime
