"""The bus as desks use it: typed messages, publish permissions, idempotent
and durable delivery. Runs on the in-memory bus (no server, no network);
`scripts/bus_smoke.py` runs the same behaviour against a real nats-server."""

from __future__ import annotations

import asyncio
import json

import pytest

from bus_samples import SAMPLES
from factories import T0, order_intent
from firm.bus import BusClient, BusError, MemoryBus, decode, encode, subject_matches
from firm.bus.consumers import CONSUMERS, Consumer
from firm.core.messages import PAYLOADS, PUBLISHERS, Envelope, Service, Subject, wrap


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


class Inbox:
    def __init__(self) -> None:
        self.got: list[Envelope] = []

    async def __call__(self, env: Envelope) -> None:
        self.got.append(env)


ALL = Consumer("ledger-all", Service.LEDGER, ">")
HB = Consumer("exe-hb", Service.EXECUTION, "risk.heartbeat")
HB_OTHER = Consumer("exe-hb-other", Service.EXECUTION, "risk.heartbeat")


def client(bus: MemoryBus, service: Service) -> BusClient:
    return BusClient(bus, service, clock=lambda: T0)


def test_every_subject_has_a_sample_of_its_schema() -> None:
    assert set(SAMPLES) == set(Subject)
    assert all(type(SAMPLES[s]) is PAYLOADS[s] for s in Subject)


@pytest.mark.parametrize("subject", list(Subject))
def test_contract_every_subject_round_trips_from_its_owner(subject: Subject) -> None:
    async def main() -> list[Envelope]:
        bus, inbox = MemoryBus(), Inbox()
        owner = PUBLISHERS[subject]
        assert owner is not None
        await client(bus, Service.LEDGER).subscribe(ALL, inbox)
        await client(bus, owner).publish(subject, SAMPLES[subject], message_id="m-1")
        return inbox.got

    (env,) = run(main())
    assert env.subject is subject and env.sender is PUBLISHERS[subject]
    assert env.typed_payload() == SAMPLES[subject]


@pytest.mark.parametrize("subject", list(Subject))
def test_only_the_owner_may_publish(subject: Subject) -> None:
    async def main() -> None:
        bus = MemoryBus()
        for service in Service:
            if service is PUBLISHERS[subject]:
                continue
            with pytest.raises(BusError, match="permission denied"):
                await client(bus, service).publish(subject, SAMPLES[subject], message_id="m")
        assert bus.log == []

    run(main())


def test_forged_malformed_and_misrouted_messages_are_rejected() -> None:
    good = encode(
        wrap(
            message_id="m-1",
            sender=Service.EXECUTION,
            subject=Subject.ORDER_INTENT,
            payload=order_intent(),
            timestamp=T0,
        )
    )
    forged = json.loads(good)
    forged["sender"] = "research"  # an agent claiming an order
    rejected: list[tuple[str, str]] = []

    async def main() -> Inbox:
        bus, inbox = MemoryBus(), Inbox()
        c = BusClient(bus, Service.LEDGER, on_reject=lambda s, why: rejected.append((s, why)))
        await c.subscribe(ALL, inbox)
        await bus.publish("order.intent", json.dumps(forged).encode(), message_id="x1")
        await bus.publish("order.intent", b"not json", message_id="x2")
        await bus.publish("fill.executed", good, message_id="x3")  # names order.intent
        await bus.publish("order.intent", good, message_id="x4")
        assert c.rejected == 3
        return inbox

    inbox = run(main())
    assert [e.message_id for e in inbox.got] == ["m-1"]
    assert "permission denied" in rejected[0][1]
    assert "arrived on fill.executed" in rejected[2][1]


def test_a_message_is_handled_once_even_when_sent_twice() -> None:
    async def main() -> Inbox:
        bus, inbox = MemoryBus(), Inbox()
        execution = client(bus, Service.EXECUTION)
        await client(bus, Service.LEDGER).subscribe(
            Consumer("ledger-fills", Service.LEDGER, "fill.>"), inbox
        )
        fill = SAMPLES[Subject.FILL_EXECUTED]
        await execution.publish(Subject.FILL_EXECUTED, fill, message_id="f-1")
        await execution.publish(Subject.FILL_EXECUTED, fill, message_id="f-1")  # retry
        # the same envelope arriving again under another transport ID (a replay)
        await bus.publish("fill.executed", bus.log[0][1], message_id="replay")
        return inbox

    assert len(run(main()).got) == 1


