"""One valid payload for every bus subject (the contract tests use all of them)."""

from __future__ import annotations

from decimal import Decimal

from factories import T0, order_intent, order_proposal, risk_decision, verdict
from firm.core.base import FirmModel
from firm.core.domain import (
    Fill,
    Notification,
    NotificationLevel,
    OpportunityNote,
    OrderStatus,
    OrderUpdate,
    Side,
    StrategyCandidate,
    StrategySpec,
    TrialRecord,
)
from firm.core.messages import Subject
from firm.core.messages.subjects import (
    HaltScope,
    OwnerCommand,
    OwnerCommandKind,
    RiskHalt,
    RiskHeartbeat,
)


def note(level: NotificationLevel) -> Notification:
    return Notification(notification_id="n-1", level=level, text="feed stale", created_at=T0)


SAMPLES: dict[Subject, FirmModel] = {
    Subject.NOTES_OPPORTUNITY: OpportunityNote(
        note_id="o-1", source="synthetic", captured_at=T0, summary="volume spike"
    ),
    Subject.STRATEGY_CANDIDATE: StrategyCandidate(
        candidate_id="cand-1",
        strategy_id="s-1",
        version="1",
        code_path="strategies/s1.py",
        code_sha256="0" * 64,
        spec=StrategySpec(
            hypothesis="trend persists",
            market_profiles=("crypto",),
            instruments=("BTC-USD",),
            timeframe="1h",
            parameters={"fast": 10},
        ),
        trial_ids=("t-1",),
        created_at=T0,
    ),
    Subject.TRIAL_RECORDED: TrialRecord(
        trial_id="t-1",
        experiment_id="e-1",
        strategy_id="s-1",
        parameters={"fast": 10},
        metrics={"sharpe": 1.0},
        dataset_version="ds-1",
        recorded_at=T0,
    ),
    Subject.VERDICT_ISSUED: verdict(),
    Subject.RISK_DECISION: risk_decision(),
    Subject.RISK_HALT: RiskHalt(
        halt_id="h-1", scope=HaltScope.MARKET, market="crypto", reason="drawdown", issued_at=T0
    ),
    Subject.RISK_HEARTBEAT: RiskHeartbeat(sequence=1, sent_at=T0),
    Subject.ORDER_PROPOSED: order_proposal(),
    Subject.ORDER_INTENT: order_intent(),
    Subject.ORDER_UPDATE: OrderUpdate(
        client_order_id="c-1",
        venue="simulator",
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.01"),
        average_fill_price=Decimal("50000"),
        reason="filled",
        updated_at=T0,
    ),
    Subject.FILL_EXECUTED: Fill(
        fill_id="f-1",
        client_order_id="c-1",
        intent_id="i-1",
        verdict_id="v-1",
        risk_decision_id="r-1",
        venue="simulator",
        instrument="BTC-USD",
        side=Side.BUY,
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
        fee=Decimal("0.3"),
        fee_currency="USD",
        filled_at=T0,
    ),
    Subject.ALERT_URGENT_RISK: note(NotificationLevel.URGENT),
    Subject.ALERT_URGENT_EXECUTION: note(NotificationLevel.URGENT),
    Subject.ALERT_ACTION_EXECUTION: note(NotificationLevel.ACTION),
    Subject.DIGEST_DAILY: note(NotificationLevel.DIGEST),
    Subject.CONTROL_COMMAND: OwnerCommand(
        command_id="k-1",
        kind=OwnerCommandKind.KILL,
        scope=HaltScope.GLOBAL,
        reason="owner",
        issued_at=T0,
    ),
}
