from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from marketpulse.data.kotak_neo import KotakNeoProvider
from marketpulse.data.sector_performance import SectorPerformance


class SectorPerformanceProviderError(RuntimeError):
    """Raised when the provider cannot retrieve the sector quote batch."""


class SectorPerformanceDataError(ValueError):
    """Raised when sector quote coverage or market values are invalid."""


class SectorPerformanceService:
    """Retrieve complete NSE sector performance snapshots through Kotak REST."""

    SOURCE = "Kotak Neo"
    EXCHANGE_SEGMENT = "nse_cm"
    QUOTE_TYPE = "all"
    SECTORS = (
        ("NIFTY BANK", "Nifty Bank"),
        ("NIFTY IT", "Nifty IT"),
        ("NIFTY AUTO", "Nifty Auto"),
        ("NIFTY PHARMA", "Nifty Pharma"),
        ("NIFTY FMCG", "Nifty FMCG"),
        ("NIFTY METAL", "Nifty Metal"),
        ("NIFTY REALTY", "Nifty Realty"),
        ("NIFTY ENERGY", "Nifty Energy"),
        ("NIFTY PSU BANK", "Nifty PSU Bank"),
        ("NIFTY MEDIA", "Nifty Media"),
        ("NIFTY FINANCIAL SERVICES", "Nifty Fin Service"),
        ("NIFTY OIL & GAS", "NIFTY OIL AND GAS"),
    )

    def __init__(
        self,
        provider: KotakNeoProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Set the quote provider and optional retrieval-time clock."""
        self._provider = provider
        self._clock = clock or _utc_now

    def get_sector_performance(self) -> tuple[SectorPerformance, ...]:
        """Fetch and validate all supported sectors in their display order."""
        request = [
            {"exchange_segment": self.EXCHANGE_SEGMENT, "instrument_token": identifier}
            for _, identifier in self.SECTORS
        ]
        try:
            response = self._provider.get_quotes(
                instrument_tokens=request, quote_type=self.QUOTE_TYPE
            )
        except Exception as exc:
            raise SectorPerformanceProviderError(
                f"Kotak Neo sector quote request failed ({type(exc).__name__})."
            ) from exc

        if isinstance(response, dict):
            category = "provider error" if response.get("stat") != "Not_Ok" else "Not_Ok"
            raise SectorPerformanceProviderError(
                f"Kotak Neo sector quote request failed ({category})."
            )
        if not isinstance(response, list):
            raise SectorPerformanceDataError("Kotak Neo sector response must be a list.")
        if len(response) != len(self.SECTORS):
            raise SectorPerformanceDataError("Sector quote response does not contain 12 records.")

        requested = {identifier: name for name, identifier in self.SECTORS}
        values: dict[str, tuple[Decimal, Decimal]] = {}
        for row in response:
            if not isinstance(row, dict):
                raise SectorPerformanceDataError("Sector quote row must be an object.")
            token = row.get("exchange_token")
            exchange = row.get("exchange")
            if not isinstance(token, str) or not isinstance(exchange, str):
                raise SectorPerformanceDataError("Sector quote row has no valid identity fields.")
            if exchange != self.EXCHANGE_SEGMENT or token not in requested:
                raise SectorPerformanceDataError("Sector quote response contains an unexpected identity.")
            if token in values:
                raise SectorPerformanceDataError("Sector quote response contains a duplicate identity.")
            index_value = _finite_decimal(row.get("ltp"), "index value", token)
            percent_change = _finite_decimal(row.get("per_change"), "percentage change", token)
            if index_value <= 0:
                raise SectorPerformanceDataError(f"Sector index value is invalid for {token!r}.")
            values[token] = (index_value, percent_change)

        missing = set(requested) - set(values)
        if missing:
            raise SectorPerformanceDataError("Sector quote response is missing required sectors.")

        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        retrieved_at = retrieved_at.astimezone(timezone.utc)
        # Kotak's observed 1900 placeholder is intentionally not used as market time.
        return tuple(
            SectorPerformance(
                sector_name=name,
                index_value=values[identifier][0],
                percent_change=values[identifier][1],
                source=self.SOURCE,
                retrieved_at=retrieved_at,
            )
            for name, identifier in self.SECTORS
        )


def _finite_decimal(value: Any, field: str, identifier: str) -> Decimal:
    """Parse one finite Kotak numeric field without accepting booleans or blanks."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise SectorPerformanceDataError(f"Sector {field} is missing for {identifier!r}.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise SectorPerformanceDataError(
            f"Sector {field} is malformed for {identifier!r}."
        ) from None
    if not number.is_finite():
        raise SectorPerformanceDataError(f"Sector {field} is malformed for {identifier!r}.")
    return number


def _utc_now() -> datetime:
    """Return the current UTC retrieval time."""
    return datetime.now(timezone.utc)
