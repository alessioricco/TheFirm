"""`firm paper status` and the dashboard's paper view: from the ledger alone."""

from __future__ import annotations

from datetime import UTC, datetime

from firm.control.cli import main
from firm.control.paper import OPENED
from firm.control.status import describe, paper_status
from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger

AT = datetime(2026, 11, 20, 0, tzinfo=UTC).isoformat()


def fill(side: str, qty: str, price: str, n: int) -> dict:
    return {
        "fill_id": f"f-{n}",
        "client_order_id": f"o-{n}",
        "intent_id": f"i-{n}",
        "verdict_id": "v-xxxxxxxxxxxxxxxx",
        "risk_decision_id": f"r-{n}",
        "venue": "coinbase",
        "instrument": "BTC-USD",
        "side": side,
        "quantity": qty,
        "price": price,
        "fee": "1",
        "fee_currency": "USD",
        "filled_at": AT,
    }


def ledger_with_a_run(path) -> Ledger:
    led = Ledger(path / "ledger.sqlite")
    ms = LedgerEventType.MARKET_STATUS
    led.append(ms, "account", {"kind": OPENED, "market": "crypto", "venue": "coinbase",
                               "balances": {"USD": "6000"}, "at": AT})  # fmt: skip
    led.append(ms, "started", {"kind": "started", "market": "crypto", "venue": "coinbase",
                               "verdict_id": "v-xxxxxxxxxxxxxxxx", "at": AT})  # fmt: skip
    led.append(LedgerEventType.FILL, "fill", fill("buy", "0.01", "50000", 1))
    led.append(LedgerEventType.FEED_STATUS, "stale", {"provider": "coinbase_ws",
               "symbol": "BTC-USD", "status": "stale", "detail": "no trade", "at": AT})  # fmt: skip
    led.append(ms, "hb", {"kind": "heartbeat_lost", "market": "crypto", "at": AT})
    led.append(LedgerEventType.RISK_HALT, "daily loss", {"scope": "market", "market": "crypto",
               "reason": "daily loss limit", "issued_at": AT, "halt_id": "h-1"})  # fmt: skip
    return led


def test_paper_status_from_the_ledger(tmp_path) -> None:
    s = paper_status(ledger_with_a_run(tmp_path))
    (acc,) = s["accounts"]
    assert acc["opening"] == {"USD": "6000"}
    assert acc["balances"] == {"USD": "5499", "BTC": "0.01"}  # 6000 - 500 - 1 fee
    assert acc["fills"] == 1 and acc["last_fill"]["side"] == "buy"
    (run,) = s["runs"]
    assert run["running"] and run["verdict_id"] == "v-xxxxxxxxxxxxxxxx"
    assert s["feeds"][0]["status"] == "stale"
    assert s["heartbeat"]["crypto"]["state"] == "lost"
    assert s["halts"][0]["reason"] == "daily loss limit"
    assert "no intent" in s["trades"]["error"]  # this test ledger has fills only
    text = "\n".join(describe(s))
    assert "running (last start has no stop)" in text and "USD 5499" in text


def test_a_stop_is_shown(tmp_path) -> None:
    led = ledger_with_a_run(tmp_path)
    led.append(LedgerEventType.MARKET_STATUS, "stopped", {"kind": "stopped", "market": "crypto",
               "venue": "coinbase", "failure": None, "balances": {}, "at": AT})  # fmt: skip
    (run,) = paper_status(led)["runs"]
    assert not run["running"] and run["stopped_at"] == AT


def test_the_command(tmp_path, capsys) -> None:
    assert main(["paper", "status", "--state-root", str(tmp_path)]) == 0
    assert "no paper account" in capsys.readouterr().out
    ledger_with_a_run(tmp_path)
    assert main(["paper", "status", "--state-root", str(tmp_path)]) == 0
    assert "account crypto on coinbase" in capsys.readouterr().out


def test_net_pnl_comes_from_the_same_rebuild_as_the_journal(tmp_path) -> None:
    import importlib.util
    from decimal import Decimal
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "journal_scenario", Path(__file__).parents[1] / "ledger" / "test_journal.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t = paper_status(mod.scenario(tmp_path))["trades"]
    assert (t["closed"], t["open"], t["error"]) == (1, 1, None)
    assert Decimal(t["net_pnl"]["USD"]) == Decimal("27.60")
