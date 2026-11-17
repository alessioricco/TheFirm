"""A small worker run for the invariant tests: a spy venue records every order."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from factories import verdict
from firm.core.config import load_risk
from firm.core.domain import OrderIntent, OrderUpdate, RiskAction, RiskDecision
from firm.core.events import CollectingSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.data.providers.base import NS, BarRow
from firm.execution.adapter import InstrumentRules, VenueMode
from firm.execution.portfolio import FxBook, Portfolio
from firm.execution.simulator import MarketTrade, PaperSimulator, SimConfig
from firm.execution.worker import Deployment, MarketWorker
from firm.risk.desk import RiskDesk

D = Decimal
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=D("0.1"),
    lot_size=D("0.0001"),
    min_quantity=D("0.0001"),
    min_notional=D("5"),
)


class SpyVenue(PaperSimulator):
    def __init__(self, **kw) -> None:
        super().__init__(VenueMode.PAPER, live_trading=False, **kw)
        self.received: list[OrderIntent] = []

    async def submit(self, intent: OrderIntent) -> OrderUpdate:
        self.received.append(intent)
        return await super().submit(intent)


class RejectAll:
    def decide(self, verdict, proposal, state) -> RiskDecision:
        return RiskDecision(
            decision_id="r-x",
            proposal_id=proposal.proposal_id,
            verdict_id=proposal.verdict_id,
            action=RiskAction.REJECT,
            reason="test: reject all",
            decided_at=state.as_of,
        )


class Alternating:
    name = "alt"
    default_params: ClassVar[dict] = {}

    def signals(self, bars, params):
        n = len(bars)
        return bars["close"].copy().mul(0).add(float((n + int(bars["close"].iloc[-1])) % 2))


def run_bars(*, reject_all: bool, n: int = 30):
    sink = CollectingSink()
    venue = SpyVenue(
        config=SimConfig(
            venue="kraken",
            rules={"BTC-USD": RULES},
            maker_bps=D(40),
            taker_bps=D(80),
            spread_bps=D(2),
            slippage_bps=D(5),
            participation=D(1),
            balances={"USD": D(6250)},
        )
    )
    fx = FxBook(max_age=timedelta(hours=1))
    fx.update("GBP", "USD", D("1.25"), T0)
    portfolio = Portfolio(capital_gbp=D(10_000), allocations_pct={"crypto": 50})
    profile = load_risk(Path(__file__).resolve().parents[1] / "config" / "risk" / "default.yaml")
    desk = RiskDesk(profile, sink)
    risk = RejectAll() if reject_all else desk
    hb = HeartbeatMonitor(timedelta(seconds=10))
    worker: MarketWorker

    async def state(now):
        return portfolio.risk_state(now, [await worker.account()], fx)

    worker = MarketWorker(
        Deployment(
            verdict=verdict(strategy_id="alt"),
            strategy_id="alt",
            params={},
            market="crypto",
            venue="kraken",
            instrument="BTC-USD",
            tf="1m",
            quote_currency="USD",
            lookback_bars=2,
            rebalance_threshold_pct=D(0),
            cost_buffer_bps=D(88),
        ),
        Alternating(),
        venue,
        risk,
        state,
        hb,
        sink=sink,
    )

    async def go() -> None:
        for i in range(n):
            start = T0 + timedelta(minutes=i)
            px = D(50_000 + i)
            worker.on_market(
                MarketTrade(
                    instrument="BTC-USD", time=start + timedelta(seconds=30), price=px, size=D(10)
                )
            )
            close = start + timedelta(minutes=1)
            worker.on_heartbeat(desk.heartbeat(close))
            o = int(start.timestamp()) * NS
            await worker.on_bar(
                BarRow(
                    open_time_ns=o,
                    close_time_ns=o + 60 * NS,
                    open=float(px),
                    high=float(px),
                    low=float(px),
                    close=float(px),
                    volume=10.0,
                )
            )

    asyncio.run(go())
    events = [(e[0].value, e[1], e[2], e[3]) for e in sink.events]
    return venue.received, events
