"""The market worker: closed bar -> target -> proposal -> risk -> intent -> paper venue.

Real pieces wired together in-process: the paper simulator, the risk desk
with the default risk profile, a portfolio valued with a live-style FX book.
Equity: £10,000 paper capital, 50% allocated to crypto (= $6,250 at 1.25).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest

from factories import verdict
from firm.core.config import load_risk
from firm.core.domain import LedgerEventType, OrderStatus, RiskAction, VerdictOutcome
from firm.core.events import CollectingSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.core.messages.subjects import HaltScope, OwnerCommand, OwnerCommandKind
from firm.data.providers.base import NS, BarRow
from firm.execution.adapter import InstrumentRules, VenueMode
from firm.execution.portfolio import FxBook, Portfolio
from firm.execution.simulator import MarketTrade, PaperSimulator, SimConfig
from firm.execution.worker import Deployment, MarketWorker
from firm.risk.desk import RiskDesk

D = Decimal
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
PROFILE = load_risk(Path(__file__).resolve().parents[2] / "config" / "risk" / "default.yaml")
RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=D("0.1"),
    lot_size=D("0.0001"),
    min_quantity=D("0.0001"),
    min_notional=D("5"),
)


class Rising:
    """Long when the last close is above the window's first close."""

    name = "rising"
    default_params: ClassVar[dict[str, int]] = {}

    def signals(self, bars: pd.DataFrame, params) -> pd.Series:
        up = (bars["close"] > bars["close"].iloc[0]).astype(float)
        up.iloc[0] = float("nan")
        return up


def bar(i: int, close: float) -> BarRow:
    start = int((T0 + timedelta(minutes=i)).timestamp()) * NS
    return BarRow(
        open_time_ns=start,
        close_time_ns=start + 60 * NS,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10.0,
    )


USD_PER_GBP = D("1.25")


class Rig:
    def __init__(self, *, fx_rate: Decimal | None = USD_PER_GBP, **deploy) -> None:
        self.sink = CollectingSink()
        self.sim = PaperSimulator(
            VenueMode.PAPER,
            live_trading=False,
            config=SimConfig(
                venue="kraken",
                rules={"BTC-USD": RULES},
                maker_bps=D(40),
                taker_bps=D(80),
                spread_bps=D(2),
                slippage_bps=D(5),
                participation=D(1),
                balances={"USD": D(6250)},
            ),
        )
        self.fx = FxBook(max_age=timedelta(hours=1))
        if fx_rate is not None:
            self.fx.update("GBP", "USD", fx_rate, T0)
        self.portfolio = Portfolio(capital_gbp=D(10_000), allocations_pct={"crypto": 50})
        self.risk = RiskDesk(PROFILE, self.sink)
        self.hb = HeartbeatMonitor(timedelta(seconds=10))
        fields = dict(
            verdict=verdict(strategy_id="rising"),
            strategy_id="rising",
            params={},
            market="crypto",
            venue="kraken",
            instrument="BTC-USD",
            tf="1m",
            quote_currency="USD",
            lookback_bars=3,
            rebalance_threshold_pct=D(2),
            cost_buffer_bps=D(88),
        )
        fields.update(deploy)
        self.worker = MarketWorker(
            Deployment(**fields), Rising(), self.sim, self.risk, self.state, self.hb, sink=self.sink
        )

    async def state(self, now: datetime):
        return self.portfolio.risk_state(now, [await self.worker.account()], self.fx)

    def bar(self, i: int, close: float, *, beat: bool = True):
        b = bar(i, close)
        close_at = datetime.fromtimestamp(b["close_time_ns"] / NS, tz=UTC)
        # as live: the bar's trades reach the paper venue before the bar closes
        self.worker.on_market(
            MarketTrade(
                instrument="BTC-USD",
                time=close_at - timedelta(seconds=1),
                price=D(str(close)),
                size=D("0.001"),
            )
        )
        if beat:
            self.worker.on_heartbeat(self.risk.heartbeat(close_at))
        return asyncio.run(self.worker.on_bar(b))

    def trade(self, i: int, price: str, size: str = "100") -> None:
        t = T0 + timedelta(minutes=i, seconds=2)
        self.worker.on_market(
            MarketTrade(instrument="BTC-USD", time=t, price=D(price), size=D(size))
        )

    def types(self) -> list[LedgerEventType]:
        return [e[0] for e in self.sink.events]


def test_full_path_from_bar_to_fill_with_every_step_in_the_ledger() -> None:
    rig = Rig()
    assert rig.bar(0, 50_000).action == "skip"  # warming up
    assert rig.bar(1, 50_000).action == "skip"
    out = rig.bar(2, 50_100)  # target 1: buy, capped by risk
    assert out.action == "submitted" and out.target == 1.0
    assert out.decision.action is RiskAction.RESIZE  # one order <= 10% of £10k
    assert out.intent.quantity == D("0.0249")  # £1,000 at $50,100 ÷ 1.25, lot-rounded down
    assert (out.intent.verdict_id, out.intent.risk_decision_id) == ("v-1", out.decision.decision_id)
    assert out.update.status is OrderStatus.ACCEPTED
    rig.trade(3, "50100")  # the next trade fills it, never the bar that decided it
    (fill,) = rig.sim.fills()
    assert (fill.verdict_id, fill.risk_decision_id) == ("v-1", out.decision.decision_id)
    assert rig.types() == [
        LedgerEventType.ORDER_PROPOSAL,
        LedgerEventType.RISK_DECISION,
        LedgerEventType.ORDER_INTENT,
        LedgerEventType.ORDER_UPDATE,
        LedgerEventType.FILL,
        LedgerEventType.ORDER_UPDATE,
    ]


