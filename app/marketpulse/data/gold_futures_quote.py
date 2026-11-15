from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class GoldFuturesQuote:
    """Hold one source-neutral snapshot for an MCX standard Gold future."""

    commodity: str
    exchange: str
    contract_trading_symbol: str
    instrument_token: str
    price: Decimal
    quotation_unit: str
    absolute_change: Decimal
    percentage_change: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    contract_expiry_timestamp: datetime
    retrieved_at: datetime
    source: str
