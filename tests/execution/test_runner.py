"""The market loop: trade ordering, FX, ticks, and a crash halting one market only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_worker import T0, Rig

from firm.core.domain import LedgerEventType
from firm.data.providers.base import NS, TradeRow
from firm.data.streams.bars import BarBuilder
from firm.execution.runner import FxPair, MarketRunner

D = Decimal


def tr(sec: float, price: float, tid: str, size: float = 1.0) -> TradeRow:
    return TradeRow(
        time_ns=int((T0.timestamp() + sec) * NS),
        trade_id=tid,
        price=price,
        size=size,
        taker_side="buy",
    )


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def runner(rig: Rig, clock: Clock, stale: bool = False) -> MarketRunner:
    rig.fx._pairs.clear()
    return MarketRunner(
        worker=rig.worker,
        builder=BarBuilder("1m", started_ns=int(T0.timestamp()) * NS),
        symbol="BTC/USD",
        fx=rig.fx,
        fx_pair=FxPair("GBP/USD", "GBP", "USD"),
        risk_heartbeat=rig.risk.heartbeat,
        stale=lambda now: {"BTC/USD": stale},
        sink=rig.sink,
        clock=clock,
    )


def test_fx_trades_feed_the_fx_book_and_bars_decide_before_the_venue_sees_the_trade() -> None:
    rig, clock = Rig(fx_rate=None), Clock()
    r = runner(rig, clock)

    async def go() -> None:
        await r.handle("GBP/USD", tr(0, 1.25, "fx1"))
        for minute, px in enumerate((50_000, 50_000, 50_100)):
            clock.now = T0 + timedelta(minutes=minute, seconds=30)
            await r.tick()  # heartbeat
            await r.handle("BTC/USD", tr(minute * 60 + 30, px, f"t{minute}", 0.001))
        clock.now = T0 + timedelta(minutes=3, seconds=1)
        await r.tick()
        await r.handle("BTC/USD", tr(181, 50_100, "t3"))  # closes minute 2 -> buy, then fills

    asyncio.run(go())
    assert rig.fx.to_gbp("USD", clock.now) == D(1) / D("1.25")
    (out,) = [o for o in rig.worker.outcomes if o.action == "submitted"]
    assert out.bar_close == T0 + timedelta(minutes=3)
    (fill,) = rig.sim.fills()
    assert fill.filled_at == T0 + timedelta(minutes=3, seconds=1)  # the trade after the close


def test_quiet_markets_close_bars_on_the_tick_and_stale_feeds_block_orders() -> None:
    rig, clock = Rig(), Clock()
    r = runner(rig, clock, stale=True)

    async def go() -> None:
        await r.handle("GBP/USD", tr(0, 1.25, "fx1"))
        for minute in range(3):
            await r.handle("BTC/USD", tr(minute * 60 + 10, 50_000 + minute * 100, f"t{minute}"))
            clock.now = T0 + timedelta(minutes=minute + 1)
            await r.tick()

    asyncio.run(go())
    assert [o.action for o in rig.worker.outcomes] == ["skip"] * 3
    assert "feed stale" in rig.worker.outcomes[-1].reason


def test_a_crash_stops_this_market_and_is_recorded() -> None:
    rig, clock = Rig(), Clock()
    r = runner(rig, clock)

    def boom(now):
        raise RuntimeError("risk link broken")

    r.risk_heartbeat = boom
    r.tick_seconds = 0.01
    with pytest.raises(RuntimeError, match="risk link broken"):
        asyncio.run(r.run(asyncio.Event()))
    (event,) = [e for e in rig.sink.events if e[0] is LedgerEventType.MARKET_STATUS]
    assert "stopped" in event[1] and rig.sim.fills() == []


def test_the_clock_is_utc() -> None:
    assert MarketRunner.__dataclass_fields__["clock"].default().tzinfo is UTC


def test_the_runner_decides_only_the_latest_bar_after_downtime() -> None:
    from test_worker import bar

    rig = Rig()

    async def source(now):
        return [bar(0, 50_000), bar(1, 50_000), bar(2, 50_100)]

    runner = MarketRunner(
        worker=rig.worker,
        builder=None,
        symbol="BTC/USD",
        fx=rig.fx,
        fx_pair=FxPair("GBP/USD", "GBP", "USD"),
        risk_heartbeat=rig.risk.heartbeat,
        stale=lambda now: {"BTC/USD": False},
        bar_source=source,
        clock=lambda: T0 + timedelta(minutes=3),
    )
    rig.trade(2, "50100")  # the paper venue's last price
    asyncio.run(runner.tick())
    assert [o.action for o in rig.worker.outcomes] == ["submitted"]  # bars 0, 1: history only
