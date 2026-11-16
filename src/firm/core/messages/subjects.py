"""Bus subjects, services, and who may publish what (SPEC "Desks as services").

The permission table is the org chart: each service may publish only the
subjects it owns, and every subject has exactly one owner. Subjects that
several desks need (alerts) are split per service, e.g.
`alert.urgent.risk`, so the notifier subscribes to `alert.>` while each
desk can publish only its own branch. No agent service may publish `order.*` or `risk.*`
(golden rule 12). The NATS account config will be generated from this table
so the bus enforces the same rule as the code.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from firm.core.base import FirmModel, Id, Reason
from firm.core.domain import (
    Fill,
    Notification,
    OpportunityNote,
    OrderIntent,
    OrderProposal,
    OrderUpdate,
    RiskDecision,
    StrategyCandidate,
    TrialRecord,
    Verdict,
)


class Service(StrEnum):
    SCOUT = "scout"
    RESEARCH = "research"
    VERIFICATION = "verification"
    RISK = "risk"
    EXECUTION = "execution"
    LEDGER = "ledger"
    NOTIFIER = "notifier"
    CONTROL = "control"


AGENT_SERVICES: frozenset[Service] = frozenset({Service.SCOUT, Service.RESEARCH})
"""Services whose behaviour comes from language models."""


class Subject(StrEnum):
    NOTES_OPPORTUNITY = "notes.opportunity"
    STRATEGY_CANDIDATE = "strategy.candidate"
    TRIAL_RECORDED = "trial.recorded"
    VERDICT_ISSUED = "verdict.issued"
    RISK_DECISION = "risk.decision"
    RISK_HALT = "risk.halt"
    RISK_HEARTBEAT = "risk.heartbeat"
    ORDER_PROPOSED = "order.proposed"
    ORDER_INTENT = "order.intent"
    ORDER_UPDATE = "order.update"
    FILL_EXECUTED = "fill.executed"
    ALERT_URGENT_RISK = "alert.urgent.risk"  # kill switch, drawdown limit, halts
    ALERT_URGENT_EXECUTION = "alert.urgent.execution"  # feed stale, venue disconnected
    ALERT_ACTION_EXECUTION = "alert.action.execution"  # paper orders and fills
    DIGEST_DAILY = "digest.daily"  # built from the ledger after each market's close
    CONTROL_COMMAND = "control.command"


# --------------------------------------------------------------------------- payloads
# Payloads that are not domain objects live here; they exist only on the bus.


class HaltScope(StrEnum):
    MARKET = "market"
    GLOBAL = "global"


class RiskHalt(FirmModel):
    halt_id: Id
    scope: HaltScope
    market: Id | None = None
    reason: Reason
    issued_at: AwareDatetime


class RiskHeartbeat(FirmModel):
    """Execution halts by itself when these stop arriving (golden rule 12)."""

    sequence: int = Field(ge=0)
    sent_at: AwareDatetime


class OwnerCommandKind(StrEnum):
    KILL = "kill"


class OwnerCommand(FirmModel):
    command_id: Id
    kind: OwnerCommandKind
    scope: HaltScope
    market: Id | None = None
    reason: Reason
    issued_at: AwareDatetime


# --------------------------------------------------------------------------- tables

PUBLISHERS: dict[Subject, Service | None] = {
    Subject.NOTES_OPPORTUNITY: Service.SCOUT,
    Subject.STRATEGY_CANDIDATE: Service.RESEARCH,
    Subject.TRIAL_RECORDED: Service.RESEARCH,
    Subject.VERDICT_ISSUED: Service.VERIFICATION,
    Subject.RISK_DECISION: Service.RISK,
    Subject.RISK_HALT: Service.RISK,
    Subject.RISK_HEARTBEAT: Service.RISK,
    Subject.ORDER_PROPOSED: Service.EXECUTION,
    Subject.ORDER_INTENT: Service.EXECUTION,
    Subject.ORDER_UPDATE: Service.EXECUTION,
    Subject.FILL_EXECUTED: Service.EXECUTION,
    Subject.ALERT_URGENT_RISK: Service.RISK,
    Subject.ALERT_URGENT_EXECUTION: Service.EXECUTION,
    Subject.ALERT_ACTION_EXECUTION: Service.EXECUTION,
    Subject.DIGEST_DAILY: Service.LEDGER,
    Subject.CONTROL_COMMAND: Service.CONTROL,
}
"""Exactly one owner per subject; `None` would mean no service may publish it."""

PAYLOADS: dict[Subject, type[FirmModel]] = {
    Subject.NOTES_OPPORTUNITY: OpportunityNote,
    Subject.STRATEGY_CANDIDATE: StrategyCandidate,
    Subject.TRIAL_RECORDED: TrialRecord,
    Subject.VERDICT_ISSUED: Verdict,
    Subject.RISK_DECISION: RiskDecision,
    Subject.RISK_HALT: RiskHalt,
    Subject.RISK_HEARTBEAT: RiskHeartbeat,
    Subject.ORDER_PROPOSED: OrderProposal,
    Subject.ORDER_INTENT: OrderIntent,
    Subject.ORDER_UPDATE: OrderUpdate,
    Subject.FILL_EXECUTED: Fill,
    Subject.ALERT_URGENT_RISK: Notification,
    Subject.ALERT_URGENT_EXECUTION: Notification,
    Subject.ALERT_ACTION_EXECUTION: Notification,
    Subject.DIGEST_DAILY: Notification,
    Subject.CONTROL_COMMAND: OwnerCommand,
}
"""The schema of every subject's payload."""

SCHEMA_VERSIONS: dict[Subject, int] = dict.fromkeys(Subject, 1)
"""Current payload schema version per subject. Bump on any breaking change."""


def may_publish(service: Service, subject: Subject) -> bool:
    return PUBLISHERS[subject] is service


def subjects_published_by(service: Service) -> frozenset[Subject]:
    return frozenset(s for s, owner in PUBLISHERS.items() if owner is service)
