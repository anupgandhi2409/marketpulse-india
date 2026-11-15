from dataclasses import dataclass

from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote


@dataclass(frozen=True)
class SilverFuturesQuote(CommodityFuturesQuote):
    """Name the shared commodity-futures snapshot for a standard MCX Silver contract."""
