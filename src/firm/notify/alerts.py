"""Alerts to the owner from ledger events (milestone 2's minimal notifier).

`AlertingSink` wraps the run's event sink: every event still goes to the
ledger first, then `alert_for` decides whether it is worth telling the owner.
Deterministic rules, no model anywhere in the path.

- **Urgent:** a live feed stale or disconnected (new orders on that market
  are blocked), a risk halt (kill switch, drawdown, daily loss), a market
  worker that crashed, risk's heartbeat lost (no new orders until it's
  back; the owner hears when it is). The same alert is sent at most once per
  `throttle_seconds`; when the thing recovers, the owner hears that too, but
  only if they heard the alert.
- **Action:** a run started or stopped (`announce_start_stop`).

`Outbox` delivers off the trading path: emitting never waits on Telegram.
A background task sends with retries; every send, delivered or not, is a
NOTIFICATION event in the ledger with its delay from the event (the "within
60 seconds" exit check reads it). A dead channel is recorded, never raised.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from firm.core.config import NotifyConfig
from firm.core.domain import LedgerEventType, Notification, NotificationLevel
from firm.core.events import EventSink

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


class Sender(Protocol):
    def send(self, notification: Notification) -> None: ...


@dataclass(frozen=True)
class Alert:
    key: str
    level: NotificationLevel
    text: str
    recovers: str | None = None  # the key of the alert this one clears


_BAD_FEED = {"stale": "no trades for too long", "disconnected": "stream disconnected"}
_GOOD_FEED = {"fresh", "connected"}


def alert_for(
    event_type: LedgerEventType, reason: str, payload: dict[str, Any], cfg: NotifyConfig
) -> Alert | None:
    if event_type is LedgerEventType.FEED_STATUS:
        status = payload.get("status")
        key = f"feed:{payload.get('provider')}:{payload.get('symbol')}"
        if status in _BAD_FEED:
            return Alert(
                key,
                NotificationLevel.URGENT,
                f"{reason}: {_BAD_FEED[status]}; no new paper orders on it until it recovers",
            )
        if status in _GOOD_FEED:
            return Alert(f"{key}:ok", NotificationLevel.ACTION, f"{reason}: recovered", key)
        return None
    if event_type is LedgerEventType.RISK_HALT:
        scope = payload.get("scope", "?")
        return Alert(
            f"halt:{scope}:{payload.get('market')}",
            NotificationLevel.URGENT,
            f"risk halt: {reason}",
        )
    if event_type is LedgerEventType.MARKET_STATUS:
        kind = payload.get("kind")
        hb_key = f"heartbeat:{payload.get('market')}"
        if kind == "heartbeat_lost":
            return Alert(hb_key, NotificationLevel.URGENT, reason)
        if kind == "heartbeat_restored":
            return Alert(f"{hb_key}:ok", NotificationLevel.ACTION, reason, hb_key)
        if payload.get("error") or payload.get("failure"):
            return Alert(
                f"crash:{payload.get('market')}", NotificationLevel.URGENT, f"market down: {reason}"
            )
        if payload.get("kind") in ("started", "stopped") and cfg.announce_start_stop:
            return Alert(f"run:{payload.get('kind')}", NotificationLevel.ACTION, reason)
    return None


class Outbox:
    """Notifications waiting for delivery, and the worker that delivers them."""

    def __init__(
        self, notifier: Sender, sink: EventSink, cfg: NotifyConfig, *, clock: Clock = _now
    ) -> None:
        self.notifier, self.sink, self.cfg, self.clock = notifier, sink, cfg, clock
        self._queue: list[Notification] = []
        self._wake = asyncio.Event()

    def put(self, notification: Notification, source: LedgerEventType | None = None) -> None:
        self._queue.append(notification)
        self._wake.set()

    def pending(self) -> list[Notification]:
        return list(self._queue)

    async def _deliver(self, n: Notification) -> None:
        error = ""
        attempts = 0
        delivered = False
        for attempts in range(1, self.cfg.attempts + 1):
            try:
                await asyncio.to_thread(self.notifier.send, n)
                delivered = True
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                if attempts < self.cfg.attempts:
                    await asyncio.sleep(self.cfg.retry_seconds)
        latency = round((self.clock() - n.created_at).total_seconds(), 3)
        self.sink.emit(
            LedgerEventType.NOTIFICATION,
            f"{n.level.value} to owner "
            + (f"delivered in {latency:g}s" if delivered else f"NOT delivered: {error}")
            + f": {n.text}"[:200],
            {
                "notification": n.model_dump(mode="json"),
                "delivered": delivered,
                "attempts": attempts,
                "latency_seconds": latency if delivered else None,
                "error": error or None,
            },
            event_id=f"notify:{n.notification_id}",
        )

    async def _send_pending(self) -> None:
        while self._queue:
            await self._deliver(self._queue.pop(0))

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
        """Send what is left (at shutdown), within `drain_seconds`."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._send_pending(), timeout=self.cfg.drain_seconds)


class AlertQueue(Protocol):
    """Where alerts go: straight to delivery (`Outbox`) or onto the bus
    (`firm.notify.bus.BusAlerts`, which routes by the event that raised it)."""

    def put(self, notification: Notification, source: LedgerEventType | None = None) -> None: ...


class AlertingSink:
    """An `EventSink` that records first, then queues the alerts worth sending."""

    def __init__(
        self, inner: EventSink, outbox: AlertQueue, cfg: NotifyConfig, *, clock: Clock = _now
    ) -> None:
        self.inner, self.outbox, self.cfg, self.clock = inner, outbox, cfg, clock
        self._last_sent: dict[str, datetime] = {}
        self._active: set[str] = set()  # alerts the owner heard and not yet recovered

    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        self.inner.emit(event_type, reason, payload, event_id=event_id)
        alert = alert_for(event_type, reason, payload, self.cfg)
        if alert is None:
            return
        now = self.clock()
        if alert.recovers is not None:
            if alert.recovers not in self._active:
                return  # the owner never heard of a problem: no news
            self._active.discard(alert.recovers)
        elif alert.level is NotificationLevel.URGENT:
            last = self._last_sent.get(alert.key)
            if last is not None and (now - last).total_seconds() < self.cfg.throttle_seconds:
                return
            self._last_sent[alert.key] = now
            self._active.add(alert.key)
        digest = hashlib.sha256(f"{alert.key}|{now.isoformat()}|{alert.text}".encode())
        self.outbox.put(
            Notification(
                notification_id="n-" + digest.hexdigest()[:16],
                level=alert.level,
                text=alert.text,
                created_at=now,
            ),
            event_type,
        )
