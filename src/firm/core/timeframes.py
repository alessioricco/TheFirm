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


# --------------------------------------------------------------------------- alignment
# The firm's canonical grid: intraday and daily bars start on multiples of their
# length from the Unix epoch (so days run 00:00-24:00 UTC); weeks start Monday
# 00:00 UTC. Providers may differ (Kraken's weeks start Thursday, share data
# follows exchange sessions); each declares its alignment and the normaliser
# checks it, so differently aligned bars are never mixed silently.

WEEK_ORIGIN_S = {"monday": 4 * 86_400, "thursday": 0}  # 1970-01-01 was a Thursday
CANONICAL_WEEK_START = "monday"


def alignment_for(tf: str, *, day_boundary: str, week_start: str | None) -> str:
    """`utc` (epoch multiples), `week:<day>`, or `session` (exchange-session based)."""
    tf_seconds(tf)
    if day_boundary == "session":
        return "session"
    if tf == "1w":
        if week_start is None:
            raise ValueError("weekly bars need a week_start")
        return f"week:{week_start}"
    return "utc"


def bucket_start(t_s: int, tf: str, *, week_start: str = CANONICAL_WEEK_START) -> int:
    """Start (unix s) of the `tf` bar containing `t_s` on the canonical grid."""
    step = tf_seconds(tf)
    origin = WEEK_ORIGIN_S[week_start] if tf == "1w" else 0
    return t_s - (t_s - origin) % step


def is_aligned(t_s: int, tf: str, alignment: str) -> bool:
    if alignment == "session":
        return True
    if alignment == "utc":
        return t_s % tf_seconds(tf) == 0
    if alignment.startswith("week:"):
        return bucket_start(t_s, "1w", week_start=alignment.split(":", 1)[1]) == t_s
    raise ValueError(f"unknown alignment {alignment!r}")
