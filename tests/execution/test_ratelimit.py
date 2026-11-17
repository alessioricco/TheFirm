"""Token bucket: free while tokens last, then back off by exactly the refill time."""

import asyncio

from firm.execution.adapter import RateLimitPolicy
from firm.execution.ratelimit import TokenBucket


def test_bucket_backs_off_when_empty_and_refills_with_time() -> None:
    now, slept = [0.0], []

    async def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    bucket = TokenBucket(
        RateLimitPolicy(max_requests=3, per_seconds=1.0), clock=lambda: now[0], sleep=sleep
    )

    async def go() -> list[float]:
        return [await bucket.acquire() for _ in range(5)]

    waits = asyncio.run(go())
    assert waits[:3] == [0.0, 0.0, 0.0]
    assert waits[3] > 0 and abs(waits[3] - 1 / 3) < 1e-9  # one token refills in 1/3 s
    now[0] += 10  # idle: the bucket refills to capacity, not beyond
    assert asyncio.run(go())[:3] == [0.0, 0.0, 0.0]
