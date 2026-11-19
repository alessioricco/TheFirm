"""Heartbeat monitoring: execution halts when it stops hearing the risk desk.

The order path stays together (golden rule 12): if risk's heartbeat goes
quiet for longer than `timeout`, `alive()` turns false and execution must
stop placing orders until it hears risk again.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from firm.core.messages.subjects import RiskHeartbeat


class HeartbeatMonitor:
    def __init__(self, timeout: timedelta) -> None:
        if timeout <= timedelta(0):
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self._last: RiskHeartbeat | None = None

    def beat(self, heartbeat: RiskHeartbeat) -> None:
        if self._last is not None and (
            heartbeat.sequence <= self._last.sequence or heartbeat.sent_at < self._last.sent_at
        ):
            raise ValueError("heartbeat went backwards (stale or replayed)")
        self._last = heartbeat

    def accept(self, heartbeat: RiskHeartbeat) -> bool:
        """`beat` for heartbeats from the bus: never raises.

        An older or repeated beat (a replay) is ignored and returns False. A
        newer beat with a lower sequence means the risk desk restarted (its
        count starts again), so it is taken as the new reference."""
        last = self._last
        if last is not None and heartbeat.sent_at <= last.sent_at:
            return False
        self._last = heartbeat
        return True

    def alive(self, now: datetime) -> bool:
        return self._last is not None and now - self._last.sent_at <= self.timeout
