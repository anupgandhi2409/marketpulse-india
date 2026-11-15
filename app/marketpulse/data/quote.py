from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Quote:
    """Source-neutral snapshot of an equity quote."""

    instrument_token: str
    trading_symbol: str
    exchange_segment: str
    last_traded_price: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    previous_close: Decimal | None = None
    volume: int | None = None
