"""Token-bucket rate limiting for venue adapters (exchanges ban clients that flood).

`acquire()` returns at once while tokens remain and otherwise sleeps until the
next one is due, so an adapter backs off instead of hitting the venue's limit.
Clock and sleep are injectable for tests.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from firm.execution.adapter import RateLimitPolicy


class TokenBucket:
    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.capacity = float(policy.max_requests)
        self.rate = policy.max_requests / policy.per_seconds  # tokens per second
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    async def acquire(self) -> float:
        """Take one token; returns how long it waited (0 if none)."""
        self._refill()
        waited = 0.0
        if self._tokens < 1:
            waited = (1 - self._tokens) / self.rate
            await self._sleep(waited)
            self._refill()
        self._tokens -= 1
        return waited
