"""The risk desk: approve, shrink or reject every order proposal (deterministic).

Limits come only from the risk profile (`config/risk/`); the desk has no way
to change them, and no agent-facing interface can reach it (firm rules 3, 7).
Same inputs, same decision. Nothing is guessed: an order whose value in GBP
can't be computed (no FX rate) is rejected.

Order of checks, each with its own reason:

1. a kill switch is on (global or this market)        -> reject
2. the verdict is not this proposal's, or didn't pass  -> reject
3. drawdown beyond the limit (global / market)         -> kill switch, reject
4. sells: capped at what is held (spot, long-only)     -> approve / shrink / reject
5. buys: daily loss limit reached                      -> reject
6. buys: shrink to the tightest of single order, position, exchange,
   market and total exposure; nothing left             -> reject

Kill switches are released only by an owner `RESUME` command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Annotated

from pydantic import AwareDatetime, Field

from firm.core.base import CurrencyCode, FirmModel, Id
from firm.core.config import RiskProfile
from firm.core.domain import (
    LedgerEventType,
    OrderProposal,
    RiskAction,
    RiskDecision,
    Side,
    Verdict,
    VerdictOutcome,
)
from firm.core.events import NULL_SINK, EventSink
from firm.core.messages.subjects import (
    HaltScope,
    OwnerCommand,
    OwnerCommandKind,
    RiskHalt,
    RiskHeartbeat,
)

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
QTY_STEP = Decimal("0.00000001")  # shrink to 1e-8 units; venue lot rules apply later
BASE = "GBP"


class Holding(FirmModel):
    market: Id
    venue: Id
    instrument: Id
    quantity: NonNegativeDecimal
    mark_price: PositiveDecimal
    quote_currency: CurrencyCode


class RiskState(FirmModel):
    """What the desk knows when it decides. Supplied by the portfolio/execution side."""

    as_of: AwareDatetime
    equity_gbp: PositiveDecimal  # cash + marked holdings
    peak_equity_gbp: PositiveDecimal
    day_start_equity_gbp: PositiveDecimal  # equity at 00:00 UTC
    holdings: tuple[Holding, ...]
    market_pnl_gbp: dict[Id, Decimal]  # cumulative P&L per market
    market_peak_pnl_gbp: dict[Id, Decimal]
    fx_to_gbp: dict[CurrencyCode, PositiveDecimal]  # 1 unit of currency in GBP


class _NoFx(Exception):
    pass


@dataclass
class RiskDesk:
    profile: RiskProfile
    sink: EventSink = NULL_SINK
    _halts: dict[str | None, RiskHalt] = field(default_factory=dict)  # None = global
    _beat: int = 0

    # --- kill switches

    def halted(self, market: str | None = None) -> bool:
        return None in self._halts or (market is not None and market in self._halts)

    def _halt(self, scope: HaltScope, market: str | None, reason: str, at: datetime) -> None:
        key = None if scope is HaltScope.GLOBAL else market
        if key in self._halts:
            return
        halt = RiskHalt(
            halt_id=f"h-{_digest([scope.value, market, reason, at.isoformat()])}",
            scope=scope,
            market=market,
            reason=reason,
            issued_at=at,
        )
        self._halts[key] = halt
        self.sink.emit(
            LedgerEventType.RISK_HALT,
            reason,
            halt.model_dump(mode="json"),
            event_id=f"halt:{halt.halt_id}",
        )

    def command(self, cmd: OwnerCommand) -> None:
        """Owner commands only: KILL sets a switch, RESUME releases one."""
        if cmd.kind is OwnerCommandKind.KILL:
            self._halt(cmd.scope, cmd.market, f"owner kill: {cmd.reason}", cmd.issued_at)
        else:
            self.resume(cmd)

    def resume(self, cmd: OwnerCommand) -> None:
        if cmd.kind is not OwnerCommandKind.RESUME:
            raise ValueError("only an owner RESUME command releases a kill switch")
        key = None if cmd.scope is HaltScope.GLOBAL else cmd.market
        if self._halts.pop(key, None) is not None:
            self.sink.emit(
                LedgerEventType.OWNER_COMMAND,
                f"resume: {cmd.reason}",
                cmd.model_dump(mode="json"),
                event_id=f"cmd:{cmd.command_id}",
            )

    def heartbeat(self, now: datetime) -> RiskHeartbeat:
        beat = RiskHeartbeat(sequence=self._beat, sent_at=now)
        self._beat += 1
        return beat

    # --- decisions

    def decide(self, verdict: Verdict, proposal: OrderProposal, state: RiskState) -> RiskDecision:
        action, qty, reason = self._decide(verdict, proposal, state)
        decision = RiskDecision(
            decision_id=f"r-{_digest([proposal.proposal_id])}",
            proposal_id=proposal.proposal_id,
            verdict_id=proposal.verdict_id,
            action=action,
            reason=reason,
            approved_quantity=None if action is RiskAction.REJECT else qty,
            decided_at=state.as_of,
        )
        self.sink.emit(
            LedgerEventType.RISK_DECISION,
            reason,
            decision.model_dump(mode="json"),
            event_id=f"risk:{decision.decision_id}",
        )
        return decision

    def _decide(
        self, verdict: Verdict, p: OrderProposal, s: RiskState
    ) -> tuple[RiskAction, Decimal, str]:
        reject = RiskAction.REJECT
        if self.halted(p.market):
            which = "global" if None in self._halts else p.market
            return reject, Decimal(0), f"halted: {which} kill switch is on"
        if (verdict.verdict_id, verdict.strategy_id) != (p.verdict_id, p.strategy_id):
            return reject, Decimal(0), "verdict does not belong to this proposal"
        if verdict.outcome is not VerdictOutcome.PASS:
            return reject, Decimal(0), f"verdict is {verdict.outcome.value}, not pass"
        limits = self.profile.markets.get(p.market)
        if limits is None:
            return reject, Decimal(0), f"no risk limits for market {p.market}"

        dd_total = (s.peak_equity_gbp - s.equity_gbp) / s.peak_equity_gbp * 100
        if dd_total >= Decimal(str(self.profile.limits.max_drawdown_pct)):
            reason = (
                f"total drawdown {dd_total:.1f}% >= "
                f"{self.profile.limits.max_drawdown_pct}%: global kill switch"
            )
            self._halt(HaltScope.GLOBAL, None, reason, s.as_of)
            return reject, Decimal(0), reason
        pnl = s.market_pnl_gbp.get(p.market, Decimal(0))
        peak = max(s.market_peak_pnl_gbp.get(p.market, Decimal(0)), pnl)
        dd_market = (peak - pnl) / s.equity_gbp * 100
        if dd_market >= Decimal(str(limits.max_drawdown_pct)):
            reason = (
                f"{p.market} drawdown {dd_market:.1f}% of equity >= "
                f"{limits.max_drawdown_pct}%: market kill switch"
            )
            self._halt(HaltScope.MARKET, p.market, reason, s.as_of)
            return reject, Decimal(0), reason

        held = sum(
            (
                h.quantity
                for h in s.holdings
                if (h.market, h.venue, h.instrument) == (p.market, p.venue, p.instrument)
            ),
            Decimal(0),
        )
        if p.side is Side.SELL:
            if held <= 0:
                return reject, Decimal(0), "nothing to sell (spot, long-only)"
            if p.quantity > held:
                return RiskAction.RESIZE, held, f"sell capped at the {held} held"
            return RiskAction.APPROVE, p.quantity, "sell within holdings"

        day_loss = (s.day_start_equity_gbp - s.equity_gbp) / s.day_start_equity_gbp * 100
        if day_loss >= Decimal(str(self.profile.limits.daily_loss_limit_pct)):
            return (
                reject,
                Decimal(0),
                (
                    f"daily loss limit: down {day_loss:.1f}% since 00:00 UTC "
                    f"(limit {self.profile.limits.daily_loss_limit_pct}%); no new buys today"
                ),
            )
        try:
            unit_gbp = p.reference_price * self._fx(s, p.quote_currency)
            caps = self._room(p, s, limits)
        except _NoFx as exc:
            return reject, Decimal(0), f"no FX rate {exc} -> GBP; can't value the order"
        name, room_gbp = min(caps.items(), key=lambda kv: kv[1])
        wanted_gbp = p.quantity * unit_gbp
        if wanted_gbp <= room_gbp:
            return RiskAction.APPROVE, p.quantity, "within all limits"
        qty = (room_gbp / unit_gbp).quantize(QTY_STEP, rounding=ROUND_DOWN) if room_gbp > 0 else 0
        if qty <= 0:
            return reject, Decimal(0), f"{name} limit reached; no room for this order"
        return (
            RiskAction.RESIZE,
            Decimal(qty),
            (f"shrunk to the {name} limit: room £{room_gbp:.2f} of £{wanted_gbp:.2f} asked"),
        )

    def _fx(self, s: RiskState, currency: str) -> Decimal:
        if currency == BASE:
            return Decimal(1)
        try:
            return s.fx_to_gbp[currency]
        except KeyError:
            raise _NoFx(currency) from None

    def _room(self, p: OrderProposal, s: RiskState, limits) -> dict[str, Decimal]:  # type: ignore[no-untyped-def]
        """GBP still available under each limit for this buy."""
        eq, lim = s.equity_gbp, self.profile.limits

        def value(pred) -> Decimal:  # type: ignore[no-untyped-def]
            return sum(
                (
                    h.quantity * h.mark_price * self._fx(s, h.quote_currency)
                    for h in s.holdings
                    if pred(h)
                ),
                Decimal(0),
            )

        def pct(x: float) -> Decimal:
            return eq * Decimal(str(x)) / 100

        caps = {
            "single order": pct(lim.max_order_notional_pct),
            "position": pct(limits.max_position_pct)
            - value(
                lambda h: (h.market, h.venue, h.instrument) == (p.market, p.venue, p.instrument)
            ),
            "market": pct(limits.max_market_exposure_pct) - value(lambda h: h.market == p.market),
            "total": pct(lim.max_total_exposure_pct) - value(lambda h: True),
        }
        if limits.exposure_cap_per_exchange_pct is not None:
            caps["exchange"] = pct(limits.exposure_cap_per_exchange_pct) - value(
                lambda h: h.market == p.market and h.venue == p.venue
            )
        return caps


def _digest(parts: list[object]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
