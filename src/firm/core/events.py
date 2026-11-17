"""Emitting ledger events without importing the ledger (golden rule 12).

A desk that has something to record takes an `EventSink`; the composition
root (`firm.control`) wires in the real ledger, tests can collect events, and
`NULL_SINK` drops them. Event IDs should be deterministic where possible, so
re-emitting the same fact (a replay, an import) is idempotent in the ledger.
"""

from __future__ import annotations

from typing import Any, Protocol

from firm.core.domain import LedgerEventType


class EventSink(Protocol):
    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None: ...


class _NullSink:
    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        return None


NULL_SINK: EventSink = _NullSink()


class CollectingSink:
    """Keeps emitted events in memory (tests, dry runs)."""

    def __init__(self) -> None:
        self.events: list[tuple[LedgerEventType, str, dict[str, Any], str | None]] = []

    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        self.events.append((event_type, reason, payload, event_id))
