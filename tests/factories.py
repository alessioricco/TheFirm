"""Valid example objects for tests. Override fields with keyword arguments."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from firm.core.domain import (
    CheckResult,
    CheckStatus,
    OrderIntent,
    OrderProposal,
    OrderType,
    RiskAction,
    RiskDecision,
    Side,
    Verdict,
    VerdictOutcome,
)

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def order_intent(**kw: Any) -> OrderIntent:
    fields: dict[str, Any] = {
        "intent_id": "i-1",
        "client_order_id": "c-1",
        "proposal_id": "p-1",
        "verdict_id": "v-1",
        "risk_decision_id": "r-1",
        "strategy_id": "s-1",
        "market": "crypto",
        "venue": "simulator",
        "instrument": "BTC-USD",
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "created_at": T0,
    }
    fields.update(kw)
    return OrderIntent(**fields)


def order_proposal(**kw: Any) -> OrderProposal:
    fields: dict[str, Any] = {
        "proposal_id": "p-1",
        "verdict_id": "v-1",
        "strategy_id": "s-1",
        "market": "crypto",
        "venue": "simulator",
        "instrument": "BTC-USD",
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "reference_price": Decimal("50000"),
        "quote_currency": "USD",
        "created_at": T0,
    }
    fields.update(kw)
    return OrderProposal(**fields)


def verdict(**kw: Any) -> Verdict:
    fields: dict[str, Any] = {
        "verdict_id": "v-1",
        "candidate_id": "cand-1",
        "strategy_id": "s-1",
        "dataset_version": "ds-1",
        "outcome": VerdictOutcome.PASS,
        "checks": (CheckResult(name="lookahead", status=CheckStatus.PASSED, detail="ok"),),
        "report": "all checks passed",
        "issued_at": T0,
    }
    fields.update(kw)
    return Verdict(**fields)


def risk_decision(**kw: Any) -> RiskDecision:
    fields: dict[str, Any] = {
        "decision_id": "r-1",
        "proposal_id": "p-1",
        "verdict_id": "v-1",
        "action": RiskAction.APPROVE,
        "reason": "within limits",
        "approved_quantity": Decimal("0.01"),
        "decided_at": T0,
    }
    fields.update(kw)
    return RiskDecision(**fields)
