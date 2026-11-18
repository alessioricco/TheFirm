"""The trade journal: one row per round trip, rebuilt from the ledger only.

Events are written the way the worker, risk desk and simulator write them
(same types, same payloads), into a real ledger in a temp directory.
"""

from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from factories import T0, order_intent, order_proposal, risk_decision
from firm.core.domain import Fill, LedgerEventType, RiskAction, Side
from firm.ledger.journal import JournalError, build_journal, export_journal, trades_from_ledger
from firm.ledger.store import Ledger

D = Decimal


class Book:
    """Writes proposal -> decision -> intent -> fills like the paper path does."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.n = 0

    def order(
        self,
        side: Side,
        qty: str,
        ref: str,
        fills: list[tuple[str, str, str]],  # (qty, price, fee)
        *,
        at_min: int,
        reject: bool = False,
        verdict_id: str = "v-1",
    ) -> None:
        self.n += 1
        n, at = self.n, T0 + timedelta(minutes=at_min)
        p = order_proposal(
            proposal_id=f"p-{n}",
            verdict_id=verdict_id,
            side=side,
            quantity=D(qty),
            reference_price=D(ref),
            created_at=at,
        )
        self.ledger.append(
            LedgerEventType.ORDER_PROPOSAL,
            f"{side.value} {qty} BTC-USD: target x",
            p.model_dump(mode="json"),
            event_id=f"proposal:p-{n}",
        )
        if reject:
            d = risk_decision(
                decision_id=f"r-{n}",
                proposal_id=f"p-{n}",
                verdict_id=verdict_id,
                action=RiskAction.REJECT,
                approved_quantity=None,
                reason="kill switch",
                decided_at=at,
            )
        else:
            d = risk_decision(
                decision_id=f"r-{n}",
                proposal_id=f"p-{n}",
                verdict_id=verdict_id,
                approved_quantity=D(qty),
                decided_at=at,
            )
        self.ledger.append(
            LedgerEventType.RISK_DECISION, d.reason, d.model_dump(mode="json"), event_id=f"r:{n}"
        )
        if reject:
            return
        i = order_intent(
            intent_id=f"i-{n}",
            client_order_id=f"c-{n}",
            proposal_id=f"p-{n}",
            verdict_id=verdict_id,
            risk_decision_id=f"r-{n}",
            side=side,
            quantity=D(qty),
            created_at=at + timedelta(seconds=1),
        )
        self.ledger.append(
            LedgerEventType.ORDER_INTENT, "intent", i.model_dump(mode="json"), event_id=f"i:{n}"
        )
        for k, (fq, fp, fee) in enumerate(fills):
            f = Fill(
                fill_id=f"f-{n}-{k}",
                client_order_id=f"c-{n}",
                intent_id=f"i-{n}",
                verdict_id=verdict_id,
                risk_decision_id=f"r-{n}",
                venue="simulator",
                instrument="BTC-USD",
                side=side,
                quantity=D(fq),
                price=D(fp),
                fee=D(fee),
                fee_currency="USD",
                filled_at=at + timedelta(minutes=1, seconds=k),
            )
            self.ledger.append(
                LedgerEventType.FILL, "fill", f.model_dump(mode="json"), event_id=f"fill:{n}:{k}"
            )


def scenario(path: Path) -> Ledger:
    ledger = Ledger(path / "ledger.sqlite")
    b = Book(ledger)
    # trade 1: entry in two steps (the per-order cap), the second filled in two parts
    b.order(Side.BUY, "0.01", "50000", [("0.01", "50025", "0.30")], at_min=0)
    b.order(
        Side.BUY,
        "0.01",
        "51000",
        [("0.004", "51040", "0.12"), ("0.006", "51040", "0.19")],
        at_min=60,
    )
    b.order(Side.BUY, "0.01", "51500", [], at_min=90, reject=True)  # rejected: no trade effect
    b.order(Side.SELL, "0.02", "52000", [("0.02", "51974", "0.62")], at_min=120)
    # trade 2: still open
    b.order(Side.BUY, "0.01", "53000", [("0.01", "53010", "0.32")], at_min=180)
    return ledger


def test_round_trips_are_rebuilt_with_prices_costs_and_pnl(tmp_path: Path) -> None:
    ledger = scenario(tmp_path)
    trades, position = trades_from_ledger(ledger)

    assert [t.status for t in trades] == ["closed", "open"]
    t = trades[0]
    assert (t.strategy_id, t.verdict_id, t.market, t.venue, t.instrument) == (
        "s-1",
        "v-1",
        "crypto",
        "simulator",
        "BTC-USD",
    )
    assert t.entry_risk_decision_ids == ("r-1", "r-2") and t.exit_verdict_id == "v-1"
    assert t.quantity == D("0.02")
    assert t.decision_time == T0 and t.order_time == T0 + timedelta(seconds=1)
    assert t.entry_time == T0 + timedelta(minutes=1)
    assert t.exit_decision_time == T0 + timedelta(minutes=120)
    assert t.exit_time == T0 + timedelta(minutes=121)
    assert t.holding_seconds == 120 * 60
    assert t.intended_entry_price == D("50500")
    assert t.entry_price == D("50532.5")
    assert t.intended_exit_price == D("52000") and t.exit_price == D("51974")
    # slippage is positive when it costs us: paid more on the buy, got less on the sell
    assert t.entry_slippage_bps == pytest.approx(6.4356, abs=1e-3)
    assert t.exit_slippage_bps == pytest.approx(5.0, abs=1e-9)
    assert t.fees == D("1.23") and t.fee_currency == "USD"
    assert t.gross_pnl == D("28.83") and t.net_pnl == D("27.60")
    assert t.net_return == pytest.approx(27.60 / 1010.65, rel=1e-9)
    assert t.entry_fills == 3 and t.exit_fills == 1
    assert t.exit_reason.startswith("sell 0.02")
    # what the ledger can't tell yet is unknown, never zero
    assert t.spread_paid is None and t.max_adverse_excursion is None
    assert t.feature_snapshot is None and t.judge_outputs is None

    o = trades[1]
    assert o.exit_time is None and o.net_pnl is None and o.quantity == D("0.01")
    assert o.fees == D("0.32")

    assert position.sequence == ledger.head().sequence
    assert position.hash == ledger.head().hash


def test_trade_ids_are_stable_across_rebuilds(tmp_path: Path) -> None:
    ledger = scenario(tmp_path)
    a, _ = trades_from_ledger(ledger)
    b, _ = trades_from_ledger(ledger)
    assert [t.trade_id for t in a] == [t.trade_id for t in b]
    assert len({t.trade_id for t in a}) == 2


def test_a_fill_without_its_intent_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    f = Fill(
        fill_id="f-x",
        client_order_id="c-x",
        intent_id="i-missing",
        verdict_id="v-1",
        risk_decision_id="r-1",
        venue="simulator",
        instrument="BTC-USD",
        side=Side.BUY,
        quantity=D("0.01"),
        price=D("50000"),
        fee=D("0"),
        fee_currency="USD",
        filled_at=T0,
    )
    ledger.append(LedgerEventType.FILL, "fill", f.model_dump(mode="json"))
    with pytest.raises(JournalError, match="i-missing"):
        trades_from_ledger(ledger)


def test_selling_more_than_held_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    Book(ledger).order(Side.SELL, "0.01", "50000", [("0.01", "50000", "0")], at_min=0)
    with pytest.raises(JournalError, match="more than held"):
        trades_from_ledger(ledger)


def test_the_journal_is_a_duckdb_table_stamped_with_its_ledger_position(tmp_path: Path) -> None:
    ledger = scenario(tmp_path)
    out = tmp_path / "journal.duckdb"
    meta = build_journal(ledger, out)
    assert meta.trades == 2 and meta.ledger_sequence == ledger.head().sequence

    con = duckdb.connect(str(out), read_only=True)
    rows = con.execute(
        "SELECT status, net_pnl, ledger_sequence, ledger_hash FROM trades ORDER BY decision_time"
    ).fetchall()
    assert rows[0][0] == "closed" and rows[0][1] == D("27.60")
    assert rows[1][1] is None
    assert {r[2] for r in rows} == {ledger.head().sequence}
    assert {r[3] for r in rows} == {ledger.head().hash}
    con.close()

    # a rebuild replaces the file whole; the old one is never edited
    more = Book(ledger)
    more.n = 100  # new IDs after the scenario's
    more.order(Side.SELL, "0.01", "54000", [("0.01", "54000", "0.32")], at_min=240)
    meta2 = build_journal(ledger, out)
    assert meta2.ledger_sequence > meta.ledger_sequence
    con = duckdb.connect(str(out), read_only=True)
    assert con.execute("SELECT count(*) FROM trades WHERE status = 'closed'").fetchone() == (2,)
    con.close()


def test_export_filters_by_decision_date_and_carries_the_ledger_position(tmp_path: Path) -> None:
    ledger = scenario(tmp_path)
    db = tmp_path / "journal.duckdb"
    build_journal(ledger, db)

    csv_out = tmp_path / "trades.csv"
    n = export_journal(db, csv_out, fmt="csv", start=T0 + timedelta(minutes=150))
    assert n == 1
    with csv_out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["status"] == "open"
    assert rows[0]["ledger_sequence"] == str(ledger.head().sequence)

    pq = tmp_path / "trades.parquet"
    assert export_journal(db, pq, fmt="parquet") == 2
    assert duckdb.sql(f"SELECT count(*) FROM '{pq}'").fetchall() == [(2,)]


def test_cli_build_show_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from firm.control.cli import main

    scenario(tmp_path)  # writes tmp_path/ledger.sqlite, the state root's ledger
    root = ["--state-root", str(tmp_path)]
    assert main(["journal", "show", *root]) == 2  # not built yet
    assert main(["journal", "build", *root]) == 0
    assert "1 closed trades, 1 open" in capsys.readouterr().out
    assert main(["journal", "show", *root]) == 0
    out = capsys.readouterr().out
    assert "net +27.60" in out and "2026-11-16 12:00" in out and "USD" in out
    dest = tmp_path / "x.csv"
    assert main(["journal", "export", *root, "--from", "2026-11-16", "--out", str(dest)]) == 0
    assert "wrote 2 trades" in capsys.readouterr().out