def test_going_flat_sells_everything_held() -> None:
    rig = Rig()
    for i, px in enumerate((50_000, 50_000, 50_100)):
        rig.bar(i, px)
    rig.trade(3, "50100")
    out = rig.bar(3, 49_000)  # below the window's first close: target 0
    assert out.action == "submitted" and out.intent.side.value == "sell"
    assert out.intent.quantity == D("0.0249")


@pytest.mark.parametrize(
    ("setup", "why"),
    [
        (lambda r: r.worker.on_feed_status(True), "feed stale"),
        (lambda r: None, "heartbeat"),
        (lambda r: r.fx._rates.clear(), "FX"),
    ],
)
def test_halts_stop_new_orders_before_anything_is_proposed(setup, why) -> None:
    rig = Rig()
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    setup(rig)
    out = rig.bar(2, 50_100, beat=why != "heartbeat")
    assert out.action == "skip" and why in out.reason
    assert rig.types() == [] and rig.sim.fills() == []


def test_risk_rejection_sends_nothing_to_the_venue() -> None:
    rig = Rig()
    rig.risk.command(
        OwnerCommand(
            command_id="k1",
            kind=OwnerCommandKind.KILL,
            scope=HaltScope.GLOBAL,
            reason="test",
            issued_at=T0,
        )
    )
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    out = rig.bar(2, 50_100)
    assert out.action == "rejected" and "kill switch" in out.reason
    assert asyncio.run(rig.sim.open_orders()) == () and LedgerEventType.ORDER_INTENT not in (
        rig.types()
    )


def test_ids_come_from_the_bar_so_a_replay_cannot_double_order() -> None:
    a, b = Rig(), Rig()
    for rig in (a, b):
        for i, px in enumerate((50_000, 50_000, 50_100)):
            rig.bar(i, px)
    assert (
        a.worker.outcomes[-1].intent.client_order_id == b.worker.outcomes[-1].intent.client_order_id
    )
    again = asyncio.run(a.sim.submit(a.worker.outcomes[-1].intent))
    assert again == a.worker.outcomes[-1].update  # the venue sees the same order, once


def test_one_working_order_at_a_time() -> None:
    rig = Rig()
    for i, px in enumerate((50_000, 50_000, 50_100)):
        rig.bar(i, px)
    out = rig.bar(3, 50_200)  # no trade yet: the first order hasn't filled
    assert out.action == "skip" and "working" in out.reason


def test_a_deployment_needs_a_passing_verdict_for_its_strategy() -> None:
    with pytest.raises(ValueError, match="unverified"):
        Rig(
            verdict=verdict(
                strategy_id="rising", outcome=VerdictOutcome.UNVERIFIED, checks=_not_run()
            )
        )
    with pytest.raises(ValueError, match="another strategy"):
        Rig(verdict=verdict(strategy_id="other"))


def _not_run():
    from firm.core.domain import CheckResult, CheckStatus

    return (CheckResult(name="oos", status=CheckStatus.NOT_RUN, detail="closed"),)


def test_the_worker_sees_what_the_backtest_sees() -> None:
    """Same bars, same targets: the worker's decision at each close equals the
    strategy's signal over the full series (window long enough for the MAs)."""
    from firm.verify.strategy import load_strategy
    from verify_helpers import EXAMPLES

    ma = load_strategy(EXAMPLES / "ma_crossover.py", "MovingAverageCrossover").strategy
    params = {"fast": 3, "slow": 8}
    closes = [100 + ((i * 7) % 11) - i * 0.3 + (i // 15) * 4 for i in range(60)]
    rig = Rig(
        verdict=verdict(strategy_id="ma_crossover"),
        strategy_id="ma_crossover",
        params=params,
        lookback_bars=20,
    )
    rig.worker.strategy = ma
    rig.worker.on_feed_status(True)  # targets only: no orders needed for this check
    full = pd.DataFrame({"close": closes, "open": closes, "high": closes, "low": closes})
    expected = ma.signals(full, params).fillna(0.0).tolist()
    seen = []
    for i, c in enumerate(closes):
        rig.bar(i, c)
        if i >= 19:
            rig.worker.on_feed_status(False)
            seen.append((i, rig.worker._target()))
            rig.worker.on_feed_status(True)
    assert all(t == expected[i] for i, t in seen)


def test_warm_up_fills_the_lookback_and_replayed_bars_never_decide_twice() -> None:
    rig = Rig()
    assert rig.worker.warm_up([bar(1, 50_000), bar(0, 50_000), bar(1, 50_000)]) == 2
    out = rig.bar(2, 50_100)  # history loaded: decides on its first live bar
    assert out.action == "submitted"
    again = rig.bar(2, 50_100)
    assert again.action == "skip" and again.reason == "bar already seen"
