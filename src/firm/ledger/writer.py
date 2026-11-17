"""The single ledger writer (golden rule 10): producers enqueue, one task writes.

Every market worker and desk in the process submits through the same queue,
so events keep a single order and the hash chain is built by one writer.
`submit()` returns the stored event once it is durable.
"""

from __future__ import annotations

import asyncio
from typing import Any

from firm.core.domain import LedgerEvent, LedgerEventType
from firm.ledger.store import Ledger

_STOP = object()


class LedgerWriter:
    def __init__(self, ledger: Ledger, *, maxsize: int = 10_000) -> None:
        self.ledger = ledger
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)

    async def submit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> LedgerEvent:
        done: asyncio.Future[LedgerEvent] = asyncio.get_running_loop().create_future()
        await self._queue.put((event_type, reason, payload, event_id, done))
        return await done

    async def close(self) -> None:
        await self._queue.put(_STOP)

    async def run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            event_type, reason, payload, event_id, done = item
            try:
                event = await asyncio.to_thread(
                    self.ledger.append, event_type, reason, payload, event_id=event_id
                )
            except Exception as exc:  # the producer sees the failure; the writer keeps going
                done.set_exception(exc)
            else:
                done.set_result(event)
