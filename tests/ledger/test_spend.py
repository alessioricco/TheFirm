"""The cloud budget holds across restarts: this month's spend comes from the ledger."""

from __future__ import annotations

from datetime import UTC, datetime

from firm.core.domain import LedgerEventType
from firm.ledger.spend import cloud_spend_gbp, dossier_model_usage
from firm.ledger.store import Ledger


def call(ledger: Ledger, at: str, cost: float, *, local=False, outcome="ok") -> None:
    payload = {"outcome": outcome, "local": local, "at": at, "cost_gbp": cost}
    ledger.append(LedgerEventType.MODEL_CALL, "model call", payload)


def test_only_this_months_successful_cloud_calls_count(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    call(ledger, "2026-10-31T23:59:00+00:00", 5.0)  # last month
    call(ledger, "2026-11-01T00:00:00+00:00", 0.40)
    call(ledger, "2026-11-18T10:00:00+00:00", 0.35)
    call(ledger, "2026-11-18T11:00:00+00:00", 0.0, local=True)  # local: free
    call(ledger, "2026-11-18T12:00:00+00:00", 9.0, outcome="refused")  # never sent
    ledger.append(LedgerEventType.FILL, "a fill", {"at": "2026-11-18T12:00:00+00:00"})
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    assert cloud_spend_gbp(ledger, now) == 0.75
    assert cloud_spend_gbp(ledger, datetime(2026, 12, 1, tzinfo=UTC)) == 0.0


def test_a_dossiers_tokens_and_cloud_spend(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    base = {"outcome": "ok", "at": "2026-11-19T10:00:00+00:00"}
    for p in (
        base | {"dossier_id": "d-1", "local": True, "input_tokens": 100, "output_tokens": 20},
        base
        | {
            "dossier_id": "d-1",
            "local": False,
            "input_tokens": 5,
            "output_tokens": 5,
            "cost_gbp": 0.3,
        },
        base | {"dossier_id": "d-2", "local": True, "input_tokens": 999, "output_tokens": 1},
        base | {"dossier_id": "d-1", "local": False, "outcome": "refused"},  # never sent
    ):
        ledger.append(LedgerEventType.MODEL_CALL, "model call", p)
    assert dossier_model_usage(ledger, "d-1") == (130, 0.3)
    assert dossier_model_usage(ledger, "d-9") == (0, 0.0)
