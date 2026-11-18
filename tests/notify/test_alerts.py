"""Urgent alerts from ledger events: which events, throttling, delivery off the
trading path, and the ledger record of every send (with its delay)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from firm.core.config import NotifyConfig
from firm.core.domain import LedgerEventType, Notification, NotificationLevel
from firm.core.events import CollectingSink
from firm.notify.alerts import AlertingSink, Outbox, alert_for

T = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CFG = NotifyConfig(
    notify="default",
    throttle_seconds=300,
    attempts=3,
    retry_seconds=0,
    drain_seconds=5,
    announce_start_stop=True,
)


class Clock:
    def __init__(self) -> None:
        self.now = T

    def __call__(self) -> datetime:
        return self.now


class FakeNotifier:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[Notification] = []
        self.fail_times = fail_times

    def send(self, n: Notification) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("telegram down")
        self.sent.append(n)


def feed(status: str) -> tuple:
    return (
        LedgerEventType.FEED_STATUS,
        f"coinbase_ws BTC-USD {status}",
        {"provider": "coinbase_ws", "symbol": "BTC-USD", "status": status, "detail": "d"},
    )


def test_which_events_become_alerts() -> None:
    assert alert_for(*feed("stale"), CFG).level is NotificationLevel.URGENT
    assert alert_for(*feed("disconnected"), CFG).level is NotificationLevel.URGENT
    assert alert_for(*feed("fresh"), CFG).recovers == alert_for(*feed("stale"), CFG).key
    assert alert_for(*feed("connected"), CFG).recovers is not None
    halt = alert_for(
        LedgerEventType.RISK_HALT,
        "drawdown 10% >= 10%: market kill switch",
        {"scope": "market"},
        CFG,
    )
    assert halt.level is NotificationLevel.URGENT and "kill switch" in halt.text
    crash = alert_for(
        LedgerEventType.MARKET_STATUS, "market crypto stopped: boom", {"error": "boom"}, CFG
    )
    assert crash.level is NotificationLevel.URGENT
    started = alert_for(LedgerEventType.MARKET_STATUS, "crypto started", {"kind": "started"}, CFG)
    assert started.level is NotificationLevel.ACTION
    quiet = CFG.model_copy(update={"announce_start_stop": False})
    assert (
        alert_for(LedgerEventType.MARKET_STATUS, "crypto started", {"kind": "started"}, quiet)
        is None
    )
    assert alert_for(LedgerEventType.FILL, "filled", {}, CFG) is None
    assert alert_for(LedgerEventType.ORDER_PROPOSAL, "buy", {}, CFG) is None


def test_alerts_are_throttled_and_recoveries_follow_only_a_sent_alert() -> None:
    clock = Clock()
    inner = CollectingSink()
    outbox = Outbox(FakeNotifier(), inner, CFG, clock=clock)
    sink = AlertingSink(inner, outbox, CFG, clock=clock)

    sink.emit(*feed("fresh"))  # nothing was wrong: no recovery message
    sink.emit(*feed("stale"))
    clock.now += timedelta(seconds=10)
    sink.emit(*feed("fresh"))  # recovery of a sent alert
    sink.emit(*feed("stale"))  # within the throttle: held back
    sink.emit(*feed("fresh"))  # its alert was held back: no recovery either
    clock.now += timedelta(seconds=301)
    sink.emit(*feed("stale"))  # throttle over

    texts = [n.text for n in outbox.pending()]
    assert len(texts) == 3
    assert "stale" in texts[0] and "recovered" in texts[1] and "stale" in texts[2]
    assert len(inner.events) == 6  # every event still reaches the ledger


def test_delivery_retries_and_records_every_send_with_its_delay() -> None:
    clock = Clock()
    inner = CollectingSink()
    notifier = FakeNotifier(fail_times=1)
    outbox = Outbox(notifier, inner, CFG, clock=clock)
    sink = AlertingSink(inner, outbox, CFG, clock=clock)
    sink.emit(*feed("stale"))
    clock.now += timedelta(seconds=2)
    asyncio.run(outbox.drain())

    assert len(notifier.sent) == 1
    (rec,) = [e for e in inner.events if e[0] is LedgerEventType.NOTIFICATION]
    payload = rec[2]
    assert payload["delivered"] is True and payload["attempts"] == 2
    assert payload["latency_seconds"] == 2.0
    assert payload["notification"]["level"] == "urgent"


def test_a_dead_channel_is_recorded_and_never_raises_into_trading() -> None:
    clock = Clock()
    inner = CollectingSink()
    outbox = Outbox(FakeNotifier(fail_times=99), inner, CFG, clock=clock)
    AlertingSink(inner, outbox, CFG, clock=clock).emit(*feed("stale"))
    asyncio.run(outbox.drain())  # does not raise
    (rec,) = [e for e in inner.events if e[0] is LedgerEventType.NOTIFICATION]
    assert rec[2]["delivered"] is False and rec[2]["attempts"] == 3
    assert "telegram down" in rec[2]["error"]


def test_the_outbox_worker_sends_while_running_and_drains_on_stop() -> None:
    clock = Clock()
    inner = CollectingSink()
    notifier = FakeNotifier()
    outbox = Outbox(notifier, inner, CFG, clock=clock)
    sink = AlertingSink(inner, outbox, CFG, clock=clock)

    async def main() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(outbox.run(stop))
        sink.emit(*feed("stale"))
        for _ in range(50):
            if notifier.sent:
                break
            await asyncio.sleep(0.01)
        sink.emit(LedgerEventType.MARKET_STATUS, "crypto stopped", {"kind": "stopped"})
        stop.set()
        await task
        await outbox.drain()

    asyncio.run(main())
    assert [n.level for n in notifier.sent] == [NotificationLevel.URGENT, NotificationLevel.ACTION]
