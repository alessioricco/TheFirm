"""Console progress for `firm paper run`: what each closed bar led to, and a
status line every few seconds (price, position, cash, equity, trades seen,
time to the next bar close), so a quiet hour never looks like a hang.

Display only: it reads the worker's outcomes and account and writes text. It
never decides, and nothing here goes to the ledger.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from firm.core.timeframes import bucket_start, tf_seconds
from firm.execution.portfolio import MarketAccount
from firm.execution.worker import Outcome


class _Worker(Protocol):
    outcomes: list[Outcome]

    def account(self) -> Awaitable[MarketAccount]: ...


def next_close(now: datetime, tf: str) -> datetime:
    """When the bar forming at `now` closes (canonical UTC grid)."""
    start = bucket_start(int(now.timestamp()), tf)
    return datetime.fromtimestamp(start + tf_seconds(tf), tz=UTC)


def describe_outcome(o: Outcome) -> str:
    target = "" if o.target is None else f" (target {o.target:.3f})"
    return f"bar {o.bar_close:%Y-%m-%d %H:%M} closed: {o.action}, {o.reason}{target}"


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _wait(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def status_line(now: datetime, tf: str, instrument: str, acc: MarketAccount, *, trades: int) -> str:
    rules = acc.rules[instrument]
    mark = acc.marks.get(instrument)
    cash = next((b.total for b in acc.balances if b.currency == rules.quote_currency), Decimal(0))
    held = next((p.quantity for p in acc.positions if p.instrument == instrument), Decimal(0))
    equity = cash + held * mark if mark is not None else None
    close = next_close(now, tf)
    q = rules.quote_currency
    return (
        f"status: {instrument} {_plain(mark) if mark is not None else '?'} {q}; "
        f"holding {_plain(held)} {rules.base_currency}; cash {cash:.2f} {q}; "
        f"equity {f'{equity:.2f}' if equity is not None else '?'} {q}; "
        f"{trades} trades received; next bar {close:%H:%M} UTC in {_wait(close - now)}"
    )


class Progress:
    """Prints new bar outcomes at once and the status line every `every_seconds`
    (0 turns the status line off)."""

    def __init__(
        self,
        worker: _Worker,
        instrument: str,
        tf: str,
        *,
        every_seconds: float,
        log: Callable[[str], None],
    ) -> None:
        self.worker, self.instrument, self.tf = worker, instrument, tf
        self.every = every_seconds
        self.log = log
        self.trades = 0
        self._seen = 0
        self._last: datetime | None = None

    def count_trade(self) -> None:
        self.trades += 1

    async def poll(self, now: datetime) -> None:
        for o in self.worker.outcomes[self._seen :]:
            self.log(f"{now:%Y-%m-%d %H:%M:%S}  {describe_outcome(o)}")
        self._seen = len(self.worker.outcomes)
        if self.every <= 0:
            return
        if self._last is None or (now - self._last).total_seconds() >= self.every:
            self._last = now
            acc = await self.worker.account()
            line = status_line(now, self.tf, self.instrument, acc, trades=self.trades)
            self.log(f"{now:%Y-%m-%d %H:%M:%S}  {line}")
