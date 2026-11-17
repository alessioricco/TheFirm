"""Typed domain objects: the interfaces between desks (SPEC "Components").

Rule of thumb: AI-produced objects (`StrategyCandidate`, `ModelArtifact`,
`JudgeCall` answers, `OpportunityNote`) are *proposals*. Only deterministic
desks produce `Verdict`, `RiskDecision`, `OrderIntent` and `Fill`.

Order path (DECISIONS 2026-11-16 "Order flow"):

    Verdict -> OrderProposal (execution sizes it; no risk ID yet)
            -> RiskDecision (risk approves / resizes / rejects that proposal)
            -> OrderIntent  (built only by `build_intent`, carries both IDs)
            -> VenueAdapter.submit -> OrderUpdate / Fill

Every `OrderIntent` carries a `verdict_id` and a `risk_decision_id`; the
schema refuses one without both (firm rule 2).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import AwareDatetime, Field, model_validator

from firm.core.base import CurrencyCode, FirmModel, Id, Probability, Reason, Sha256

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
ParamValue = int | float | str | bool


class DataTag(StrEnum):
    """Egress-gate tag (firm rule 8). `private` work runs on local models only."""

    PUBLIC = "public"
    PRIVATE = "private"


# --------------------------------------------------------------------------- research


class TrialRecord(FirmModel):
    """One counted trial. The count is global across experiments (firm rule 5)."""

    trial_id: Id
    experiment_id: Id
    strategy_id: Id
    parameters: dict[str, ParamValue]
    metrics: dict[str, float]
    dataset_version: Id
    recorded_at: AwareDatetime


class StrategySpec(FirmModel):
    """What a strategy claims to be: the hypothesis, its parameters and where it runs."""

    hypothesis: Reason
    market_profiles: tuple[Id, ...] = Field(min_length=1)
    instruments: tuple[Id, ...] = Field(min_length=1)
    timeframe: Id
    parameters: dict[str, ParamValue]


class StrategyCandidate(FirmModel):
    """Research desk output: code + spec + trial log. A proposal, nothing more."""

    candidate_id: Id
    strategy_id: Id
    version: Id
    experiment_id: Id | None = None
    code_path: Id
    code_sha256: Sha256
    spec: StrategySpec
    trial_ids: tuple[Id, ...]
    created_at: AwareDatetime


class TrainingRecord(FirmModel):
    dataset_version: Id
    feature_library_version: Id
    train_start: AwareDatetime
    train_end: AwareDatetime
    trial_ids: tuple[Id, ...]
    metrics: dict[str, float]

    @model_validator(mode="after")
    def _window_ordered(self) -> Self:
        if self.train_end <= self.train_start:
            raise ValueError("train_end must be after train_start")
        return self


class ModelArtifact(FirmModel):
    """ML desk output: weights + the record of how they were trained."""

    artifact_id: Id
    weights_path: Id
    weights_sha256: Sha256
    training: TrainingRecord
    created_at: AwareDatetime


# --------------------------------------------------------------------------- judge


class JudgeQuestionKind(StrEnum):
    NEWS_EVENT = "news_event"
    REGIME = "regime"
    PROPOSAL_CONSISTENCY = "proposal_consistency"


class JudgeQuestion(FirmModel):
    question_id: Id
    kind: JudgeQuestionKind
    text: Reason
    allowed_labels: tuple[Id, ...] = Field(min_length=2)


class JudgeAnswer(FirmModel):
    question_id: Id
    label: Id
    probability: Probability


class JudgeCall(FirmModel):
    """A typed call to the local decision model. Its answers are features, never orders."""

    call_id: Id
    model: Id  # from config, never hard-coded
    as_of: AwareDatetime
    state: dict[str, Any]
    questions: tuple[JudgeQuestion, ...] = Field(min_length=1)
    answers: tuple[JudgeAnswer, ...] = ()

    @model_validator(mode="after")
    def _answers_match_questions(self) -> Self:
        by_id = {q.question_id: q for q in self.questions}
        for a in self.answers:
            q = by_id.get(a.question_id)
            if q is None:
                raise ValueError(f"answer for unknown question {a.question_id!r}")
            if a.label not in q.allowed_labels:
                raise ValueError(f"label {a.label!r} not allowed for {a.question_id!r}")
        return self


# --------------------------------------------------------------------------- scout


class OpportunityNote(FirmModel):
    """Scout output. Becomes a research hypothesis at most; never an order."""

    note_id: Id
    source: Id
    captured_at: AwareDatetime
    summary: Reason
    instruments: tuple[Id, ...] = ()
    evidence_refs: tuple[Id, ...] = ()


# --------------------------------------------------------------------------- verification


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"  # reported as unverified, never as passing (golden rule 2)


class CheckResult(FirmModel):
    name: Id
    status: CheckStatus
    detail: Reason
    metrics: dict[str, float] = Field(default_factory=dict)


class VerdictOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIED = "unverified"


class Verdict(FirmModel):
    """Verification desk output. Produced only by deterministic checks."""

    verdict_id: Id
    candidate_id: Id
    strategy_id: Id
    dataset_version: Id
    outcome: VerdictOutcome
    checks: tuple[CheckResult, ...] = Field(min_length=1)
    report: Reason
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def _outcome_consistent(self) -> Self:
        statuses = {c.status for c in self.checks}
        if CheckStatus.FAILED in statuses:
            expected = VerdictOutcome.FAIL
        elif CheckStatus.NOT_RUN in statuses:
            expected = VerdictOutcome.UNVERIFIED
        else:
            expected = VerdictOutcome.PASS
        if self.outcome is not expected:
            raise ValueError(f"outcome {self.outcome} inconsistent with checks (want {expected})")
        return self

    @property
    def passed(self) -> bool:
        return self.outcome is VerdictOutcome.PASS


# --------------------------------------------------------------------------- risk


class RiskAction(StrEnum):
    APPROVE = "approve"
    RESIZE = "resize"
    REJECT = "reject"


class RiskDecision(FirmModel):
    """Risk desk output: approve / resize / reject, always with a reason."""

    decision_id: Id
    proposal_id: Id
    verdict_id: Id
    action: RiskAction
    reason: Reason
    approved_quantity: NonNegativeDecimal | None = None
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def _quantity_matches_action(self) -> Self:
        if self.action is RiskAction.REJECT:
            if self.approved_quantity not in (None, Decimal(0)):
                raise ValueError("a rejection cannot approve a quantity")
        elif self.approved_quantity is None or self.approved_quantity <= 0:
            raise ValueError(f"{self.action} requires a positive approved_quantity")
        return self


# --------------------------------------------------------------------------- execution


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    DAY = "day"


def _check_prices(
    order_type: OrderType, limit_price: Decimal | None, stop_price: Decimal | None
) -> None:
    needs_limit = order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
    needs_stop = order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
    if needs_limit != (limit_price is not None):
        raise ValueError(f"{order_type} order: limit_price presence is wrong")
    if needs_stop != (stop_price is not None):
        raise ValueError(f"{order_type} order: stop_price presence is wrong")


class OrderProposal(FirmModel):
    """A sized order the execution desk asks risk to decide on. Not sendable to a venue."""

    proposal_id: Id
    verdict_id: Id
    strategy_id: Id
    market: Id
    venue: Id
    instrument: Id
    side: Side
    order_type: OrderType
    quantity: PositiveDecimal
    reference_price: PositiveDecimal  # the price execution sized at (for risk in GBP)
    quote_currency: CurrencyCode  # currency of reference_price (e.g. USD, USDT)
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _prices_match_type(self) -> Self:
        _check_prices(self.order_type, self.limit_price, self.stop_price)
        return self


class OrderIntent(FirmModel):
    """An order the execution desk may send to a venue adapter.

    `verdict_id` and `risk_decision_id` are required and non-empty: an order
    without both cannot even be constructed (firm rule 2). Build it with
    `build_intent`, which checks it against the proposal and the decision.
    """

    intent_id: Id
    client_order_id: Id  # idempotency key at the venue
    proposal_id: Id
    verdict_id: Id
    risk_decision_id: Id
    strategy_id: Id
    market: Id
    venue: Id
    instrument: Id
    side: Side
    order_type: OrderType
    quantity: PositiveDecimal
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _prices_match_type(self) -> Self:
        _check_prices(self.order_type, self.limit_price, self.stop_price)
        return self


class IntentRefused(ValueError):
    """A risk decision does not authorise an order for this proposal."""


def build_intent(
    proposal: OrderProposal,
    decision: RiskDecision,
    *,
    intent_id: str,
    client_order_id: str,
    created_at: datetime,
) -> OrderIntent:
    """The only sanctioned way to turn a proposal into a sendable order.

    Refuses unless the decision is for this proposal and this verdict and is
    not a rejection. `approve` must keep the proposed quantity; `resize` may
    only shrink it. Everything else is copied from the proposal unchanged.
    """
    if decision.proposal_id != proposal.proposal_id:
        raise IntentRefused("risk decision is for a different proposal")
    if decision.verdict_id != proposal.verdict_id:
        raise IntentRefused("risk decision is for a different verdict")
    if decision.action is RiskAction.REJECT:
        raise IntentRefused(f"rejected by risk: {decision.reason}")
    qty = decision.approved_quantity
    if qty is None:  # unreachable: RiskDecision's validator requires it
        raise IntentRefused("risk decision has no approved quantity")
    if decision.action is RiskAction.APPROVE and qty != proposal.quantity:
        raise IntentRefused("approve must keep the proposed quantity")
    if decision.action is RiskAction.RESIZE and not qty < proposal.quantity:
        raise IntentRefused("resize may only reduce the quantity")
    return OrderIntent(
        intent_id=intent_id,
        client_order_id=client_order_id,
        proposal_id=proposal.proposal_id,
        verdict_id=proposal.verdict_id,
        risk_decision_id=decision.decision_id,
        strategy_id=proposal.strategy_id,
        market=proposal.market,
        venue=proposal.venue,
        instrument=proposal.instrument,
        side=proposal.side,
        order_type=proposal.order_type,
        quantity=qty,
        limit_price=proposal.limit_price,
        stop_price=proposal.stop_price,
        time_in_force=proposal.time_in_force,
        created_at=created_at,
    )


class Fill(FirmModel):
    """One execution against an order. Traces back to its verdict and risk decision."""

    fill_id: Id
    client_order_id: Id
    intent_id: Id
    verdict_id: Id
    risk_decision_id: Id
    venue: Id
    instrument: Id
    side: Side
    quantity: PositiveDecimal
    price: PositiveDecimal
    fee: NonNegativeDecimal
    fee_currency: CurrencyCode
    filled_at: AwareDatetime


class OrderStatus(StrEnum):
    """Full order lifecycle (SPEC "Integration layer")."""

    PENDING_NEW = "pending_new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)

ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_NEW: frozenset(
        {
            OrderStatus.ACCEPTED,
            OrderStatus.REJECTED,
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
        }
    ),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}
"""Allowed lifecycle transitions. Terminal states have no way out."""


def can_transition(old: OrderStatus, new: OrderStatus) -> bool:
    return new in ORDER_TRANSITIONS[old]


class OrderUpdate(FirmModel):
    """The venue's view of one order at one moment."""

    client_order_id: Id
    venue_order_id: Id | None = None
    venue: Id
    status: OrderStatus
    filled_quantity: NonNegativeDecimal
    average_fill_price: PositiveDecimal | None = None
    reason: Reason
    updated_at: AwareDatetime


