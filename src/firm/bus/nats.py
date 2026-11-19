"""The NATS JetStream transport (the bus of a running firm).

One stream (`config/bus.yaml`, `FIRM` by default) holds exactly the firm's
subjects, so messages survive a consumer being down and can be replayed. Its
messages can't be deleted or purged through the API (the ledger is the
record; the stream only ages out after `max_age_days`). Publishing sets
`Nats-Msg-Id` to the message ID, so JetStream drops a duplicate sent within
`duplicate_window_seconds` (a retried publish never lands twice).
Subscriptions are durable push consumers with manual acknowledgement: a
handler that returns acknowledges, one that raises asks for redelivery after
`redeliver_seconds`.

Services log in with their own user (`firm.bus.auth`), so the server
refuses a subject a service does not own. Only the admin login creates or
updates the stream (`firm bus up`).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import nats
from nats.js import api
from nats.js.errors import NotFoundError

from firm.bus.auth import ADMIN, inbox_prefix, login
from firm.bus.client import RawHandler
from firm.bus.consumers import CONSUMERS, Consumer
from firm.core.config import BusConfig
from firm.core.messages import Service, Subject

log = logging.getLogger(__name__)

DAY = 86_400.0


def stream_config(cfg: BusConfig) -> api.StreamConfig:
    return api.StreamConfig(
        name=cfg.stream,
        subjects=sorted(s.value for s in Subject),
        storage=api.StorageType.FILE,
        retention=api.RetentionPolicy.LIMITS,
        max_age=cfg.max_age_days * DAY,
        duplicate_window=cfg.duplicate_window_seconds,
        deny_delete=True,
        deny_purge=True,
    )


@dataclass
class NatsSubscription:
    sub: Any

    async def close(self) -> None:
        await self.sub.unsubscribe()  # the durable consumer stays on the server


class NatsBus:
    def __init__(self, nc: Any, cfg: BusConfig) -> None:
        self.nc, self.js, self.cfg = nc, nc.jetstream(), cfg
        self._closing: dict[str, bool] = {"yes": False}

    @classmethod
    async def connect(
        cls, cfg: BusConfig, *, user: str, password: str, url: str | None = None
    ) -> NatsBus:
        last: dict[str, float] = {}
        closing = {"yes": False}

        async def on_error(exc: Exception) -> None:
            # e.g. a permissions violation; while the server is down the client retries
            # every few seconds, so the same error is logged at most once a minute
            text, now = str(exc), time.monotonic()
            if now - last.get(text, -1e9) >= 60:
                last[text] = now
                log.warning("bus: %s", text)

        async def on_disconnect() -> None:
            if closing["yes"]:
                return  # our own close(), not an outage
            log.warning("bus: disconnected from %s; reconnecting", url or cfg.url)

        async def on_reconnect() -> None:
            last.clear()
            log.warning("bus: reconnected to %s", url or cfg.url)

        nc = await nats.connect(
            url or cfg.url,
            user=user,
            password=password,
            name=f"firm-{user}",
            inbox_prefix=inbox_prefix(user).encode(),
            max_reconnect_attempts=-1,
            error_cb=on_error,
            disconnected_cb=on_disconnect,
            reconnected_cb=on_reconnect,
        )
        bus = cls(nc, cfg)
        bus._closing = closing
        return bus

    @classmethod
    async def for_service(
        cls, cfg: BusConfig, state: Path, service: Service | str, *, url: str | None = None
    ) -> NatsBus:
        """Log in as `service` (or `ADMIN`) with the password from the state folder."""
        user, password = login(state, service)
        return await cls.connect(cfg, user=user, password=password, url=url)

    async def ensure_stream(self) -> api.StreamInfo:
        """Create or update the firm's stream (admin login only)."""
        wanted = stream_config(self.cfg)
        try:
            info = await self.js.stream_info(self.cfg.stream)
        except NotFoundError:
            return await self.js.add_stream(wanted)
        current = info.config
        if (
            sorted(current.subjects or []) != wanted.subjects
            or current.max_age != wanted.max_age
            or current.duplicate_window != wanted.duplicate_window
        ):
            return await self.js.update_stream(wanted)
        return info

    async def publish(self, subject: str, data: bytes, *, message_id: str) -> None:
        await self.js.publish(
            subject,
            data,
            stream=self.cfg.stream,
            headers={"Nats-Msg-Id": message_id},
            timeout=self.cfg.publish_timeout_seconds,
        )

    async def ensure_consumers(self, consumers: list[Consumer] | None = None) -> list[str]:
        """Create the declared durable consumers that don't exist yet (admin only).

        An existing consumer is left as it is, so it keeps its position."""
        created = []
        for c in consumers if consumers is not None else list(CONSUMERS.values()):
            try:
                await self.js.consumer_info(self.cfg.stream, c.name)
                continue
            except NotFoundError:
                pass
            await self.js.add_consumer(
                self.cfg.stream,
                api.ConsumerConfig(
                    durable_name=c.name,
                    filter_subject=c.filter,
                    deliver_subject=c.deliver_subject,
                    deliver_policy=(
                        api.DeliverPolicy.NEW if c.start == "new" else api.DeliverPolicy.ALL
                    ),
                    ack_policy=api.AckPolicy.EXPLICIT,
                    ack_wait=max(30.0, 3 * self.cfg.redeliver_seconds),
                ),
            )
            created.append(c.name)
        return created

    async def subscribe(self, consumer: Consumer, handler: RawHandler) -> NatsSubscription:
        """Bind to a declared consumer (created by the admin at `firm bus up`)."""

        async def cb(msg: Any) -> None:
            try:
                await handler(msg.subject, msg.data)
            except Exception as exc:
                log.warning(
                    "bus: %s failed on %s (%s); redelivering", consumer.name, msg.subject, exc
                )
                await msg.nak(delay=self.cfg.redeliver_seconds)
                return
            await msg.ack()

        try:
            await self.js.consumer_info(self.cfg.stream, consumer.name)
        except NotFoundError:
            raise RuntimeError(
                f"bus consumer {consumer.name} does not exist: restart `firm bus up` "
                "(it creates every declared consumer)"
            ) from None
        sub = await self.js.subscribe_bind(
            stream=self.cfg.stream,
            consumer=consumer.name,
            config=api.ConsumerConfig(
                durable_name=consumer.name, deliver_subject=consumer.deliver_subject
            ),
            cb=cb,
            manual_ack=True,
        )
        return NatsSubscription(sub)

    async def close(self) -> None:
        self._closing["yes"] = True
        await self.nc.drain()


__all__ = ["ADMIN", "NatsBus", "NatsSubscription", "stream_config"]
