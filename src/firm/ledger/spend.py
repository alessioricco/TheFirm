"""Model spend read back from the ledger (MODEL_CALL events, firm rule 8).

The model gateway's cloud budget is per calendar month; this gives it the
spend already recorded, so the budget holds across restarts and processes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger


def cloud_spend_gbp(ledger: Ledger, now: datetime) -> float:
    """Cloud spend (GBP) of successful calls in `now`'s calendar month (UTC)."""
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    for e in ledger.read():
        p = e.payload
        if e.event_type is not LedgerEventType.MODEL_CALL or p.get("outcome") != "ok":
            continue
        if p.get("local") or datetime.fromisoformat(p["at"]) < start:
            continue
        if datetime.fromisoformat(p["at"]) <= now:
            total += float(p.get("cost_gbp", 0.0))
    return total
