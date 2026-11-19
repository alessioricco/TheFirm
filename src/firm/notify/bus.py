"""Alerts over the bus: desks publish them, the notifier delivers them.

`BusAlerts` is the `AlertQueue` the desks' `AlertingSink` feeds: each alert
is published on its raising desk's own subject (`alert.urgent.risk` for a
risk halt; `alert.urgent.execution` / `alert.action.execution` for the rest:
feeds, heartbeat, crashes, start/stop), so every alert subject keeps exactly
one publisher. Publishing runs in a background task; a publish that keeps
failing is recorded in the ledger as an undelivered NOTIFICATION, never
raised into trading.

`NotifierService` reads `alert.>` through its own consumer and hands each
alert to the `Outbox`, which delivers it and records the delay from the
event that raised it (bus hop included).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from firm.bus.client import BusClient
from firm.core.config import NotifyConfig
from firm.core.domain import LedgerEventType, Notification, NotificationLevel
from firm.core.events import EventSink
from firm.core.messages import Envelope, Service, Subject
from firm.notify.alerts import Outbox

log = logging.getLogger(__name__)

ALERTS = "notifier-alerts"


def route(level: NotificationLevel, source: LedgerEventType | None) -> tuple[Service, Subject]:
    if source is LedgerEventType.RISK_HALT:
        return Service.RISK, Subject.ALERT_URGENT_RISK
    if level is NotificationLevel.URGENT:
        return Service.EXECUTION, Subject.ALERT_URGENT_EXECUTION
    return Service.EXECUTION, Subject.ALERT_ACTION_EXECUTION


class BusAlerts:
    def __init__(
        self, clients: dict[Service, BusClient], sink: EventSink, cfg: NotifyConfig
    ) -> None:
        self.clients, self.sink, self.cfg = clients, sink, cfg
        self._queue: list[tuple[Notification, LedgerEventType | None]] = []
        self._wake = asyncio.Event()
        self.published: set[str] = set()  # notification IDs now on the bus

    def put(self, notification: Notification, source: LedgerEventType | None = None) -> None:
        self._queue.append((notification, source))
        self._wake.set()

    async def _publish(self, n: Notification, source: LedgerEventType | None) -> None:
        service, subject = route(n.level, source)
        error = ""
        for attempt in range(1, self.cfg.attempts + 1):
            try:
                await self.clients[service].publish(subject, n, message_id=n.notification_id)
                self.published.add(n.notification_id)
                return
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                if attempt < self.cfg.attempts:
                    await asyncio.sleep(self.cfg.retry_seconds)
        self.sink.emit(
            LedgerEventType.NOTIFICATION,
            f"{n.level.value} to owner NOT delivered (bus): {error}: {n.text}"[:200],
            {
                "notification": n.model_dump(mode="json"),
                "delivered": False,
                "attempts": self.cfg.attempts,
                "latency_seconds": None,
                "error": f"not published to the bus: {error}",
            },
            event_id=f"notify:{n.notification_id}",
        )

    async def _send_pending(self) -> None:
        while self._queue:
            await self._publish(*self._queue.pop(0))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._wake.clear()
            await self._send_pending()
            waiter = asyncio.create_task(self._wake.wait())
            stopper = asyncio.create_task(stop.wait())
            await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            for t in (waiter, stopper):
                t.cancel()

    async def drain(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._send_pending(), timeout=self.cfg.drain_seconds)


class NotifierService:
    def __init__(self, client: BusClient, outbox: Outbox) -> None:
        if client.service is not Service.NOTIFIER:
            raise ValueError("the notifier reads alerts as the notifier")
        self.client, self.outbox = client, outbox
        self.received: set[str] = set()

    async def start(self) -> None:
        await self.client.subscribe(ALERTS, self._on_alert)

    async def _on_alert(self, env: Envelope) -> None:
        n = env.typed_payload()
        assert isinstance(n, Notification)
        self.received.add(n.notification_id)
        self.outbox.put(n)

    async def settle(self, expected: set[str], *, timeout: float) -> bool:
        """Wait (at shutdown) until every alert in `expected` has arrived."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not expected <= self.received:
            if loop.time() > end:
                return False
            await asyncio.sleep(0.05)
        return True
