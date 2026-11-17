"""Proposal -> risk decision -> intent. `build_intent` is the only sanctioned path."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from factories import T0, order_proposal, risk_decision
from firm.core.domain import (
    IntentRefused,
    OrderProposal,
    OrderType,
    RiskAction,
    build_intent,
)


def _build(proposal, decision):
    return build_intent(proposal, decision, intent_id="i-1", client_order_id="c-1", created_at=T0)


def test_proposal_has_no_risk_decision_id_and_refuses_one() -> None:
    assert "risk_decision_id" not in OrderProposal.model_fields
    with pytest.raises(ValidationError):
        order_proposal(risk_decision_id="r-1")


def test_approve_copies_proposal_and_carries_both_ids() -> None:
    p = order_proposal(order_type=OrderType.LIMIT, limit_price=Decimal("50000"))
    intent = _build(p, risk_decision())
    assert intent.verdict_id == p.verdict_id
    assert intent.risk_decision_id == "r-1"
    assert intent.proposal_id == p.proposal_id
    assert intent.quantity == p.quantity
    assert intent.limit_price == Decimal("50000")
    assert (intent.instrument, intent.side, intent.venue) == (p.instrument, p.side, p.venue)


def test_resize_shrinks_quantity() -> None:
    d = risk_decision(action=RiskAction.RESIZE, approved_quantity=Decimal("0.004"))
    assert _build(order_proposal(), d).quantity == Decimal("0.004")


@pytest.mark.parametrize("qty", ["0.01", "0.02"])
def test_resize_cannot_keep_or_grow_quantity(qty: str) -> None:
    d = risk_decision(action=RiskAction.RESIZE, approved_quantity=Decimal(qty))
    with pytest.raises(IntentRefused, match="reduce"):
        _build(order_proposal(), d)


def test_approve_cannot_change_quantity() -> None:
    d = risk_decision(approved_quantity=Decimal("0.005"))
    with pytest.raises(IntentRefused, match="keep"):
        _build(order_proposal(), d)


def test_reject_refuses_intent() -> None:
    d = risk_decision(action=RiskAction.REJECT, approved_quantity=None, reason="drawdown")
    with pytest.raises(IntentRefused, match="drawdown"):
        _build(order_proposal(), d)


def test_decision_for_other_proposal_refused() -> None:
    with pytest.raises(IntentRefused, match="different proposal"):
        _build(order_proposal(), risk_decision(proposal_id="p-other"))


def test_decision_for_other_verdict_refused() -> None:
    with pytest.raises(IntentRefused, match="different verdict"):
        _build(order_proposal(), risk_decision(verdict_id="v-other"))


def test_lot_size_rounds_the_approved_quantity_down_never_up() -> None:
    d = risk_decision(action=RiskAction.RESIZE, approved_quantity=Decimal("0.00456789"))
    intent = build_intent(
        order_proposal(),
        d,
        intent_id="i-1",
        client_order_id="c-1",
        created_at=T0,
        lot_size=Decimal("0.0001"),
    )
    assert intent.quantity == Decimal("0.0045")
    tiny = risk_decision(action=RiskAction.RESIZE, approved_quantity=Decimal("0.00005"))
    with pytest.raises(IntentRefused, match="lot"):
        build_intent(
            order_proposal(),
            tiny,
            intent_id="i-1",
            client_order_id="c-1",
            created_at=T0,
            lot_size=Decimal("0.0001"),
        )
