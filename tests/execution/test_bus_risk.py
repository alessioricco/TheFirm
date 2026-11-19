"""The order path over the bus (golden rule 12): execution proposes on
`order.proposed`, risk answers on `risk.decision`, and execution stops
ordering when risk's heartbeat stops. In-memory bus: no server, no network."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from test_runner import Clock, runner
from test_worker import T0, Rig

from firm.bus import BusClient, MemoryBus
from firm.core.config import NotifyConfig
from firm.core.domain import LedgerEventType, NotificationLevel, RiskAction
from firm.core.messages import Service
from firm.core.messages.subjects import RiskHeartbeat
from firm.execution.bus_risk import BusRisk
from firm.notify.alerts import alert_for
from firm.risk.service import RiskService

D = Decimal
NOTIFY = NotifyConfig(
    notify="default",
    throttle_seconds=300,
    attempts=1,
    retry_seconds=0,
    drain_seconds=1,
    announce_start_stop=True,
)


def on_bus(rig: Rig, *, risk_up: bool = True, verdicts=None, risk_now=None) -> MemoryBus:
    """Put the rig's risk desk behind the bus; execution reaches it only there."""
    bus = MemoryBus()
    service = RiskService(
        rig.risk,
        BusClient(bus, Service.RISK, clock=lambda: risk_now or T0),
        verdicts={"v-1": rig.worker.d.verdict} if verdicts is None else verdicts,
        state=rig.state,
        heartbeat_seconds=1,
        max_age=timedelta(minutes=30),
        clock=lambda: risk_now or T0 + timedelta(minutes=5),
    )
    remote = BusRisk(BusClient(bus, Service.EXECUTION), timeout=0.2, on_beat=rig.hb.accept)
    rig.worker.risk = remote

    async def start() -> None:
        if risk_up:
            await service.start()
        await remote.start()

    asyncio.run(start())
    return bus


def test_the_full_order_path_runs_over_the_bus_with_the_same_ledger() -> None:
    rig = Rig()
    bus = on_bus(rig)
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    out = rig.bar(2, 50_100)
    assert out.action == "submitted"
    assert out.decision.action is RiskAction.RESIZE and out.intent.quantity == D("0.0249")
    assert (out.intent.verdict_id, out.intent.risk_decision_id) == ("v-1", out.decision.decision_id)
    assert [s for s, _, _ in bus.log] == ["order.proposed", "risk.decision"]
    assert rig.types() == [
        LedgerEventType.ORDER_PROPOSAL,
        LedgerEventType.RISK_DECISION,
        LedgerEventType.ORDER_INTENT,
        LedgerEventType.ORDER_UPDATE,
    ]


def test_no_answer_from_risk_means_no_order() -> None:
    rig = Rig()
    on_bus(rig, risk_up=False)
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    out = rig.bar(2, 50_100)
    assert out.action == "skip" and "no risk decision within 0.2s" in out.reason
    assert out.intent is None and rig.sim.fills() == []
    assert rig.types() == [LedgerEventType.ORDER_PROPOSAL]  # nothing was invented


def test_risk_rejects_a_proposal_for_a_verdict_it_does_not_know() -> None:
    rig = Rig()
    on_bus(rig, verdicts={})
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    out = rig.bar(2, 50_100)
    assert out.action == "rejected" and "unknown verdict v-1" in out.reason
    assert rig.types() == [LedgerEventType.ORDER_PROPOSAL, LedgerEventType.RISK_DECISION]


def test_risk_does_not_decide_a_proposal_long_given_up_on() -> None:
    rig = Rig()
    on_bus(rig, risk_now=T0 + timedelta(hours=2))  # e.g. replayed after a restart
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    out = rig.bar(2, 50_100)
    assert out.action == "skip" and "no risk decision" in out.reason
    assert LedgerEventType.RISK_DECISION not in rig.types()


def test_heartbeats_over_the_bus_survive_replays_and_a_risk_restart() -> None:
    rig = Rig()
    bus = MemoryBus()
    remote = BusRisk(BusClient(bus, Service.EXECUTION), timeout=1, on_beat=rig.hb.accept)
    now = {"t": T0}

    def service() -> RiskService:
        return RiskService(
            rig.risk.__class__(rig.risk.profile),  # a fresh desk: its count starts at 0
            BusClient(bus, Service.RISK),
            verdicts={},
            state=rig.state,
            heartbeat_seconds=1,
            max_age=timedelta(minutes=1),
            clock=lambda: now["t"],
        )

    async def go() -> None:
        await remote.start()
        first = service()
        for s in range(3):
            now["t"] = T0 + timedelta(seconds=s)
            await first.beat()
        assert rig.hb.alive(T0 + timedelta(seconds=5))
        assert not rig.hb.alive(T0 + timedelta(seconds=13))
        assert not rig.hb.accept(RiskHeartbeat(sequence=9, sent_at=T0))  # a replay: ignored
        now["t"] = T0 + timedelta(seconds=20)
        await service().beat()  # restarted risk: sequence 0 again, newer time
        assert rig.hb.alive(T0 + timedelta(seconds=21))

    asyncio.run(go())


def test_a_lost_heartbeat_is_recorded_and_alerted_and_so_is_its_return() -> None:
    rig, clock = Rig(), Clock()
    r = runner(rig, clock)
    r.risk_heartbeat = None  # heartbeats arrive on the bus, or not at all
    lost = lambda: [e for e in rig.sink.events if e[2].get("kind") == "heartbeat_lost"]  # noqa: E731

    r.watch_heartbeat(T0)
    r.watch_heartbeat(T0 + timedelta(seconds=9))
    assert lost() == []  # not yet: risk has the timeout to say hello
    r.watch_heartbeat(T0 + timedelta(seconds=11))
    r.watch_heartbeat(T0 + timedelta(seconds=12))
    (event,) = lost()  # once, not on every tick
    rig.hb.accept(RiskHeartbeat(sequence=0, sent_at=T0 + timedelta(seconds=13)))
    r.watch_heartbeat(T0 + timedelta(seconds=13))
    back = [e for e in rig.sink.events if e[2].get("kind") == "heartbeat_restored"]
    assert len(back) == 1

    alert = alert_for(event[0], event[1], event[2], NOTIFY)
    assert alert.level is NotificationLevel.URGENT and "risk heartbeat lost" in alert.text
    ok = alert_for(back[0][0], back[0][1], back[0][2], NOTIFY)
    assert ok.recovers == alert.key
