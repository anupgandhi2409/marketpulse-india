from marketpulse.data.commodity_futures_quote_service import (
    CommodityFuturesDataError,
    CommodityFuturesMasterError,
    CommodityFuturesProviderError,
    CommodityFuturesQuoteService,
)
from marketpulse.data.silver_futures_quote import SilverFuturesQuote


SilverFuturesMasterError = CommodityFuturesMasterError
SilverFuturesDataError = CommodityFuturesDataError
SilverFuturesProviderError = CommodityFuturesProviderError


class SilverFuturesQuoteService(CommodityFuturesQuoteService):
    """Retrieve an active standard MCX Silver futures quote through Kotak Neo."""

    COMMODITY = "SILVER"
    QUOTATION_UNIT = "₹ per kg"
    QUOTE_MODEL = SilverFuturesQuote
