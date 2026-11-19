"""Alerts over the bus: each desk publishes on its own alert subject, the
notifier reads `alert.>` and delivers; a bus outage is recorded, never raised."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from firm.bus import BusClient, MemoryBus
from firm.core.config import NotifyConfig
from firm.core.domain import LedgerEventType, Notification, NotificationLevel
from firm.core.events import CollectingSink
from firm.core.messages import Service
from firm.notify.alerts import AlertingSink, Outbox
from firm.notify.bus import BusAlerts, NotifierService

T = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
CFG = NotifyConfig(
    notify="default",
    throttle_seconds=300,
    attempts=2,
    retry_seconds=0,
    drain_seconds=2,
    announce_start_stop=True,
)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, n: Notification) -> None:
        self.sent.append(n)


def test_alerts_travel_from_their_desk_to_the_owner() -> None:
    bus, inbox = MemoryBus(), CollectingSink()
    notifier = FakeNotifier()
    clients = {s: BusClient(bus, s) for s in (Service.RISK, Service.EXECUTION, Service.NOTIFIER)}
    outbox = Outbox(notifier, inbox, CFG, clock=lambda: T)
    alerts = BusAlerts(clients, inbox, CFG)
    sink = AlertingSink(inbox, alerts, CFG, clock=lambda: T)
    service = NotifierService(clients[Service.NOTIFIER], outbox)

    async def go() -> None:
        await service.start()
        sink.emit(LedgerEventType.RISK_HALT, "drawdown: market kill switch", {"scope": "market"})
        sink.emit(
            LedgerEventType.FEED_STATUS,
            "coinbase_ws BTC-USD stale",
            {"provider": "coinbase_ws", "symbol": "BTC-USD", "status": "stale"},
        )
        sink.emit(
            LedgerEventType.MARKET_STATUS,
            "crypto: risk heartbeat lost for over 10s: no new orders",
            {"kind": "heartbeat_lost", "market": "crypto"},
        )
        sink.emit(LedgerEventType.MARKET_STATUS, "crypto started", {"kind": "started"})
        await alerts.drain()
        assert await service.settle(alerts.published, timeout=1)
        await outbox.drain()

    asyncio.run(go())
    published = [(s, e) for s, e, _ in bus.log]
    assert [s for s, _ in published] == [
        "alert.urgent.risk",
        "alert.urgent.execution",
        "alert.urgent.execution",
        "alert.action.execution",
    ]
    assert [n.level for n in notifier.sent] == [NotificationLevel.URGENT] * 3 + [
        NotificationLevel.ACTION
    ]
    delivered = [e for e in inbox.events if e[0] is LedgerEventType.NOTIFICATION]
    assert len(delivered) == 4 and all(e[2]["delivered"] for e in delivered)


class DeadBus(MemoryBus):
    async def publish(self, subject: str, data: bytes, *, message_id: str) -> None:
        raise ConnectionError("bus down")


def test_a_bus_outage_is_recorded_as_an_undelivered_alert() -> None:
    bus, inbox = DeadBus(), CollectingSink()
    alerts = BusAlerts({s: BusClient(bus, s) for s in Service}, inbox, CFG)
    sink = AlertingSink(inbox, alerts, CFG, clock=lambda: T)
    sink.emit(LedgerEventType.RISK_HALT, "global kill switch", {"scope": "global"})
    asyncio.run(alerts.drain())  # does not raise
    (rec,) = [e for e in inbox.events if e[0] is LedgerEventType.NOTIFICATION]
    assert rec[2]["delivered"] is False and "bus down" in rec[2]["error"]
