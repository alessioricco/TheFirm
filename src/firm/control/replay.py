"""Replay history through the real paper-trading path, and compare with the backtest.

The verifier judges a strategy with a vectorised backtest (decide at bar i's
close, fill at bar i+1's open, a flat cost per unit traded, always fully in
or out). Paper trading uses a different path: the market worker, the risk
desk (with its caps), lot rounding and the paper simulator's fills. Both must
make the same decisions on the same bars, or paper results can't confirm a
verdict. This module feeds closed bars from a dataset version through that
path in minutes, in the backtest's order:

    for each bar: the simulator sees it (fills the previous order at its open),
                  then the worker decides at its close.

and checks it against `run_backtest` on the same bars:

- **signals**: the worker's target at every bar equals the backtest's
  (the worker computes it on its lookback window only, so a mismatch means
  the strategy needs more history than the lookback, or isn't causal);
- **trades**: every change of the backtest's position has an order at the
  same bar, or a recorded reason why not (risk caps, minimum order);
- **returns and exposure**: side by side. Risk caps make the paper account
  hold less than the backtest's 100%, so returns differ by design; the report
  shows by how much.

Nothing here touches the firm's ledger: events go to the sink the caller
gives (a scratch ledger for `firm paper replay`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from firm.control.paper import OPENED, venue_rules
from firm.core.config import FeeSchedule, MarketProfile, RiskProfile, load_config, load_fee_schedule
from firm.core.domain import Fill, LedgerEventType, Side
from firm.core.events import NULL_SINK, EventSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.data.dataset import capture_methods, open_dataset
from firm.data.fetch import Transport, http_get
from firm.data.providers.base import NS, BarRow
from firm.data.reference import latest_instruments
from firm.execution.adapter import InstrumentRules, VenueMode
from firm.execution.deploy import DeploymentRefused, verdict_for_deployment
from firm.execution.portfolio import FxBook, Portfolio, starting_cash
from firm.execution.simulator import MarketBar, PaperSimulator, SimConfig
from firm.execution.worker import Deployment, MarketWorker, Outcome, SignalSource
from firm.ledger.sink import LedgerSink, ledger_path
from firm.ledger.store import Ledger
from firm.risk.desk import RiskDesk
from firm.verify.backtest import run_backtest
from firm.verify.runner import FAR_FUTURE
from firm.verify.strategy import load_strategy
from firm.verify.verifier import cost_bps

D = Decimal


@dataclass(frozen=True)
class BarRecord:
    """One decided bar: what the worker did, next to what the backtest did."""

    close_time: datetime
    action: str  # the worker's: skip | rejected | submitted
    reason: str
    target: float | None  # the worker's target (None if it never got that far)
    backtest_target: float
    equity: Decimal  # paper account, quote currency, at this close
    exposure: float  # position value / equity at this close


@dataclass
class ReplayResult:
    start_cash: Decimal
    quote: str
    bars: list[BarRecord] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    backtest_return: float = 0.0
    backtest_exposure: float = 0.0

    # ------------------------------------------------------------------ checks

    @property
    def signal_mismatches(self) -> list[BarRecord]:
        return [
            b
            for b in self.bars
            if b.target is not None and abs(b.target - b.backtest_target) > 1e-9
        ]

    def trade_bars(self) -> list[int]:
        """Indexes of the bars where the backtest's position changes."""
        out, prev = [], 0.0
        for i, b in enumerate(self.bars):
            if b.backtest_target != prev:
                out.append(i)
            prev = b.backtest_target
        return out

    @property
    def missed(self) -> list[BarRecord]:
        """Backtest trades with no paper order at that bar."""
        return [self.bars[i] for i in self.trade_bars() if self.bars[i].action != "submitted"]

    @property
    def extra(self) -> list[BarRecord]:
        """Paper orders where the backtest didn't trade (e.g. topping up after a cap)."""
        changes = set(self.trade_bars())
        return [b for i, b in enumerate(self.bars) if b.action == "submitted" and i not in changes]

    @property
    def paper_return(self) -> float:
        if not self.bars:
            return 0.0
        return float(self.bars[-1].equity / self.start_cash - 1)

    @property
    def paper_exposure(self) -> float:
        return float(np.mean([b.exposure for b in self.bars])) if self.bars else 0.0

    def fill_slippage_bps(self, opens: dict[datetime, float]) -> list[float]:
        """Each fill's price against the open the backtest fills at (+ = worse for us)."""
        out = []
        for f in self.fills:
            ref = opens.get(f.filled_at)
            if ref:
                sign = 1 if f.side is Side.BUY else -1
                out.append(sign * (float(f.price) / ref - 1) * 1e4)
        return out

    def summary(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for b in self.missed:
            reasons[b.reason] = reasons.get(b.reason, 0) + 1
        return {
            "bars_decided": len(self.bars),
            "signal_mismatches": len(self.signal_mismatches),
            "backtest_trades": len(self.trade_bars()),
            "paper_orders": sum(b.action == "submitted" for b in self.bars),
            "fills": len(self.fills),
            "missed": len(self.missed),
            "missed_reasons": reasons,
            "extra_orders": len(self.extra),
            "paper_return": self.paper_return,
            "backtest_return": self.backtest_return,
            "paper_exposure": self.paper_exposure,
            "backtest_exposure": self.backtest_exposure,
        }


def _row(r: Any) -> BarRow:
    return BarRow(
        open_time_ns=int(r.open_time.value),
        close_time_ns=int(r.close_time.value),
        open=float(r.open),
        high=float(r.high),
        low=float(r.low),
        close=float(r.close),
        volume=float(r.volume),
    )


def _market_bar(instrument: str, b: BarRow) -> MarketBar:
    return MarketBar(
        instrument=instrument,
        open_time=datetime.fromtimestamp(b["open_time_ns"] / NS, tz=UTC),
        close_time=datetime.fromtimestamp(b["close_time_ns"] / NS, tz=UTC),
        open=D(str(b["open"])),
        high=D(str(b["high"])),
        low=D(str(b["low"])),
        close=D(str(b["close"])),
        volume=D(str(b["volume"])),
    )


async def replay(
    bars: pd.DataFrame,
    *,
    deployment: Deployment,
    strategy: SignalSource,
    profile: MarketProfile,
    fees: FeeSchedule,
    risk: RiskProfile,
    rules: InstrumentRules,
    gbp_per_quote: Decimal,
    decide_from: int,
    cost_bps: float,
    sink: EventSink = NULL_SINK,
) -> ReplayResult:
    """Decide bars `decide_from..` of `bars` (the ones before are history only).

    `bars` is a dataset's closed bars (`DataSource.bars` columns), oldest first.
    `cost_bps` is the verifier's cost per unit traded, for the backtest side.
    """
    if not deployment.lookback_bars <= decide_from < len(bars):
        raise ValueError(
            f"need {deployment.lookback_bars} bars of history before the first decided bar "
            f"and at least one to decide; got decide_from={decide_from} of {len(bars)}"
        )
    market, quote = deployment.market, deployment.quote_currency
    allocation = risk.markets[market].paper_allocation_pct
    cash = starting_cash(risk.paper_capital_gbp, allocation, gbp_per_quote)
    sim = PaperSimulator(
        VenueMode.PAPER,
        live_trading=False,
        config=SimConfig(
            venue=deployment.venue,
            rules={deployment.instrument: rules},
            maker_bps=D(str(fees.maker_bps)),
            taker_bps=D(str(fees.taker_bps)),
            spread_bps=D(str(profile.costs.spread_bps)),
            slippage_bps=D(str(profile.costs.slippage_bps)),
            participation=profile.execution.participation,
            balances={quote: cash},
        ),
    )
    # Size buys exactly as this simulator reserves for them (as `firm paper run` does).
    deployment = replace(deployment, cost_buffer_bps=sim.taker_buffer_bps())
    desk = RiskDesk(risk, sink)
    portfolio = Portfolio(capital_gbp=risk.paper_capital_gbp, allocations_pct={market: allocation})
    fx = FxBook(max_age=timedelta(days=36500))  # one fixed rate for the whole replay
    worker: MarketWorker

    async def risk_state(now: datetime):  # type: ignore[no-untyped-def]
        return portfolio.risk_state(now, [await worker.account()], fx)

    worker = MarketWorker(
        deployment,
        strategy,
        sim,
        desk,
        risk_state,
        HeartbeatMonitor(timedelta(seconds=profile.execution.heartbeat_timeout_seconds)),
        sink=sink,
    )

    rows = [_row(r) for r in bars.itertuples(index=False)]
    signals = strategy.signals(bars.copy(), deployment.params)
    masked = signals.copy()
    masked.iloc[:decide_from] = 0.0  # the paper account starts flat at decide_from
    bt = run_backtest(bars, masked, cost_bps=cost_bps)

    result = ReplayResult(start_cash=cash, quote=quote)
    for i, bar in enumerate(rows):
        fills = worker.on_market(_market_bar(deployment.instrument, bar))
        if i < decide_from:
            worker.warm_up([bar])
            continue
        result.fills += [f for f in fills if isinstance(f, Fill)]
        at = datetime.fromtimestamp(bar["close_time_ns"] / NS, tz=UTC)
        fx.update(quote, "GBP", gbp_per_quote, at)
        worker.on_heartbeat(desk.heartbeat(at))
        outcome: Outcome = await worker.on_bar(bar)
        held = next(
            (p.quantity for p in await sim.positions() if p.instrument == deployment.instrument),
            D(0),
        )
        money = next((b.total for b in await sim.balances() if b.currency == quote), D(0))
        value = held * D(str(bar["close"]))
        equity = money + value
        result.bars.append(
            BarRecord(
                close_time=at,
                action=outcome.action,
                reason=outcome.reason,
                target=outcome.target,
                backtest_target=float(bt.targets[i]),
                equity=equity,
                exposure=float(value / equity) if equity else 0.0,
            )
        )
    window = bt.net[decide_from + 1 :]
    result.backtest_return = float(np.prod(1.0 + window) - 1.0)
    result.backtest_exposure = float(np.mean(bt.targets[decide_from:]))
    return result


# --------------------------------------------------------------------------- `firm paper replay`


def gbp_per_quote_from_ledger(state_root: Path, market: str, venue: str) -> Decimal | None:
    """The FX rate the live paper account opened at, if there is one."""
    path = ledger_path(state_root)
    if not path.is_file():
        return None
    rate = None
    for e in Ledger(path).read():
        p = e.payload
        if (
            e.event_type is LedgerEventType.MARKET_STATUS
            and p.get("kind") == OPENED
            and (p.get("market"), p.get("venue")) == (market, venue)
        ):
            rate = D(p["fx_to_gbp"])
    return rate


@dataclass
class ReplayRun:
    """Load a verified strategy, its dataset and the venue's rules; replay; report."""

    config_root: Path
    jurisdiction: str
    market: str
    venue: str
    instrument: str
    tf: str
    strategy_path: Path
    class_name: str
    params: dict[str, Any] | None
    verdict_id: str
    data_root: Path
    state_root: Path
    out: Path
    lookback: int = 200
    start: datetime | None = None
    gbp_per_quote: Decimal | None = None
    transport: Transport = http_get

    async def run(self) -> ReplayResult:
        cfg = load_config(self.config_root, self.jurisdiction, [self.market])
        profile = cfg.markets[self.market]
        fees = load_fee_schedule(self.config_root, profile.costs.fee_schedule_for(self.venue))
        loaded = load_strategy(self.strategy_path, self.class_name)
        params = dict(loaded.strategy.default_params) | dict(self.params or {})
        path = self.state_root / "reports" / f"{self.verdict_id}.json"
        if not path.is_file():
            raise DeploymentRefused(f"no verifier report {path}")
        report = path.read_bytes()
        data = json.loads(report)["data"]
        verdict = verdict_for_deployment(
            report,
            code_sha256=loaded.code_sha256,
            params=params,
            venue=self.venue,
            instrument=self.instrument,
            tf=self.tf,
            capture_methods=capture_methods(self.data_root, data["dataset_version"]),
            issued_at=datetime.now(UTC),
        )
        bars = open_dataset(self.data_root, data["dataset_version"]).bars(
            self.venue, self.instrument, self.tf, as_of=FAR_FUTURE
        )
        start = self.start or (
            datetime.fromisoformat(data["hidden_from"]) if data.get("hidden_from") else None
        )
        if start is None:
            raise ValueError("no --start and the report has no hidden period to start from")
        decide_from = int((bars["open_time"] < pd.Timestamp(start)).sum())
        rate = self.gbp_per_quote or gbp_per_quote_from_ledger(
            self.state_root, self.market, self.venue
        )
        rec = latest_instruments(self.data_root)[(self.venue, self.instrument)][1]
        if rate is None:
            raise ValueError(
                f"no GBP rate for {rec.quote}: pass --gbp-per-quote (no paper account "
                "has opened on this venue to take it from)"
            )
        rules = await asyncio.to_thread(
            venue_rules,
            self.venue,
            self.instrument,
            rec.provider_symbols[self.venue],
            rec,
            self.transport,
        )
        self.out.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(ledger_path(self.out))
        result = await replay(
            bars,
            deployment=Deployment(
                verdict=verdict,
                strategy_id=verdict.strategy_id,
                params=params,
                market=self.market,
                venue=self.venue,
                instrument=self.instrument,
                tf=self.tf,
                quote_currency=rec.quote,
                lookback_bars=self.lookback,
                rebalance_threshold_pct=profile.execution.rebalance_threshold_pct,
                cost_buffer_bps=D(0),  # replaced by the simulator's own
            ),
            strategy=loaded.strategy,
            profile=profile,
            fees=fees,
            risk=cfg.risk,
            rules=rules,
            gbp_per_quote=rate,
            decide_from=decide_from,
            cost_bps=cost_bps(profile, fees),
            sink=LedgerSink(ledger),
        )
        summary = result.summary() | {
            "verdict_id": verdict.verdict_id,
            "dataset_version": data["dataset_version"],
            "from": result.bars[0].close_time.isoformat() if result.bars else None,
            "to": result.bars[-1].close_time.isoformat() if result.bars else None,
            "gbp_per_quote": str(rate),
            "start_cash": str(result.start_cash),
            "risk_profile": cfg.risk.risk_profile,
        }
        (self.out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        return result
