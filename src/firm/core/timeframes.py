"""Timeframe vocabulary and calendar arithmetic shared by every desk.

The vocabulary is fixed (ARCHITECTURE.md "Data store"): a timeframe string
outside it is an error, never a guess.
"""

from __future__ import annotations

from datetime import timedelta

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "4h": 14_400,
    "1d": 86_400,
    "1w": 604_800,
}

CONTINUOUS_CALENDAR = "24x7"
_EXCHANGE_SESSIONS_PER_YEAR = 252  # trading days; exchange intraday comes with milestone 6


def tf_seconds(tf: str) -> int:
    try:
        return TIMEFRAME_SECONDS[tf]
    except KeyError:
        raise ValueError(
            f"unknown timeframe {tf!r}; allowed: {sorted(TIMEFRAME_SECONDS)}"
        ) from None


def tf_delta(tf: str) -> timedelta:
    return timedelta(seconds=tf_seconds(tf))


def periods_per_year(calendar: str, tf: str) -> float:
    """Bars per year, used to annualise per-bar statistics."""
    seconds = tf_seconds(tf)
    if calendar == CONTINUOUS_CALENDAR:
        return 365.0 * 86_400 / seconds
    if tf == "1d":
        return float(_EXCHANGE_SESSIONS_PER_YEAR)
    if tf == "1w":
        return 52.0
    raise ValueError(f"periods_per_year: {calendar} {tf} not supported yet (milestone 6)")
