from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CurrencyFuturesQuote:
    """Hold a source-neutral snapshot for one currency futures contract."""

    currency_pair: str
    instrument_type: str
    exchange_segment: str
    contract_trading_symbol: str
    instrument_token: str
    price: Decimal
    quotation_unit: str
    lot_size: int
    contract_size_usd: Decimal
    absolute_change: Decimal
    percentage_change: Decimal
    contract_expiry_timestamp: datetime
    retrieved_at: datetime
    source: str
