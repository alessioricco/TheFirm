"""Bars from live trades, on the canonical grid, emitted only once closed.

A bar for [t, t+tf) is emitted when time reaches t+tf: either a trade at or
after t+tf arrives, or `close_due(now)` is called with now >= t+tf (so a
quiet market still closes its bar). Late trades for an already-emitted bar
are counted and dropped, never rewritten into a bar that was already known.
Periods without trades produce no bar (gaps are visible, never invented).

Only bars watched from start to end are emitted. A stream joined mid-period
(or a snapshot of older trades) can't tell what traded before, so bars
starting before `started_ns` are dropped and counted as `incomplete`; after a
disconnect, `mark_gap(now)` does the same for every bar up to the gap. Missing
periods are filled later from the venue's REST history, not guessed here.
"""

from __future__ import annotations

from firm.core.timeframes import bucket_start, tf_seconds
from firm.data.providers.base import NS, BarRow, TradeRow


class BarBuilder:
    def __init__(self, tf: str = "1m", *, started_ns: int) -> None:
        self.tf = tf
        self.late_trades = 0
        self.incomplete = 0
        self._bar: BarRow | None = None
        self._emitted_until = 0  # ns: everything before this is final
        self._complete_from = self._next_boundary(started_ns)

    def _next_boundary(self, t_ns: int) -> int:
        """The first bucket start at or after t_ns: the first bar watched whole."""
        start = bucket_start(t_ns // NS, self.tf) * NS
        return start if start == t_ns else start + tf_seconds(self.tf) * NS

    def mark_gap(self, at_ns: int) -> None:
        """Trades may have been missed at `at_ns` (a disconnect): bars up to then are partial."""
        self._complete_from = max(self._complete_from, self._next_boundary(at_ns))

    def add(self, trade: TradeRow) -> list[BarRow]:
        """Add one trade; returns the complete bars this trade closed."""
        if trade["time_ns"] < self._emitted_until:
            self.late_trades += 1
            return []
        out = self.close_due(trade["time_ns"])
        p, q = trade["price"], trade["size"]
        if self._bar is None:
            start = bucket_start(trade["time_ns"] // NS, self.tf) * NS
            self._bar = BarRow(
                open_time_ns=start,
                close_time_ns=start + tf_seconds(self.tf) * NS,
                open=p,
                high=p,
                low=p,
                close=p,
                volume=q,
            )
        else:
            b = self._bar
            b["high"], b["low"] = max(b["high"], p), min(b["low"], p)
            b["close"], b["volume"] = p, b["volume"] + q
        return out

    def close_due(self, now_ns: int) -> list[BarRow]:
        """Emit the open bar if its period has ended by `now_ns` and it was watched whole."""
        if self._bar is None or now_ns < self._bar["close_time_ns"]:
            return []
        bar, self._bar = self._bar, None
        self._emitted_until = bar["close_time_ns"]
        if bar["open_time_ns"] < self._complete_from:
            self.incomplete += 1
            return []
        return [bar]
