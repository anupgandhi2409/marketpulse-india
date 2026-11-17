from marketpulse.data.commodity_futures_quote_service import (
    CommodityFuturesDataError,
    CommodityFuturesMasterError,
    CommodityFuturesProviderError,
    CommodityFuturesQuoteService,
)
from marketpulse.data.gold_futures_quote import GoldFuturesQuote


GoldFuturesMasterError = CommodityFuturesMasterError
GoldFuturesDataError = CommodityFuturesDataError
GoldFuturesProviderError = CommodityFuturesProviderError


class GoldFuturesQuoteService(CommodityFuturesQuoteService):
    """Retrieve an active standard MCX Gold futures quote through Kotak Neo."""

    COMMODITY = "GOLD"
    QUOTATION_UNIT = "₹ per 10 grams"
    QUOTE_MODEL = GoldFuturesQuote