def test_durable_consumers_resume_where_they_stopped() -> None:
    beat = SAMPLES[Subject.RISK_HEARTBEAT]

    async def main() -> tuple[Inbox, Inbox, Inbox]:
        bus = MemoryBus()
        risk = client(bus, Service.RISK)
        first, again, fresh = Inbox(), Inbox(), Inbox()
        sub = await client(bus, Service.EXECUTION).subscribe(HB, first)
        await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-1")
        await sub.close()  # execution restarts...
        await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-2")
        await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-3")
        await client(bus, Service.EXECUTION).subscribe(HB, again)
        await client(bus, Service.EXECUTION).subscribe(HB_OTHER, fresh)
        return first, again, fresh

    first, again, fresh = run(main())
    assert [e.message_id for e in first.got] == ["b-1"]
    assert [e.message_id for e in again.got] == ["b-2", "b-3"]  # nothing lost, nothing twice
    assert [e.message_id for e in fresh.got] == ["b-1", "b-2", "b-3"]


def test_a_failing_handler_gets_the_message_again() -> None:
    calls: list[str] = []

    async def flaky(env: Envelope) -> None:
        calls.append(env.message_id)
        if len(calls) == 1:
            raise RuntimeError("ledger busy")

    async def main() -> MemoryBus:
        bus = MemoryBus()
        execution = client(bus, Service.EXECUTION)
        await client(bus, Service.LEDGER).subscribe(
            Consumer("ledger-fills", Service.LEDGER, "fill.executed"), flaky
        )
        fill = SAMPLES[Subject.FILL_EXECUTED]
        await execution.publish(Subject.FILL_EXECUTED, fill, message_id="f-1")
        await execution.publish(Subject.FILL_EXECUTED, fill, message_id="f-2")
        return bus

    bus = run(main())
    assert calls == ["f-1", "f-1", "f-2"]  # retried, in order, then the next
    assert bus.failures == [("ledger-fills", "RuntimeError: ledger busy")]


def test_decode_refuses_what_encode_did_not_make() -> None:
    with pytest.raises(BusError, match="invalid message"):
        decode("order.intent", b"{}")


@pytest.mark.parametrize(
    ("pattern", "subject", "match"),
    [
        (">", "order.intent", True),
        ("alert.>", "alert.urgent.risk", True),
        ("alert.>", "alert", False),
        ("alert.*", "alert.urgent.risk", False),
        ("alert.*.risk", "alert.urgent.risk", True),
        ("risk.heartbeat", "risk.heartbeat", True),
        ("risk.heartbeat", "risk.halt", False),
        ("risk", "risk.halt", False),
    ],
)
def test_subject_wildcards(pattern: str, subject: str, match: bool) -> None:
    assert subject_matches(pattern, subject) is match


def test_a_service_reads_only_through_its_own_consumers() -> None:
    async def main() -> None:
        bus = MemoryBus()
        with pytest.raises(BusError, match="belongs to"):
            await client(bus, Service.RESEARCH).subscribe("risk-proposals", Inbox())
        with pytest.raises(KeyError):
            await client(bus, Service.RISK).subscribe("not-declared", Inbox())

    run(main())


def test_a_new_consumer_starting_new_skips_the_past() -> None:
    async def main() -> Inbox:
        bus, inbox = MemoryBus(), Inbox()
        risk = client(bus, Service.RISK)
        beat = SAMPLES[Subject.RISK_HEARTBEAT]
        await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="old")
        await client(bus, Service.EXECUTION).subscribe("execution-heartbeat", inbox)
        await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="new")
        return inbox

    assert [e.message_id for e in run(main()).got] == ["new"]


@pytest.mark.parametrize("name", sorted(CONSUMERS))
def test_contract_every_declared_consumer_reads_real_subjects(name: str) -> None:
    c = CONSUMERS[name]
    assert c.name == name
    assert any(subject_matches(c.filter, s.value) for s in Subject)
    assert c.deliver_subject.startswith(f"_INBOX.{c.service.value}.")
