"""One market's asyncio loop: live trades -> bars -> the market worker.

`on_trade` is the feed's callback; it only queues. `run` drains the queue
and ticks once a second:

- a trade on the FX pair updates the FX book;
- a trade on the instrument first closes any bar it ends (the worker decides
  on that bar), THEN reaches the paper venue, so an order placed at a bar's
  close can fill on the next trade but never on one it already saw;
- each tick: a risk heartbeat (in-process risk; over the bus they arrive on
  their own), the heartbeat watch (lost / back, recorded and alerted), the
  feed's staleness, and bars closed by time (quiet markets still close
  their bars).

Bars come from one of two places: the trade-built `BarBuilder` (fully
watched periods only), or a `bar_source`, polled each tick, that returns new
closed bars from the venue's own history (what `firm paper run` uses: the
same bars the verifier judged the strategy on, and no partial first bar).

An exception stops this market's loop only (golden rule 10): it is recorded
and re-raised to whoever runs the market, and other markets keep going.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from firm.core.domain import LedgerEventType
from firm.core.events import NULL_SINK, EventSink
from firm.core.messages.subjects import RiskHeartbeat
from firm.data.providers.base import NS, BarRow, TradeRow
from firm.data.streams.bars import BarBuilder
from firm.execution.portfolio import FxBook
from firm.execution.simulator import MarketTrade
from firm.execution.worker import MarketWorker


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class FxPair:
    symbol: str  # as the stream spells it, e.g. GBP/USD
    base: str
    quote: str


@dataclass
class MarketRunner:
    worker: MarketWorker
    builder: BarBuilder | None
    symbol: str  # the instrument as the stream spells it
    fx: FxBook
    fx_pair: FxPair | None  # an FX pair on the same stream, if any
    # in-process risk: a heartbeat each tick; None when heartbeats arrive on the bus
    risk_heartbeat: Callable[[datetime], RiskHeartbeat] | None
    stale: Callable[[datetime], dict[str, bool]]  # the feed's check()
    bar_source: Callable[[datetime], Awaitable[list[BarRow]]] | None = None
    sink: EventSink = NULL_SINK
    clock: Callable[[], datetime] = _now
    tick_seconds: float = 1.0
    _queue: asyncio.Queue[tuple[str, TradeRow]] = field(default_factory=asyncio.Queue)
    _hb_alive: bool | None = None  # None until risk was heard (or given up on)
    _started: datetime | None = None

    def on_trade(self, symbol: str, trade: TradeRow) -> None:
        self._queue.put_nowait((symbol, trade))

    def on_gap(self, at: datetime) -> None:
        if self.builder is not None:
            self.builder.mark_gap(int(at.timestamp() * NS))

    async def handle(self, symbol: str, trade: TradeRow) -> None:
        price = Decimal(str(trade["price"]))
        at = datetime.fromtimestamp(trade["time_ns"] / NS, tz=UTC)
        if self.fx_pair is not None and symbol == self.fx_pair.symbol:
            self.fx.update(self.fx_pair.base, self.fx_pair.quote, price, at)
            return
        if symbol != self.symbol:
            return
        if self.builder is not None:
            for bar in self.builder.add(trade):
                await self.worker.on_bar(bar)
        self.worker.on_market(
            MarketTrade(
                instrument=self.worker.d.instrument,
                time=at,
                price=price,
                size=Decimal(str(trade["size"])),
            )
        )

    def watch_heartbeat(self, now: datetime) -> None:
        """Record when risk's heartbeat is lost and when it comes back (alerted)."""
        if self._started is None:
            self._started = now
        alive = self.worker.heartbeat.alive(now)
        market = self.worker.d.market
        if alive and self._hb_alive is False:
            self.sink.emit(
                LedgerEventType.MARKET_STATUS,
                f"{market}: risk heartbeat back, orders allowed again",
                {"kind": "heartbeat_restored", "market": market, "at": now.isoformat()},
            )
        lost = not alive and (
            self._hb_alive is True
            or (self._hb_alive is None and now - self._started >= self.worker.heartbeat.timeout)
        )
        if lost:
            self.sink.emit(
                LedgerEventType.MARKET_STATUS,
                f"{market}: risk heartbeat lost for over "
                f"{self.worker.heartbeat.timeout.total_seconds():g}s: no new orders",
                {"kind": "heartbeat_lost", "market": market, "at": now.isoformat()},
            )
            self._hb_alive = False
        elif alive:
            self._hb_alive = True

    async def tick(self) -> None:
        now = self.clock()
        if self.risk_heartbeat is not None:
            self.worker.on_heartbeat(self.risk_heartbeat(now))
        self.watch_heartbeat(now)
        self.worker.on_feed_status(self.stale(now).get(self.symbol, True))
        if self.builder is not None:
            for bar in self.builder.close_due(int(now.timestamp() * NS)):
                await self.worker.on_bar(bar)
        if self.bar_source is not None:
            new = await self.bar_source(now)
            if new:  # after downtime, decide only on the latest bar; older ones are history
                self.worker.warm_up(new[:-1])
                await self.worker.on_bar(new[-1])

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    symbol, trade = await asyncio.wait_for(
                        self._queue.get(), timeout=self.tick_seconds
                    )
                except TimeoutError:
                    await self.tick()
                    continue
                await self.handle(symbol, trade)
                if self._queue.empty():
                    await self.tick()
        except Exception as exc:
            self.sink.emit(
                LedgerEventType.MARKET_STATUS,
                f"market {self.worker.d.market} stopped: {exc!r}"[:300],
                {
                    "market": self.worker.d.market,
                    "error": repr(exc)[:500],
                    "at": self.clock().isoformat(),
                },
            )
            raise  # nothing more runs on this market: no final tick, no order after a crash
