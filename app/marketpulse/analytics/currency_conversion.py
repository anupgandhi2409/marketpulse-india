from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class CurrencyConversionError(ValueError):
    """Raised when an indicative INR-to-USD conversion cannot be validated."""


@dataclass(frozen=True)
class IndicativeUsdPerBarrel:
    """Describe an indicative converted price and the explicitly supplied FX rate."""

    usd_per_barrel: Decimal
    inr_per_barrel: Decimal
    inr_per_usd: Decimal
    rate_source: str
    rate_instrument_type: str
    description: str = "Indicative conversion only; not a WTI or Brent quote."


def convert_crudeoil_inr_to_indicative_usd(
    inr_per_barrel: Decimal,
    inr_per_usd: Decimal | None,
    *,
    rate_source: str,
    rate_instrument_type: str,
) -> IndicativeUsdPerBarrel:
    """Convert positive INR prices with an identified rate and half-up cent rounding."""
    if not isinstance(inr_per_barrel, Decimal) or not _is_finite_positive(inr_per_barrel):
        raise CurrencyConversionError("Crude Oil INR price must be a finite positive Decimal.")
    if not isinstance(inr_per_usd, Decimal) or not _is_finite_positive(inr_per_usd):
        raise CurrencyConversionError("USD/INR rate must be a finite positive Decimal.")
    if not isinstance(rate_source, str) or not rate_source.strip():
        raise CurrencyConversionError("USD/INR rate source must be identified.")
    if not isinstance(rate_instrument_type, str) or not rate_instrument_type.strip():
        raise CurrencyConversionError("USD/INR rate instrument type must be identified.")

    try:
        converted = (inr_per_barrel / inr_per_usd).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ZeroDivisionError):
        raise CurrencyConversionError("Indicative USD price could not be calculated.") from None
    return IndicativeUsdPerBarrel(
        usd_per_barrel=converted,
        inr_per_barrel=inr_per_barrel,
        inr_per_usd=inr_per_usd,
        rate_source=rate_source.strip(),
        rate_instrument_type=rate_instrument_type.strip(),
    )


def _is_finite_positive(value: Decimal) -> bool:
    """Return whether a Decimal is finite and greater than zero."""
    return value.is_finite() and value > 0
