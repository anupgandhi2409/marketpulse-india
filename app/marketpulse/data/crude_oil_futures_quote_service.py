from marketpulse.data.commodity_futures_quote_service import (
    CommodityFuturesDataError,
    CommodityFuturesMasterError,
    CommodityFuturesProviderError,
    CommodityFuturesQuoteService,
)
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote


CrudeOilFuturesMasterError = CommodityFuturesMasterError
CrudeOilFuturesDataError = CommodityFuturesDataError
CrudeOilFuturesProviderError = CommodityFuturesProviderError


class CrudeOilFuturesQuoteService(CommodityFuturesQuoteService):
    """Retrieve the nearest active standard MCX Crude Oil futures quote."""

    COMMODITY = "CRUDEOIL"
    QUOTATION_UNIT = "INR per barrel"
    QUOTE_MODEL = CommodityFuturesQuote
