"""Risk desk interface. Implemented in milestone 2."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import OrderProposal, RiskDecision, Verdict


class RiskDesk(Protocol):
    def decide(self, verdict: Verdict, proposal: OrderProposal) -> RiskDecision:
        """Approve, resize (shrink only) or reject, with a reason."""
        ...

    def halted(self, market: str | None = None) -> bool: ...
