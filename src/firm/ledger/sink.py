"""Adapters from the `EventSink` protocol to the ledger, plus importing old state."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger


class LedgerSink:
    """Writes emitted events straight into a ledger (one process, in-process desks)."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        self.ledger.append(event_type, reason, payload, event_id=event_id)


def ledger_path(state: Path) -> Path:
    return state / "ledger.sqlite"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def state_history(state: Path) -> list[tuple[str, int, str, dict[str, Any]]]:
    """Existing verifier state as (time, order, kind, record), oldest first."""
    items: list[tuple[str, int, str, dict[str, Any]]] = []
    n = 0
    for kind, name, time_key in (
        ("trial", "trials.jsonl", "recorded_at"),
        ("vault", "oos_openings.jsonl", "at"),
    ):
        for rec in _read_jsonl(state / name):
            items.append((str(rec.get(time_key, "")), n, kind, rec))
            n += 1
    for rec in _read_jsonl(state / "trial_aliases.jsonl"):
        items.append(("", n, "trial_alias", rec))  # no time: replay after what it links
        n += 1
    for path in sorted((state / "reports").glob("v-*.json")):
        mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
        items.append((mtime, n, "report", json.loads(path.read_text())))
        n += 1
    order = {"trial": 0, "vault": 0, "report": 0, "trial_alias": 1}
    return sorted(items, key=lambda x: (order[x[2]], x[0], x[1]))
