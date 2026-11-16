from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from factories import T0, order_intent, risk_decision, verdict
from firm.core.domain import (
    CheckResult,
    CheckStatus,
    JudgeAnswer,
    JudgeCall,
    JudgeQuestion,
    JudgeQuestionKind,
    LedgerEvent,
    LedgerEventType,
    Notification,
    OrderStatus,
    OrderType,
    RiskAction,
    VerdictOutcome,
    can_transition,
)

# --- OrderIntent: firm rule 2 at the type level


@pytest.mark.parametrize("field", ["verdict_id", "risk_decision_id"])
def test_order_intent_requires_verdict_and_risk_ids(field: str) -> None:
    good = order_intent().model_dump()
    del good[field]
    with pytest.raises(ValidationError):
        type(order_intent()).model_validate(good)


@pytest.mark.parametrize("field", ["verdict_id", "risk_decision_id"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_order_intent_refuses_empty_ids(field: str, value: str | None) -> None:
    with pytest.raises(ValidationError):
        order_intent(**{field: value})


def test_order_intent_is_frozen() -> None:
    intent = order_intent()
    with pytest.raises(ValidationError):
        intent.verdict_id = "other"  # type: ignore[misc]


def test_order_intent_refuses_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        order_intent(llm_override=True)


def test_order_intent_requires_positive_quantity() -> None:
    with pytest.raises(ValidationError):
        order_intent(quantity=Decimal(0))


def test_order_intent_requires_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        order_intent(created_at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("order_type", "limit", "stop", "ok"),
    [
        (OrderType.MARKET, None, None, True),
        (OrderType.MARKET, Decimal(1), None, False),
        (OrderType.LIMIT, Decimal(1), None, True),
        (OrderType.LIMIT, None, None, False),
        (OrderType.STOP, None, Decimal(1), True),
        (OrderType.STOP, Decimal(1), None, False),
        (OrderType.STOP_LIMIT, Decimal(1), Decimal(1), True),
        (OrderType.STOP_LIMIT, Decimal(1), None, False),
    ],
)
def test_order_prices_match_type(
    order_type: OrderType, limit: Decimal | None, stop: Decimal | None, ok: bool
) -> None:
    kw = {"order_type": order_type, "limit_price": limit, "stop_price": stop}
    if ok:
        order_intent(**kw)
    else:
        with pytest.raises(ValidationError):
            order_intent(**kw)


# --- Verdict: unchecked is unverified, never passing (golden rule 2)


def _check(status: CheckStatus) -> CheckResult:
    return CheckResult(name="x", status=status, detail="d")


@pytest.mark.parametrize(
    ("statuses", "outcome"),
    [
        ([CheckStatus.PASSED], VerdictOutcome.PASS),
        ([CheckStatus.PASSED, CheckStatus.NOT_RUN], VerdictOutcome.UNVERIFIED),
        ([CheckStatus.PASSED, CheckStatus.FAILED], VerdictOutcome.FAIL),
        ([CheckStatus.NOT_RUN, CheckStatus.FAILED], VerdictOutcome.FAIL),
    ],
)
def test_verdict_outcome_follows_checks(
    statuses: list[CheckStatus], outcome: VerdictOutcome
) -> None:
    checks = tuple(_check(s) for s in statuses)
    assert verdict(checks=checks, outcome=outcome).outcome is outcome
    for other in set(VerdictOutcome) - {outcome}:
        with pytest.raises(ValidationError):
            verdict(checks=checks, outcome=other)


def test_verdict_needs_at_least_one_check() -> None:
    with pytest.raises(ValidationError):
        verdict(checks=())


# --- RiskDecision


def test_risk_reject_cannot_approve_quantity() -> None:
    risk_decision(action=RiskAction.REJECT, approved_quantity=None)
    with pytest.raises(ValidationError):
        risk_decision(action=RiskAction.REJECT, approved_quantity=Decimal(1))


@pytest.mark.parametrize("action", [RiskAction.APPROVE, RiskAction.RESIZE])
def test_risk_approve_and_resize_need_quantity(action: RiskAction) -> None:
    with pytest.raises(ValidationError):
        risk_decision(action=action, approved_quantity=None)


def test_risk_decision_needs_reason() -> None:
    with pytest.raises(ValidationError):
        risk_decision(reason="")


# --- Judge


def test_judge_answers_must_use_allowed_labels() -> None:
    q = JudgeQuestion(
        question_id="q1",
        kind=JudgeQuestionKind.REGIME,
        text="regime?",
        allowed_labels=("trend", "range"),
    )
    base = {"call_id": "j1", "model": "judge", "as_of": T0, "state": {}, "questions": (q,)}
    JudgeCall(**base, answers=(JudgeAnswer(question_id="q1", label="trend", probability=0.7),))
    with pytest.raises(ValidationError):
        JudgeCall(**base, answers=(JudgeAnswer(question_id="q1", label="buy", probability=0.7),))
    with pytest.raises(ValidationError):
        JudgeCall(**base, answers=(JudgeAnswer(question_id="q9", label="trend", probability=0.7),))


# --- Notification: no recipient field (firm rule 10)


def test_notification_has_no_recipient_field() -> None:
    assert not {"recipient", "recipients", "to", "chat_id"} & set(Notification.model_fields)


# --- Ledger event genesis


def test_ledger_event_prev_hash_only_absent_at_genesis() -> None:
    h = "0" * 64
    base = {
        "event_id": "e",
        "event_type": LedgerEventType.VERDICT,
        "reason": "r",
        "payload": {},
        "recorded_at": T0,
        "hash": h,
    }
    LedgerEvent(sequence=0, prev_hash=None, **base)
    LedgerEvent(sequence=1, prev_hash=h, **base)
    with pytest.raises(ValidationError):
        LedgerEvent(sequence=1, prev_hash=None, **base)
    with pytest.raises(ValidationError):
        LedgerEvent(sequence=0, prev_hash=h, **base)


# --- Order lifecycle


def test_terminal_states_have_no_exit() -> None:
    for terminal in (
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    ):
        assert not any(can_transition(terminal, s) for s in OrderStatus)


def test_partial_fill_can_continue_or_complete() -> None:
    assert can_transition(OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
    assert can_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED)
    assert can_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)
    assert not can_transition(OrderStatus.FILLED, OrderStatus.CANCELLED)
