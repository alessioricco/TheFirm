"""The risk desk as execution reaches it over the bus.

`BusRisk.decide` publishes the `OrderProposal` on `order.proposed` and waits
for the `risk.decision` that names it. No answer within `timeout` raises
`RiskUnavailable`: execution skips the bar and places nothing (it never
invents a decision). Heartbeats from `risk.heartbeat` go to `on_beat`
(the worker's monitor), so execution stops ordering when risk goes quiet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from firm.bus.client import BusClient
from firm.core.domain import OrderProposal, RiskDecision, Verdict
from firm.core.messages import Envelope, Service, Subject
from firm.core.messages.subjects import RiskHeartbeat
from firm.core.risk_state import RiskState
from firm.execution.worker import RiskUnavailable

__all__ = ["BusRisk", "RiskUnavailable"]

DECISIONS = "execution-decisions"
HEARTBEATS = "execution-heartbeat"


class BusRisk:
    def __init__(
        self,
        client: BusClient,
        *,
        timeout: float,
        on_beat: Callable[[RiskHeartbeat], object] | None = None,
    ) -> None:
        if client.service is not Service.EXECUTION:
            raise ValueError("proposals are published by execution")
        self.client, self.timeout, self.on_beat = client, timeout, on_beat
        self._waiting: dict[str, asyncio.Future[RiskDecision]] = {}

    async def start(self) -> None:
        await self.client.subscribe(DECISIONS, self._on_decision)
        await self.client.subscribe(HEARTBEATS, self._on_heartbeat)

    async def _on_decision(self, env: Envelope) -> None:
        decision = env.typed_payload()
        assert isinstance(decision, RiskDecision)
        fut = self._waiting.get(decision.proposal_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    async def _on_heartbeat(self, env: Envelope) -> None:
        beat = env.typed_payload()
        assert isinstance(beat, RiskHeartbeat)
        if self.on_beat is not None:
            self.on_beat(beat)

    async def decide(
        self, verdict: Verdict, proposal: OrderProposal, state: RiskState
    ) -> RiskDecision:
        """Ask risk. `verdict` and `state` stay here: risk uses its own."""
        fut: asyncio.Future[RiskDecision] = asyncio.get_running_loop().create_future()
        self._waiting[proposal.proposal_id] = fut
        try:
            try:
                await self.client.publish(
                    Subject.ORDER_PROPOSED, proposal, message_id=proposal.proposal_id
                )
            except Exception as exc:  # the bus is down: same as no answer
                raise RiskUnavailable(f"proposal not delivered to risk: {exc!r}"[:200]) from None
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except TimeoutError:
            raise RiskUnavailable(f"no risk decision within {self.timeout:g}s") from None
        finally:
            self._waiting.pop(proposal.proposal_id, None)
