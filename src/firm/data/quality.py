"""Implausible bars: found point-in-time, quarantined, never silently trusted.

A venue's own candles can be corrupt (a bar opening at ~0, a low of ~0).
Raw captures keep them exactly as received; this module decides which bars
are too implausible to judge a strategy on, under a versioned rule from
`config/data/quality.yaml`:

    a bar is implausible when any of its prices is more than `max_price_ratio`
    times above or below the last ACCEPTED close.

Only the past is used (the previous accepted close), so whether a bar is
quarantined never depends on data from after it. The first bar of a series
is checked against its own close. A quarantined bar's close is not used as a
reference, so one bad bar can't make the next good one look bad.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from firm.core.config import DataQualityConfig

PRICES = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Implausible:
    open_time_ns: int
    reason: str

    def as_dict(self) -> dict[str, str]:
        when = datetime.fromtimestamp(self.open_time_ns / 1e9, tz=UTC).isoformat()
        return {"open_time": when, "reason": self.reason}


def implausible_bars(
    bars: Iterable[Mapping[str, object]], rule: DataQualityConfig, *, time_key: str = "open_time_ns"
) -> list[Implausible]:
    """Bars breaking the rule, oldest first. `bars` must be in time order."""
    out: list[Implausible] = []
    ref: float | None = None
    r = rule.max_price_ratio
    for bar in bars:
        prices = {k: float(bar[k]) for k in PRICES}  # type: ignore[arg-type]
        base = ref if ref is not None else prices["close"]
        bad = []
        for k, v in prices.items():
            if v <= 0 or base <= 0:
                bad.append(f"{k} {v:g} is not a positive price")
            elif max(v / base, base / v) > r:
                bad.append(f"{k} {v:g} is {max(v / base, base / v):.4g}x the reference {base:g}")
        if bad:
            t = bar[time_key]
            ns = int(t.value) if hasattr(t, "value") else int(t)  # type: ignore[arg-type,call-overload]
            out.append(Implausible(ns, "; ".join(bad) + f" (rule {rule.rule_id})"))
        else:
            ref = prices["close"]
    return out
