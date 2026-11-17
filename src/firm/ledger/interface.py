"""Ledger interface (implemented by `firm.ledger.store.Ledger`).

There is intentionally no update or delete method.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from firm.core.domain import LedgerEvent, LedgerEventType
from firm.ledger.store import ChainReport


class LedgerLike(Protocol):
    def append(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> LedgerEvent: ...

    def read(self, from_sequence: int = 0) -> Iterator[LedgerEvent]: ...

    def verify_chain(self) -> ChainReport: ...
