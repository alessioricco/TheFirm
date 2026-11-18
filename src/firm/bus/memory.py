"""An in-memory bus with JetStream's semantics, for tests and single-process runs.

What it keeps from JetStream:
- one stream: every published message is kept, in order;
- publish dedupe: a second message with the same ID is dropped (JetStream's
  `Nats-Msg-Id` window), so a publisher may retry safely;
- durable consumers: a consumer name resumes after the last message it
  acknowledged, even after its subscription was closed;
- a handler that raises does not acknowledge: delivery to that consumer
  stops there, and the message comes again on its next subscribe or the
  next publish.
Subject patterns use NATS wildcards: `*` is one token, `>` the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from firm.bus.client import RawHandler


def subject_matches(pattern: str, subject: str) -> bool:
    p, s = pattern.split("."), subject.split(".")
    for i, token in enumerate(p):
        if token == ">":
            return len(s) > i
        if i >= len(s) or (token != "*" and token != s[i]):
            return False
    return len(p) == len(s)


@dataclass
class _Consumer:
    pattern: str
    cursor: int = 0  # index of the next message to deliver
    handler: RawHandler | None = None
    busy: bool = False


@dataclass
class MemorySubscription:
    bus: MemoryBus
    durable: str

    async def close(self) -> None:
        self.bus._consumers[self.durable].handler = None


@dataclass
class MemoryBus:
    log: list[tuple[str, bytes, str]] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)
    _consumers: dict[str, _Consumer] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (durable, error)

    async def publish(self, subject: str, data: bytes, *, message_id: str) -> None:
        if message_id in self._ids:
            return
        self._ids.add(message_id)
        self.log.append((subject, data, message_id))
        for name in list(self._consumers):
            await self._pump(name)

    async def subscribe(
        self, pattern: str, *, durable: str, handler: RawHandler
    ) -> MemorySubscription:
        consumer = self._consumers.get(durable)
        if consumer is None:
            consumer = self._consumers[durable] = _Consumer(pattern)
        elif consumer.pattern != pattern:
            raise ValueError(f"consumer {durable} is bound to {consumer.pattern}, not {pattern}")
        consumer.handler = handler
        await self._pump(durable)
        return MemorySubscription(self, durable)

    async def _pump(self, durable: str) -> None:
        c = self._consumers[durable]
        if c.busy:  # a handler that publishes: the outer loop delivers the rest
            return
        c.busy = True
        try:
            while c.handler is not None and c.cursor < len(self.log):
                subject, data, _ = self.log[c.cursor]
                if subject_matches(c.pattern, subject):
                    try:
                        await c.handler(subject, data)
                    except Exception as exc:
                        self.failures.append((durable, f"{type(exc).__name__}: {exc}"))
                        return  # not acknowledged: stays at this message
                c.cursor += 1
        finally:
            c.busy = False

    async def close(self) -> None:
        for c in self._consumers.values():
            c.handler = None
