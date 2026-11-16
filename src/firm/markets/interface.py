"""Market worker interface. Implemented in milestone 2."""

from __future__ import annotations

from typing import Protocol


class MarketWorker(Protocol):
    market: str

    async def run(self) -> None: ...

    async def halt(self, reason: str) -> None: ...
