"""The bus permission table (golden rule 12) and envelope contract."""

import pytest
from pydantic import ValidationError

from factories import T0, order_intent, verdict
from firm.core.base import FirmModel
from firm.core.messages import (
    AGENT_SERVICES,
    PAYLOADS,
    PUBLISHERS,
    SCHEMA_VERSIONS,
    Envelope,
    Service,
    Subject,
    may_publish,
    wrap,
)

# --- the permission table


def test_every_subject_has_a_payload_schema_and_version() -> None:
    assert set(PAYLOADS) == set(Subject)
    assert set(PUBLISHERS) == set(Subject)
    assert set(SCHEMA_VERSIONS) == set(Subject)
    assert all(issubclass(m, FirmModel) for m in PAYLOADS.values())


@pytest.mark.parametrize("agent", sorted(AGENT_SERVICES))
@pytest.mark.parametrize("subject", list(Subject))
def test_no_agent_may_publish_orders_risk_fills_verdicts_or_alerts(
    agent: Service, subject: Subject
) -> None:
    forbidden_prefixes = ("order.", "risk.", "fill.", "verdict.", "control.", "alert.", "digest.")
    if subject.value.startswith(forbidden_prefixes):
        assert not may_publish(agent, subject)


def test_order_path_subjects_are_owned_by_deterministic_desks() -> None:
    expected = {
        Subject.ORDER_PROPOSED: Service.EXECUTION,
        Subject.ORDER_INTENT: Service.EXECUTION,
        Subject.ORDER_UPDATE: Service.EXECUTION,
        Subject.FILL_EXECUTED: Service.EXECUTION,
        Subject.RISK_DECISION: Service.RISK,
        Subject.RISK_HALT: Service.RISK,
        Subject.RISK_HEARTBEAT: Service.RISK,
        Subject.VERDICT_ISSUED: Service.VERIFICATION,
    }
    for subject, owner in expected.items():
        assert PUBLISHERS[subject] is owner


def test_notifier_publishes_nothing() -> None:
    assert not any(may_publish(Service.NOTIFIER, s) for s in Subject)


def test_ledger_publishes_only_the_digest() -> None:
    assert {s for s in Subject if may_publish(Service.LEDGER, s)} == {Subject.DIGEST_DAILY}


def test_alert_subjects_are_namespaced_by_their_publisher() -> None:
    """alert.<level>.<service>: a desk can only raise alerts under its own name."""
    for subject, owner in PUBLISHERS.items():
        if subject.value.startswith("alert."):
            assert owner is not None
            assert subject.value.split(".")[-1] == owner.value


def test_permission_table_snapshot() -> None:
    """Any change to who-publishes-what must be a deliberate edit here too."""
    snapshot = {s.value: (o.value if o else None) for s, o in PUBLISHERS.items()}
    assert snapshot == {
        "notes.opportunity": "scout",
        "strategy.candidate": "research",
        "trial.recorded": "research",
        "verdict.issued": "verification",
        "risk.decision": "risk",
        "risk.halt": "risk",
        "risk.heartbeat": "risk",
        "order.proposed": "execution",
        "order.intent": "execution",
        "order.update": "execution",
        "fill.executed": "execution",
        "alert.urgent.risk": "risk",
        "alert.urgent.execution": "execution",
        "alert.action.execution": "execution",
        "digest.daily": "ledger",
        "control.command": "control",
    }


# --- the envelope


def test_wrap_round_trips_typed_payload() -> None:
    env = wrap(
        message_id="m-1",
        sender=Service.EXECUTION,
        subject=Subject.ORDER_INTENT,
        payload=order_intent(),
        timestamp=T0,
    )
    assert env.typed_payload() == order_intent()
    assert Envelope.model_validate_json(env.model_dump_json()) == env


@pytest.mark.parametrize("sender", [Service.RESEARCH, Service.SCOUT, Service.CONTROL])
def test_envelope_refuses_unauthorised_sender(sender: Service) -> None:
    with pytest.raises(ValidationError, match="permission denied"):
        wrap(
            message_id="m",
            sender=sender,
            subject=Subject.ORDER_INTENT,
            payload=order_intent(),
            timestamp=T0,
        )


def test_envelope_refuses_wrong_payload_type() -> None:
    with pytest.raises(TypeError):
        wrap(
            message_id="m",
            sender=Service.EXECUTION,
            subject=Subject.ORDER_INTENT,
            payload=verdict(),
            timestamp=T0,
        )


def test_envelope_validates_raw_payload() -> None:
    raw = order_intent().model_dump(mode="json")
    del raw["risk_decision_id"]
    with pytest.raises(ValidationError):
        Envelope(
            message_id="m",
            subject=Subject.ORDER_INTENT,
            schema_version=1,
            timestamp=T0,
            sender=Service.EXECUTION,
            payload=raw,
        )


def test_envelope_refuses_wrong_schema_version() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            message_id="m",
            subject=Subject.ORDER_INTENT,
            schema_version=99,
            timestamp=T0,
            sender=Service.EXECUTION,
            payload=order_intent().model_dump(mode="json"),
        )


def test_every_subject_has_an_owner() -> None:
    assert all(owner is not None for owner in PUBLISHERS.values())