# --------------------------------------------------------------------------- notify


class NotificationLevel(StrEnum):
    URGENT = "urgent"
    ACTION = "action"
    DIGEST = "digest"


class Notification(FirmModel):
    """A message to the owner. There is deliberately no recipient field (firm rule 10)."""

    notification_id: Id
    level: NotificationLevel
    text: Reason
    links: tuple[str, ...] = ()
    created_at: AwareDatetime


# --------------------------------------------------------------------------- ledger


class LedgerEventType(StrEnum):
    OPPORTUNITY_NOTE = "opportunity_note"
    STRATEGY_CANDIDATE = "strategy_candidate"
    TRIAL = "trial"
    TRIAL_ALIAS = "trial_alias"
    OOS_VAULT = "oos_vault"  # first_opening / replay / refused / sealed / alias
    VERDICT = "verdict"
    RISK_DECISION = "risk_decision"
    RISK_HALT = "risk_halt"
    ORDER_PROPOSAL = "order_proposal"
    ORDER_INTENT = "order_intent"
    ORDER_UPDATE = "order_update"
    FILL = "fill"
    NOTIFICATION = "notification"
    OWNER_COMMAND = "owner_command"
    MODEL_CALL = "model_call"


class LedgerEvent(FirmModel):
    """One append-only ledger entry, hash-chained to the previous one (firm rule 6)."""

    sequence: int = Field(ge=0)
    event_id: Id
    event_type: LedgerEventType
    reason: Reason
    payload: dict[str, Any]
    recorded_at: AwareDatetime
    prev_hash: Sha256 | None  # None only for sequence 0
    hash: Sha256

    @model_validator(mode="after")
    def _genesis(self) -> Self:
        if (self.sequence == 0) != (self.prev_hash is None):
            raise ValueError("only the first event (sequence 0) has no prev_hash")
        return self
