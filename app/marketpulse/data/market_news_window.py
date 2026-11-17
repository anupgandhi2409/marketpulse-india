"""Build the IST Morning news window from an explicit report timestamp.

Windows use calendar days only and do not adjust for weekends or market holidays.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MORNING_CUTOFF = time(18, 0)


class MarketNewsWindowPolicyError(ValueError):
    """Signal invalid report timestamps, cutoff settings, or empty windows."""


def build_market_news_window(
    generated_at: datetime,
    *,
    morning_cutoff: time = DEFAULT_MORNING_CUTOFF,
) -> tuple[datetime, datetime]:
    """Return the prior calendar day 18:00 IST to the supplied generation time."""
    _validate_cutoff("morning_cutoff", morning_cutoff)
    generation_ist = _to_ist(generated_at)
    try:
        start_date = generation_ist.date() - timedelta(days=1)
    except OverflowError:
        raise MarketNewsWindowPolicyError("The previous calendar day is out of range.") from None
    window_start = datetime.combine(start_date, morning_cutoff, tzinfo=IST)
    if window_start >= generation_ist:
        raise MarketNewsWindowPolicyError("The Morning window must precede report generation.")
    return window_start, generation_ist


def _validate_cutoff(name: str, value: object) -> None:
    """Require a naive local wall-clock time for an IST cutoff."""
    if not isinstance(value, time) or value.tzinfo is not None:
        raise MarketNewsWindowPolicyError(f"{name} must be a timezone-naive datetime.time.")


def _to_ist(value: object) -> datetime:
    """Validate an aware generation timestamp and convert its instant to IST."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MarketNewsWindowPolicyError("generated_at must be timezone-aware.")
    try:
        if value.utcoffset() is None:
            raise MarketNewsWindowPolicyError("generated_at must be timezone-aware.")
        return value.astimezone(IST)
    except (OverflowError, TypeError, ValueError):
        raise MarketNewsWindowPolicyError(
            "generated_at is not a valid timezone-aware timestamp."
        ) from None
