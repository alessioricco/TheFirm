"""Check the bus against a real nats-server (the unit tests use an in-memory bus).

Starts a throwaway server exactly as `firm bus up` does (generated config, one
login per service, the declared consumers created by the admin) on a free
localhost port, with its state in a temporary folder, then checks:

- every subject round-trips from its owner, each publisher on its own login;
  a retried publish lands once;
- the order path: execution's proposal reaches risk, risk's decision comes
  back; risk's heartbeats reach execution; a durable consumer resumes after
  a restart;
- alerts: published by a desk, read by the notifier, handed to delivery; a
  failing handler gets the message again;
- the server itself enforces the org chart: an agent's login can't publish
  an order (even bypassing the client's own check), can't read another
  desk's deliveries, can't create or look up a consumer; a service can't
  delete or purge the stream; a wrong password is refused.

    uv run python scripts/bus_smoke.py
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from nats.js import api

from bus_samples import SAMPLES
from factories import verdict
from firm.bus import BusClient, encode
from firm.bus.auth import ADMIN
from firm.bus.consumers import CONSUMERS
from firm.bus.nats import NatsBus
from firm.bus.server import BusServerError, start
from firm.core.config import BusConfig, NotifyConfig, load_bus, load_risk
from firm.core.domain import (
    LedgerEventType,
    Notification,
    NotificationLevel,
    OrderProposal,
    RiskAction,
)
from firm.core.events import CollectingSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.core.messages import PUBLISHERS, Envelope, Service, Subject, wrap
from firm.execution.bus_risk import BusRisk
from firm.notify.alerts import Outbox
from firm.notify.bus import BusAlerts, NotifierService
from firm.risk.desk import RiskDesk
from firm.risk.service import RiskService

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def wait_for(cond, seconds: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + seconds
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError("timed out waiting for delivery")
        await asyncio.sleep(0.02)


async def refused(coro) -> str:  # type: ignore[no-untyped-def]
    try:
        await asyncio.wait_for(coro, timeout=3)
    except Exception as exc:
        return type(exc).__name__
    raise AssertionError("the server accepted it")


async def check(cfg: BusConfig, state: Path) -> None:
    buses: dict[str, NatsBus] = {}

    async def bus(user: Service | str) -> NatsBus:
        name = user.value if isinstance(user, Service) else user
        if name not in buses:
            buses[name] = await NatsBus.for_service(cfg, state, user)
        return buses[name]

    admin = await bus(ADMIN)
    await admin.ensure_stream()
    created = await admin.ensure_consumers()
    assert sorted(created) == sorted(CONSUMERS)
    assert await admin.ensure_consumers() == []  # existing ones are left as they are
    print(f"ok  the admin created the declared consumers ({', '.join(sorted(created))})")

    # the admin watches everything (no acks, so it needs no ack permission)
    seen: list[str] = []

    async def watch(msg) -> None:  # type: ignore[no-untyped-def]
        seen.append(msg.subject)

    await admin.js.subscribe(
        ">",
        stream=cfg.stream,
        cb=watch,
        config=api.ConsumerConfig(ack_policy=api.AckPolicy.NONE),
    )
    for subject in Subject:
        owner = PUBLISHERS[subject]
        assert owner is not None
        await BusClient(await bus(owner), owner).publish(
            subject, SAMPLES[subject], message_id=f"m-{subject}"
        )
    await wait_for(lambda: len(seen) == len(Subject))
    print(f"ok  every subject published from its owner's own login ({len(seen)} subjects)")
    risk_client = BusClient(await bus(Service.RISK), Service.RISK)
    beat = SAMPLES[Subject.RISK_HEARTBEAT]
    await risk_client.publish(Subject.RISK_HEARTBEAT, beat, message_id="m-risk.heartbeat")
    await asyncio.sleep(0.3)
    assert len(seen) == len(Subject)
    print("ok  a retried publish lands once (Nats-Msg-Id)")

    # --- the order path over the real server
    profile = load_risk(ROOT / "config" / "risk" / "default.yaml")
    risk_sink = CollectingSink()
    now = datetime.now(UTC)

    async def no_state(at):  # type: ignore[no-untyped-def]
        return None

    service = RiskService(
        RiskDesk(profile, risk_sink),
        risk_client,
        verdicts={},
        state=no_state,
        heartbeat_seconds=0.2,
        max_age=timedelta(minutes=1),
    )
    monitor = HeartbeatMonitor(timedelta(seconds=2))
    exe_client = BusClient(await bus(Service.EXECUTION), Service.EXECUTION)
    remote = BusRisk(exe_client, timeout=3, on_beat=monitor.accept)
    await service.start()
    await remote.start()
    proposal = SAMPLES[Subject.ORDER_PROPOSED]
    assert isinstance(proposal, OrderProposal)
    proposal = proposal.model_copy(update={"proposal_id": "p-smoke", "created_at": now})
    decision = await remote.decide(verdict(), proposal, None)  # type: ignore[arg-type]
    assert decision.action is RiskAction.REJECT and "unknown verdict" in decision.reason
    assert risk_sink.events[0][0] is LedgerEventType.RISK_DECISION
    print("ok  execution's proposal reaches risk and risk's decision comes back")

    stop = asyncio.Event()
    beating = asyncio.create_task(service.run(stop))
    await wait_for(lambda: monitor.alive(datetime.now(UTC)))
    stop.set()
    await beating
    print("ok  risk's heartbeats reach execution")

    # --- durable resume: execution restarts, misses nothing, gets nothing twice
    beats: list[str] = []

    async def hb(env: Envelope) -> None:
        beats.append(env.message_id)

    await exe_client.close()  # execution "restarts"
    await risk_client.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-2")
    await risk_client.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-3")
    await BusClient(await bus(Service.EXECUTION), Service.EXECUTION).subscribe(
        "execution-heartbeat", hb
    )
    await wait_for(lambda: beats[-2:] == ["b-2", "b-3"])
    assert beats.count("b-2") == 1
    print("ok  a durable consumer resumes after a restart: nothing lost, nothing twice")

    # --- alerts: execution publishes, the notifier delivers; a failure is retried
    notify_cfg = NotifyConfig(
        notify="default",
        throttle_seconds=0,
        attempts=1,
        retry_seconds=0,
        drain_seconds=3,
        announce_start_stop=True,
    )
    calls: list[str] = []

    class Phone:
        def send(self, n: Notification) -> None:
            calls.append(n.notification_id)

    outbox = Outbox(Phone(), CollectingSink(), notify_cfg)
    notifier = NotifierService(BusClient(await bus(Service.NOTIFIER), Service.NOTIFIER), outbox)
    failures = {"left": 1}
    original = outbox.put

    def put_failing_once(n, source=None):  # type: ignore[no-untyped-def]
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("notifier busy")
        original(n, source)

    outbox.put = put_failing_once  # type: ignore[method-assign]
    await notifier.start()
    alerts = BusAlerts(
        {Service.EXECUTION: BusClient(await bus(Service.EXECUTION), Service.EXECUTION)},
        CollectingSink(),
        notify_cfg,
    )
    alerts.put(
        Notification(
            notification_id="n-smoke",
            level=NotificationLevel.URGENT,
            text="feed stale",
            created_at=datetime.now(UTC),
        ),
        LedgerEventType.FEED_STATUS,
    )
    await alerts.drain()
    await wait_for(lambda: "n-smoke" in notifier.received, seconds=cfg.redeliver_seconds + 5)
    await outbox.drain()
    # (the sample alerts published above arrive too; one of them hit the failure)
    assert calls.count("n-smoke") == 1 and failures["left"] == 0
    print("ok  an alert reaches the notifier and is delivered; a failing handler gets it again")

    # --- the server enforces the org chart, not only the client
    before = (await admin.js.stream_info(cfg.stream)).state.messages
    research = await bus(Service.RESEARCH)
    order = encode(
        wrap(
            message_id="forged-1",
            sender=Service.EXECUTION,
            subject=Subject.ORDER_INTENT,
            payload=SAMPLES[Subject.ORDER_INTENT],
            timestamp=now,
        )
    )
    research.cfg = cfg.model_copy(update={"publish_timeout_seconds": 1.0})
    why = await refused(research.publish("order.intent", order, message_id="forged-1"))
    after = (await admin.js.stream_info(cfg.stream)).state.messages
    assert after == before
    print(f"ok  the research login can't publish order.intent, even raw ({why}; stream unchanged)")

    snooped: list[str] = []

    async def snoop(msg) -> None:  # type: ignore[no-untyped-def]
        snooped.append(msg.subject)

    await research.nc.subscribe("_INBOX.risk.>", cb=snoop)  # risk's deliveries
    await research.nc.subscribe("order.proposed", cb=snoop)  # around JetStream
    seen_before = len(seen)
    p2 = proposal.model_copy(update={"proposal_id": "p-snoop"})
    await BusClient(await bus(Service.EXECUTION), Service.EXECUTION).publish(
        Subject.ORDER_PROPOSED, p2, message_id="p-snoop"
    )
    await wait_for(lambda: len(seen) > seen_before)
    await asyncio.sleep(0.3)
    assert snooped == []
    print("ok  the research login can't read risk's deliveries nor firm subjects directly")

    why = await refused(
        research.js.add_consumer(cfg.stream, api.ConsumerConfig(durable_name="squat"))
    )
    why2 = await refused(research.js.consumer_info(cfg.stream, "risk-proposals"))
    print(f"ok  the research login can't create ({why}) or look up ({why2}) a consumer")

    risk_bus = await bus(Service.RISK)
    why = await refused(risk_bus.js.delete_stream(cfg.stream))
    why2 = await refused(risk_bus.js.purge_stream(cfg.stream))
    print(f"ok  a service login can't delete ({why}) or purge ({why2}) the stream")

    why = await refused(NatsBus.connect(cfg, user="execution", password="wrong"))
    print(f"ok  a wrong password is refused ({why})")

    for b in buses.values():
        await b.close()


def main() -> int:
    port = free_port()
    cfg = load_bus(ROOT / "config").model_copy(update={"port": port, "redeliver_seconds": 0.5})
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        try:
            proc = start(cfg, state, state / "logs" / "bus.log")
        except BusServerError as exc:
            print(f"error: {exc}")
            return 1
        try:
            asyncio.run(check(cfg, state))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    print("bus smoke: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
