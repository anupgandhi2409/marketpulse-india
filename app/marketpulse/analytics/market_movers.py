from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class StockChange:
    """Hold a symbol and its validated daily percentage change."""

    symbol: str
    percent_change: Decimal


@dataclass(frozen=True)
class MarketMovers:
    """Return ranked movers with explicit coverage for the target universe."""

    gainers: tuple[StockChange, ...]
    losers: tuple[StockChange, ...]
    observed_count: int
    universe_size: int

    @property
    def is_complete(self) -> bool:
        """Indicate whether every member of the configured universe was ranked."""
        return self.observed_count == self.universe_size


def rank_market_movers(
    changes: Iterable[StockChange | tuple[str, Decimal | int | float | str | None]],
    universe_size: int = 50,
    limit: int = 10,
) -> MarketMovers:
    """Rank validated daily percentage changes and mark incomplete universe coverage."""
    if universe_size <= 0:
        raise ValueError("universe_size must be positive.")
    if limit <= 0:
        raise ValueError("limit must be positive.")

    valid: dict[str, StockChange] = {}
    for item in changes:
        if isinstance(item, StockChange):
            symbol, raw_change = item.symbol, item.percent_change
        else:
            try:
                symbol, raw_change = item
            except (TypeError, ValueError):
                continue
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        if raw_change is None:
            continue
        try:
            change = Decimal(str(raw_change))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not change.is_finite() or symbol in valid:
            continue
        valid[symbol] = StockChange(symbol, change)

    by_gain = sorted(valid.values(), key=lambda row: (-row.percent_change, row.symbol))
    by_loss = sorted(valid.values(), key=lambda row: (row.percent_change, row.symbol))
    return MarketMovers(
        gainers=tuple(by_gain[:limit]),
        losers=tuple(by_loss[:limit]),
        observed_count=len(valid),
        universe_size=universe_size,
    )
