"""Risk desk interface (implemented by `firm.risk.desk.RiskDesk`)."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import OrderProposal, RiskDecision, Verdict
from firm.risk.desk import RiskState


class RiskDeskLike(Protocol):
    def decide(self, verdict: Verdict, proposal: OrderProposal, state: RiskState) -> RiskDecision:
        """Approve, resize (shrink only) or reject, always with a reason."""
        ...

    def halted(self, market: str | None = None) -> bool: ...
