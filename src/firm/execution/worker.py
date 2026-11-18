"""One market's execution worker: closed bars in, paper orders out, through risk.

For each closed bar of a deployed strategy:

1. Halted? A stale feed, a lost risk heartbeat or no risk state (no fresh
   FX rate) means no new order, recorded as a skip.
2. The strategy's target exposure for the bar just closed (its `signals` on
   the last `lookback_bars` closed bars; NaN means flat, as in the backtest).
3. Sizing: target times the market account's equity, after a buffer for costs,
   rounded down to the venue's lot. Changes under `rebalance_threshold_pct`
   of equity are skipped (going flat never is). One working order at a time.
4. `OrderProposal` → the risk desk's `RiskDecision` → `build_intent` (the only
   path to an order; it carries the verdict ID and the risk decision ID) →
   the venue adapter. Every step is a ledger event.

Market data goes to the paper venue through `on_market`, after the bar was
decided, so an order fills on the next trade or bar, never the one it saw.

The worker has no method that accepts an order from outside: its only inputs
are market data, feed status and risk heartbeats (firm rule 1). IDs are
derived from (verdict, instrument, bar time), so a replayed bar can't create
a second order: the venue sees the same client order ID.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

import pandas as pd

from firm.core.domain import (
    Fill,
    LedgerEventType,
    OrderIntent,
    OrderProposal,
    OrderType,
    OrderUpdate,
    RiskAction,
    RiskDecision,
    Side,
    Verdict,
    VerdictOutcome,
    build_intent,
)
from firm.core.events import NULL_SINK, EventSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.core.messages.subjects import RiskHeartbeat
from firm.core.risk_state import RiskState
from firm.data.providers.base import NS, BarRow
from firm.execution.adapter import VenueAdapter
from firm.execution.portfolio import MarketAccount
from firm.execution.simulator import MarketBar, MarketTrade, PaperSimulator

ParamValue = int | float | str | bool


class RiskDecider(Protocol):
    """The risk desk as execution sees it (in-process now, over the bus later)."""

    def decide(
        self, verdict: Verdict, proposal: OrderProposal, state: RiskState
    ) -> RiskDecision: ...


class SignalSource(Protocol):
    def signals(self, bars: pd.DataFrame, params: Mapping[str, ParamValue]) -> pd.Series: ...


@dataclass(frozen=True)
class Deployment:
    """A verified strategy, cleared to paper-trade one instrument on one venue."""

    verdict: Verdict
    strategy_id: str
    params: Mapping[str, ParamValue]
    market: str
    venue: str
    instrument: str
    tf: str
    quote_currency: str
    lookback_bars: int
    rebalance_threshold_pct: Decimal
    cost_buffer_bps: Decimal  # taker fee + half spread + slippage: keeps buys affordable
    # share of equity a target of 1 holds: the size the verifier judged it at
    position_fraction: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if self.verdict.outcome is not VerdictOutcome.PASS:
            raise ValueError(f"verdict {self.verdict.verdict_id} is {self.verdict.outcome}")
        if self.verdict.strategy_id != self.strategy_id:
            raise ValueError("the verdict is for another strategy")
        if self.lookback_bars < 2:
            raise ValueError("lookback_bars must be at least 2")
        if not 0 < self.position_fraction <= 1:
            raise ValueError(f"position_fraction {self.position_fraction} outside (0, 1]")


@dataclass(frozen=True)
class Outcome:
    """What one closed bar led to (for logs and tests)."""

    bar_close: datetime
    action: str  # skip | proposed | rejected | submitted
    reason: str
    target: float | None = None
    proposal: OrderProposal | None = None
    decision: RiskDecision | None = None
    intent: OrderIntent | None = None
    update: OrderUpdate | None = None


def _digest(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _ts(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / NS, tz=UTC)


class MarketWorker:
    def __init__(
        self,
        deployment: Deployment,
        strategy: SignalSource,
        adapter: VenueAdapter,
        risk: RiskDecider,
        risk_state: Callable[[datetime], Awaitable[RiskState | None]],
        heartbeat: HeartbeatMonitor,
        *,
        sink: EventSink = NULL_SINK,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.d = deployment
        self.strategy = strategy
        self.adapter = adapter
        self.risk = risk
        self.risk_state = risk_state
        self.heartbeat = heartbeat
        self.sink = sink
        self._clock = clock
        self._bars: deque[BarRow] = deque(maxlen=deployment.lookback_bars)
        self._feed_stale = False
        self._last_price: Decimal | None = None
        self.outcomes: list[Outcome] = []

    # ------------------------------------------------------------------ inputs

    def on_heartbeat(self, beat: RiskHeartbeat) -> None:
        self.heartbeat.beat(beat)

    def on_feed_status(self, stale: bool) -> None:
        self._feed_stale = stale

    def on_market(self, event: MarketTrade | MarketBar) -> list[OrderUpdate | Fill]:
        """Market data for the paper venue; records the fills and updates it caused."""
        self._last_price = event.price if isinstance(event, MarketTrade) else event.close
        if not isinstance(self.adapter, PaperSimulator):
            return []
        out = self.adapter.on_market(event)
        for item in out:
            self._record_venue(item)
        return out

    def warm_up(self, bars: list[BarRow]) -> int:
        """Load closed history for the strategy's lookback without deciding on it."""
        added = 0
        for bar in sorted(bars, key=lambda b: b["open_time_ns"]):
            if self._bars and bar["open_time_ns"] <= self._bars[-1]["open_time_ns"]:
                continue
            self._bars.append(bar)
            self._last_price = Decimal(str(bar["close"]))
            added += 1
        return added

    async def on_bar(self, bar: BarRow) -> Outcome:
        if self._bars and bar["open_time_ns"] <= self._bars[-1]["open_time_ns"]:
            outcome = self._skip(bar, "bar already seen")  # replays never decide twice
            self.outcomes.append(outcome)
            return outcome
        self._bars.append(bar)
        self._last_price = Decimal(str(bar["close"]))
        outcome = await self._decide(bar)
        self.outcomes.append(outcome)
        return outcome

    # ------------------------------------------------------------------ helpers

    def _now(self, bar: BarRow) -> datetime:
        close = _ts(bar["close_time_ns"])
        return max(close, self._clock()) if self._clock is not None else close

    def _record_venue(self, item: OrderUpdate | Fill) -> None:
        if isinstance(item, Fill):
            self.sink.emit(
                LedgerEventType.FILL,
                f"filled {item.quantity} {item.instrument} at {item.price}",
                item.model_dump(mode="json"),
                event_id=f"fill:{item.fill_id}",
            )
        else:
            self.sink.emit(
                LedgerEventType.ORDER_UPDATE,
                f"{item.client_order_id} {item.status.value}: {item.reason}",
                item.model_dump(mode="json"),
                event_id=f"update:{item.client_order_id}:{item.status.value}:"
                f"{item.filled_quantity}",
            )

    def _skip(self, bar: BarRow, reason: str, target: float | None = None) -> Outcome:
        return Outcome(_ts(bar["close_time_ns"]), "skip", reason, target)

    def _target(self) -> float | None:
        frame = pd.DataFrame(list(self._bars))
        frame["open_time"] = pd.to_datetime(frame["open_time_ns"], unit="ns", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time_ns"], unit="ns", utc=True)
        frame = frame.drop(columns=["open_time_ns", "close_time_ns"])
        sig = self.strategy.signals(frame, self.d.params)
        if len(sig) != len(frame):
            raise ValueError(f"strategy returned {len(sig)} signals for {len(frame)} bars")
        value = float(sig.iloc[-1])
        if value != value:  # NaN: warm-up, flat as in the backtest
            return 0.0
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"target {value} outside [0, 1] (spot, no leverage, no shorting)")
        return value

    async def account(self) -> MarketAccount:
        """This market's venue account and marks, for the portfolio's risk state."""
        rules = await self.adapter.instrument_rules(self.d.instrument)
        return MarketAccount(
            market=self.d.market,
            venue=self.d.venue,
            balances=await self.adapter.balances(),
            positions=await self.adapter.positions(),
            marks={self.d.instrument: self._last_price} if self._last_price else {},
            rules={self.d.instrument: rules},
        )

    # ------------------------------------------------------------------ the decision

    async def _decide(self, bar: BarRow) -> Outcome:
        now = self._now(bar)
        if self._feed_stale:
            return self._skip(bar, "feed stale: no new orders")
        if not self.heartbeat.alive(now):
            return self._skip(bar, "risk heartbeat lost: no new orders")
        if len(self._bars) < self.d.lookback_bars:
            return self._skip(bar, f"warming up ({len(self._bars)}/{self.d.lookback_bars} bars)")
        target = self._target()
        if await self.adapter.open_orders():  # one deployment per venue account in v1
            return self._skip(bar, "an order is still working", target)

        rules = await self.adapter.instrument_rules(self.d.instrument)
        price = Decimal(str(bar["close"]))
        cash = next(
            (b.total for b in await self.adapter.balances() if b.currency == rules.quote_currency),
            Decimal(0),
        )
        held = next(
            (
                p.quantity
                for p in await self.adapter.positions()
                if p.instrument == self.d.instrument
            ),
            Decimal(0),
        )
        equity = cash + held * price
        buffer = 1 + self.d.cost_buffer_bps / 10_000
        want = Decimal(str(target)) * self.d.position_fraction * equity / (price * buffer)
        want = (want / rules.lot_size).to_integral_value(rounding=ROUND_DOWN) * rules.lot_size
        delta = want - held
        if delta == 0:
            return self._skip(bar, "at target", target)
        if want > 0 and abs(delta) * price < self.d.rebalance_threshold_pct / 100 * equity:
            return self._skip(bar, "change below the rebalance threshold", target)
        side = Side.BUY if delta > 0 else Side.SELL
        qty = abs(delta)
        if qty < rules.min_quantity or qty * price < rules.min_notional:
            return self._skip(bar, "below the venue's minimum order", target)

        state = await self.risk_state(now)
        if state is None:
            return self._skip(bar, "no risk state (no fresh FX rate): no new orders", target)

        key = _digest(
            self.d.verdict.verdict_id, self.d.venue, self.d.instrument, bar["close_time_ns"]
        )
        proposal = OrderProposal(
            proposal_id=f"p-{key}",
            verdict_id=self.d.verdict.verdict_id,
            strategy_id=self.d.strategy_id,
            market=self.d.market,
            venue=self.d.venue,
            instrument=self.d.instrument,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            reference_price=price,
            quote_currency=self.d.quote_currency,
            created_at=now,
        )
        self.sink.emit(
            LedgerEventType.ORDER_PROPOSAL,
            f"{side.value} {qty} {self.d.instrument}: target {target:.3f} of equity",
            proposal.model_dump(mode="json"),
            event_id=f"proposal:{proposal.proposal_id}",
        )
        decision = self.risk.decide(self.d.verdict, proposal, state)
        if decision.action is RiskAction.REJECT:
            return Outcome(
                _ts(bar["close_time_ns"]), "rejected", decision.reason, target, proposal, decision
            )
        intent = build_intent(
            proposal,
            decision,
            intent_id=f"i-{key}",
            client_order_id=f"c-{key}",
            created_at=now,
            lot_size=rules.lot_size,
        )
        self.sink.emit(
            LedgerEventType.ORDER_INTENT,
            f"{intent.side.value} {intent.quantity} {intent.instrument} "
            f"(verdict {intent.verdict_id}, risk {intent.risk_decision_id})",
            intent.model_dump(mode="json"),
            event_id=f"intent:{intent.intent_id}",
        )
        update = await self.adapter.submit(intent)
        self._record_venue(update)
        return Outcome(
            _ts(bar["close_time_ns"]),
            "submitted",
            update.reason,
            target,
            proposal,
            decision,
            intent,
            update,
        )
