"""Data plane read interface. Implemented in milestone 1."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class DataPlane(Protocol):
    """Point-in-time reads. Nothing with a timestamp after `as_of` is ever returned.

    Return types (bar/trade/book frames) are fixed in milestone 1.
    """

    def dataset_version(self) -> str: ...

    def bars(
        self, instrument: str, timeframe: str, *, start: datetime, as_of: datetime
    ) -> object: ...

    def trades(self, instrument: str, *, start: datetime, as_of: datetime) -> object: ...

    def book(self, instrument: str, *, as_of: datetime) -> object: ...
