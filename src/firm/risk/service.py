"""The risk desk as a bus service: proposals in, decisions and heartbeats out.

- Every `order.proposed` gets a `risk.decision` from `RiskDesk.decide`, which
  also records it in the ledger. A proposal for a verdict the desk doesn't
  know is rejected (and recorded) the same way; one older than `max_age` is
  not decided at all: execution stopped waiting for it long ago, and
  deciding it now, on today's state, could contradict what was recorded.
- `risk.heartbeat` every `heartbeat_seconds`; execution stops ordering when
  they stop (golden rule 12). Heartbeat message IDs carry a boot ID, so a
  restarted desk's count starting again is never taken for a duplicate.

The desk computes its own risk state (`state`) when it decides: whatever
execution saw when it proposed is not trusted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from firm.bus.client import BusClient
from firm.core.domain import LedgerEventType, OrderProposal, RiskAction, RiskDecision, Verdict
from firm.core.messages import Envelope, Service, Subject
from firm.core.risk_state import RiskState
from firm.risk.desk import RiskDesk, _digest

log = logging.getLogger(__name__)

PROPOSALS = "risk-proposals"


def _now() -> datetime:
    return datetime.now(UTC)


class RiskService:
    def __init__(
        self,
        desk: RiskDesk,
        client: BusClient,
        *,
        verdicts: dict[str, Verdict],
        state: Callable[[datetime], Awaitable[RiskState | None]],
        heartbeat_seconds: float,
        max_age: timedelta,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if client.service is not Service.RISK:
            raise ValueError("the risk service publishes as risk")
        self.desk, self.client, self.verdicts = desk, client, verdicts
        self.state, self.clock = state, clock
        self.heartbeat_seconds, self.max_age = heartbeat_seconds, max_age
        self.boot = uuid.uuid4().hex[:12]
        self.decided = 0

    async def start(self) -> None:
        await self.client.subscribe(PROPOSALS, self.on_proposal)

    def _reject(self, p: OrderProposal, reason: str, at: datetime) -> RiskDecision:
        decision = RiskDecision(
            decision_id=f"r-{_digest([p.proposal_id])}",
            proposal_id=p.proposal_id,
            verdict_id=p.verdict_id,
            action=RiskAction.REJECT,
            reason=reason,
            decided_at=at,
        )
        self.desk.sink.emit(
            LedgerEventType.RISK_DECISION,
            reason,
            decision.model_dump(mode="json"),
            event_id=f"risk:{decision.decision_id}",
        )
        return decision

    async def on_proposal(self, env: Envelope) -> None:
        p = env.typed_payload()
        assert isinstance(p, OrderProposal)
        now = self.clock()
        if now - p.created_at > self.max_age:
            log.warning("risk: proposal %s is too old to decide; ignored", p.proposal_id)
            return
        verdict = self.verdicts.get(p.verdict_id)
        state = await self.state(now)
        if verdict is None:
            decision = self._reject(p, f"unknown verdict {p.verdict_id}", now)
        elif state is None:
            decision = self._reject(p, "no risk state (no fresh FX rate)", now)
        else:
            decision = self.desk.decide(verdict, p, state)
        self.decided += 1
        await self.client.publish(Subject.RISK_DECISION, decision, message_id=decision.decision_id)

    async def beat(self) -> None:
        hb = self.desk.heartbeat(self.clock())
        await self.client.publish(
            Subject.RISK_HEARTBEAT, hb, message_id=f"hb-{self.boot}-{hb.sequence}"
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Heartbeats until `stop`. A failed publish is logged and retried next beat."""
        failing = False
        while not stop.is_set():
            try:
                await self.beat()
                if failing:
                    log.warning("risk: heartbeats published again")
                failing = False
            except Exception as exc:
                if not failing:  # once per outage, not every beat
                    log.warning("risk: heartbeat not published (%s); retrying", exc)
                failing = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
