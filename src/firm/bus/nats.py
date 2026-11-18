"""The NATS JetStream transport (the bus of a running firm).

One stream (`FIRM` by default) holds every firm subject, so messages survive
a consumer being down and can be replayed. Publishing sets `Nats-Msg-Id` to
the message ID, so JetStream drops a duplicate sent within its window (a
retried publish never lands twice). Subscriptions are durable push
consumers with manual acknowledgement: a handler that returns acknowledges,
a handler that raises asks for redelivery after `redeliver_seconds`.

The server address and this service's credentials are configuration
(`NATS_URL`, and per-service users from F2), never code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import nats
from nats.js import api
from nats.js.errors import NotFoundError

from firm.bus.client import RawHandler
from firm.core.messages import Subject

log = logging.getLogger(__name__)

DEFAULT_STREAM = "FIRM"


def stream_config(name: str = DEFAULT_STREAM) -> api.StreamConfig:
    return api.StreamConfig(
        name=name,
        subjects=sorted(s.value for s in Subject),
        storage=api.StorageType.FILE,
        retention=api.RetentionPolicy.LIMITS,
    )


@dataclass
class NatsSubscription:
    sub: Any

    async def close(self) -> None:
        await self.sub.unsubscribe()  # the durable consumer stays on the server


class NatsBus:
    def __init__(self, nc: Any, stream: str, redeliver_seconds: float) -> None:
        self.nc, self.js, self.stream = nc, nc.jetstream(), stream
        self.redeliver_seconds = redeliver_seconds

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        user: str | None = None,
        password: str | None = None,
        stream: str = DEFAULT_STREAM,
        ensure_stream: bool = True,
        redeliver_seconds: float = 5.0,
    ) -> NatsBus:
        nc = await nats.connect(
            url, user=user, password=password, name="firm", max_reconnect_attempts=-1
        )
        bus = cls(nc, stream, redeliver_seconds)
        if ensure_stream:
            await bus._ensure_stream()
        return bus

    async def _ensure_stream(self) -> None:
        wanted = stream_config(self.stream)
        try:
            info = await self.js.stream_info(self.stream)
        except NotFoundError:
            await self.js.add_stream(wanted)
            return
        if sorted(info.config.subjects or []) != wanted.subjects:
            await self.js.update_stream(wanted)  # a new subject was added to the table

    async def publish(self, subject: str, data: bytes, *, message_id: str) -> None:
        await self.js.publish(
            subject, data, stream=self.stream, headers={"Nats-Msg-Id": message_id}
        )

    async def subscribe(
        self, pattern: str, *, durable: str, handler: RawHandler
    ) -> NatsSubscription:
        async def cb(msg: Any) -> None:
            try:
                await handler(msg.subject, msg.data)
            except Exception as exc:
                log.warning("bus: %s failed on %s (%s); redelivering", durable, msg.subject, exc)
                await msg.nak(delay=self.redeliver_seconds)
                return
            await msg.ack()

        sub = await self.js.subscribe(
            pattern, durable=durable, stream=self.stream, cb=cb, manual_ack=True
        )
        return NatsSubscription(sub)

    async def close(self) -> None:
        await self.nc.drain()
