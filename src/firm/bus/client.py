"""A desk's connection to the bus: typed, permission-checked, idempotent.

Each service gets one `BusClient` bound to its own identity. Publishing
wraps a typed payload in an `Envelope`, which refuses a subject the service
does not own (the code-side copy of the bus permissions, golden rule 12).
Subscribing decodes every message back into a validated `Envelope`:

- a message that fails its schema, claims a sender that may not publish its
  subject, or arrives on a subject other than the one it names is rejected
  (counted and reported, never handed to the desk, never retried);
- a message already handled (same `message_id`) is skipped, so a replay
  after a restart never makes a desk act twice;
- a handler that raises leaves the message unacknowledged: the transport
  delivers it again, and it is not marked as handled.

A service reads only through its own declared consumers (`firm.bus.consumers`).

The transport underneath is NATS JetStream in a running firm
(`firm.bus.nats`) and an in-memory bus in tests (`firm.bus.memory`).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from firm.bus.consumers import CONSUMERS, Consumer
from firm.core.base import FirmModel
from firm.core.messages import Envelope, Service, Subject, may_publish, wrap

log = logging.getLogger(__name__)

RawHandler = Callable[[str, bytes], Awaitable[None]]
Handler = Callable[[Envelope], Awaitable[None]]
Clock = Callable[[], datetime]


class BusError(RuntimeError):
    pass


class Subscription(Protocol):
    async def close(self) -> None: ...


class Transport(Protocol):
    """Moves bytes. Durable consumers resume where they stopped; a raising
    handler means "not acknowledged": the message comes again later."""

    async def publish(self, subject: str, data: bytes, *, message_id: str) -> None: ...

    async def subscribe(self, consumer: Consumer, handler: RawHandler) -> Subscription: ...

    async def close(self) -> None: ...


def encode(envelope: Envelope) -> bytes:
    return envelope.model_dump_json().encode()


def decode(subject: str, data: bytes) -> Envelope:
    """The envelope a message carries, or `BusError` if it is not a valid one."""
    try:
        env = Envelope.model_validate_json(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise BusError(f"invalid message on {subject}: {first['msg']}") from None
    if env.subject.value != subject:
        raise BusError(f"message names {env.subject.value} but arrived on {subject}")
    return env


def _now() -> datetime:
    return datetime.now(UTC)


class _Seen:
    """The last `capacity` message IDs a subscription handled."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._ids: OrderedDict[str, None] = OrderedDict()

    def __contains__(self, message_id: str) -> bool:
        return message_id in self._ids

    def add(self, message_id: str) -> None:
        self._ids[message_id] = None
        while len(self._ids) > self.capacity:
            self._ids.popitem(last=False)


class BusClient:
    def __init__(
        self,
        transport: Transport,
        service: Service,
        *,
        clock: Clock = _now,
        on_reject: Callable[[str, str], None] | None = None,
        dedupe_capacity: int = 100_000,
    ) -> None:
        self.transport, self.service, self.clock = transport, service, clock
        self.on_reject = on_reject
        self.dedupe_capacity = dedupe_capacity
        self.rejected = 0
        self._subs: list[Subscription] = []

    async def publish(self, subject: Subject, payload: FirmModel, *, message_id: str) -> Envelope:
        """Publish a typed payload as this service. Refused unless it owns `subject`."""
        if not may_publish(self.service, subject):
            raise BusError(f"permission denied: {self.service} may not publish {subject}")
        env = wrap(
            message_id=message_id,
            sender=self.service,
            subject=subject,
            payload=payload,
            timestamp=self.clock(),
        )
        await self.transport.publish(subject.value, encode(env), message_id=message_id)
        return env

    async def subscribe(self, consumer: Consumer | str, handler: Handler) -> Subscription:
        """Deliver every valid message of one of this service's consumers, once each.

        `consumer` is a name from `CONSUMERS` (or a `Consumer`, in tests); it is
        durable, so after a restart it resumes where it stopped.
        """
        c = CONSUMERS[consumer] if isinstance(consumer, str) else consumer
        if c.service is not self.service:
            raise BusError(f"consumer {c.name} belongs to {c.service}, not {self.service}")
        seen = _Seen(self.dedupe_capacity)

        async def raw(subject: str, data: bytes) -> None:
            try:
                env = decode(subject, data)
            except BusError as exc:
                self._reject(subject, str(exc))
                return  # acknowledged: a malformed message never becomes valid
            if env.message_id in seen:
                return
            await handler(env)  # raising: not acknowledged, delivered again
            seen.add(env.message_id)

        sub = await self.transport.subscribe(c, raw)
        self._subs.append(sub)
        return sub

    def _reject(self, subject: str, why: str) -> None:
        self.rejected += 1
        log.warning("bus: rejected a message on %s: %s", subject, why)
        if self.on_reject is not None:
            self.on_reject(subject, why)

    async def close(self) -> None:
        for sub in self._subs:
            await sub.close()
        self._subs.clear()
