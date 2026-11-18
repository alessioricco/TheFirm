"""The trade journal: one row per paper trade, rebuilt from the ledger only.

A **trade** is one round trip on one venue account and instrument: it opens
with the fill that takes the position from flat to long and closes with the
fill that takes it back to flat (spot, long-only). Every buy and sell in
between belongs to it, so an entry made in steps (the per-order cap) or an
order filled in parts is still one trade. Each fill is traced through its
intent to the proposal and the risk decision that allowed it.

What the ledger can't tell yet stays **unknown (None), never zero**: spread
paid (the simulator folds it into the fill price), maximum adverse and
favourable excursion (needs the price path), the feature snapshot, judge
outputs and model versions (milestones 3 and 5).

The journal is never edited: `build_journal` writes a fresh DuckDB file and
swaps it in whole. Every row, and every export, carries the ledger position
(sequence and hash of the last event read) it was built from. Tagged private:
it holds results.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb

from firm.core.domain import Fill, LedgerEventType, OrderIntent, OrderProposal, Side
from firm.ledger.interface import LedgerLike

SCHEMA_VERSION = 1
_PRICE = Decimal("1e-10")


class JournalError(RuntimeError):
    """The ledger doesn't describe a consistent book; the journal refuses to guess."""


@dataclass(frozen=True)
class LedgerPosition:
    sequence: int  # -1 for an empty ledger
    hash: str | None


@dataclass(frozen=True)
class Trade:
    # identity
    trade_id: str
    strategy_id: str
    verdict_id: str
    exit_verdict_id: str | None
    entry_risk_decision_ids: tuple[str, ...]
    exit_risk_decision_ids: tuple[str, ...]
    experiment_id: str | None
    # market
    market: str
    venue: str
    instrument: str
    side: str  # long (spot, long-only in v1)
    quote_currency: str
    status: Literal["open", "closed"]
    # timing
    decision_time: datetime
    order_time: datetime
    entry_time: datetime
    exit_decision_time: datetime | None
    exit_time: datetime | None
    holding_seconds: float | None
    # prices
    quantity: Decimal
    intended_entry_price: Decimal
    entry_price: Decimal
    intended_exit_price: Decimal | None
    exit_price: Decimal | None
    entry_slippage_bps: float
    exit_slippage_bps: float | None
    entry_fills: int
    exit_fills: int
    # costs
    fees: Decimal
    fee_currency: str
    spread_paid: Decimal | None
    fx_cost: Decimal | None
    # result
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    net_return: float | None
    max_adverse_excursion: Decimal | None
    max_favourable_excursion: Decimal | None
    # context at decision (later milestones)
    entry_reason: str
    feature_snapshot: str | None
    feature_version: str | None
    judge_outputs: str | None
    model_versions: str | None
    # outcome labels
    exit_reason: str | None
    regime_after: str | None


@dataclass
class _Open:
    first: Fill
    proposal: OrderProposal
    intent: OrderIntent
    entry_reason: str
    held: Decimal = Decimal(0)
    bought: Decimal = Decimal(0)
    sold: Decimal = Decimal(0)
    buy_value: Decimal = Decimal(0)
    buy_intended: Decimal = Decimal(0)
    sell_value: Decimal = Decimal(0)
    sell_intended: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    entry_fills: int = 0
    exit_fills: int = 0
    entry_risk: list[str] = field(default_factory=list)
    exit_risk: list[str] = field(default_factory=list)
    exit_verdict: str | None = None
    exit_decision_time: datetime | None = None
    exit_reason: str | None = None
    last_fill: datetime | None = None


def _bps(diff: Decimal, base: Decimal) -> float:
    return float(diff / base * 10_000)


def _q(d: Decimal) -> Decimal:
    return d.quantize(_PRICE)


def _trade(acc: _Open) -> Trade:
    p, closed = acc.proposal, acc.held == 0
    entry_price = acc.buy_value / acc.bought
    intended_entry = acc.buy_intended / acc.bought
    exit_price = acc.sell_value / acc.sold if acc.sold else None
    intended_exit = acc.sell_intended / acc.sold if acc.sold else None
    gross = acc.sell_value - acc.buy_value if closed else None
    net = gross - acc.fees if gross is not None else None
    entry_time = acc.first.filled_at
    exit_time = acc.last_fill if closed else None
    return Trade(
        trade_id="t-" + hashlib.sha256(acc.first.fill_id.encode()).hexdigest()[:16],
        strategy_id=p.strategy_id,
        verdict_id=acc.first.verdict_id,
        exit_verdict_id=acc.exit_verdict,
        entry_risk_decision_ids=tuple(acc.entry_risk),
        exit_risk_decision_ids=tuple(acc.exit_risk),
        experiment_id=None,
        market=p.market,
        venue=acc.first.venue,
        instrument=acc.first.instrument,
        side="long",
        quote_currency=p.quote_currency,
        status="closed" if closed else "open",
        decision_time=p.created_at,
        order_time=acc.intent.created_at,
        entry_time=entry_time,
        exit_decision_time=acc.exit_decision_time if closed else None,
        exit_time=exit_time,
        holding_seconds=(exit_time - entry_time).total_seconds() if exit_time else None,
        quantity=acc.bought,
        intended_entry_price=_q(intended_entry),
        entry_price=_q(entry_price),
        intended_exit_price=_q(intended_exit) if intended_exit is not None else None,
        exit_price=_q(exit_price) if exit_price is not None else None,
        entry_slippage_bps=_bps(entry_price - intended_entry, intended_entry),
        exit_slippage_bps=_bps(intended_exit - exit_price, intended_exit)
        if intended_exit is not None and exit_price is not None
        else None,
        entry_fills=acc.entry_fills,
        exit_fills=acc.exit_fills,
        fees=acc.fees,
        fee_currency=p.quote_currency,
        spread_paid=None,
        fx_cost=None,
        gross_pnl=gross,
        net_pnl=net,
        net_return=float(net / acc.buy_value) if net is not None else None,
        max_adverse_excursion=None,
        max_favourable_excursion=None,
        entry_reason=acc.entry_reason,
        feature_snapshot=None,
        feature_version=None,
        judge_outputs=None,
        model_versions=None,
        exit_reason=acc.exit_reason if closed else None,
        regime_after=None,
    )


def trades_from_ledger(ledger: LedgerLike) -> tuple[list[Trade], LedgerPosition]:
    """Every trade in the ledger, ordered by decision time, and the position read up to."""
    proposals: dict[str, tuple[OrderProposal, str]] = {}
    intents: dict[str, OrderIntent] = {}
    books: dict[tuple[str, str], _Open] = {}
    done: list[Trade] = []
    position = LedgerPosition(-1, None)
    for e in ledger.read():
        position = LedgerPosition(e.sequence, e.hash)
        if e.event_type is LedgerEventType.ORDER_PROPOSAL:
            p = OrderProposal.model_validate(e.payload)
            proposals[p.proposal_id] = (p, e.reason)
        elif e.event_type is LedgerEventType.ORDER_INTENT:
            i = OrderIntent.model_validate(e.payload)
            intents[i.intent_id] = i
        elif e.event_type is LedgerEventType.FILL:
            fill = Fill.model_validate(e.payload)
            intent = intents.get(fill.intent_id)
            if intent is None:
                raise JournalError(f"fill {fill.fill_id}: no intent {fill.intent_id} before it")
            if intent.proposal_id not in proposals:
                raise JournalError(f"intent {intent.intent_id}: no proposal {intent.proposal_id}")
            proposal, reason = proposals[intent.proposal_id]
            if fill.fee_currency != proposal.quote_currency:
                raise JournalError(
                    f"fill {fill.fill_id}: fee in {fill.fee_currency}, not the quote "
                    f"{proposal.quote_currency} (not supported yet)"
                )
            key = (fill.venue, fill.instrument)
            acc = books.get(key)
            value = fill.quantity * fill.price
            intended = fill.quantity * proposal.reference_price
            if fill.side is Side.BUY:
                if acc is None:
                    acc = books[key] = _Open(fill, proposal, intent, reason)
                acc.held += fill.quantity
                acc.bought += fill.quantity
                acc.buy_value += value
                acc.buy_intended += intended
                acc.entry_fills += 1
                if fill.risk_decision_id not in acc.entry_risk:
                    acc.entry_risk.append(fill.risk_decision_id)
            else:
                held = acc.held if acc else Decimal(0)
                if acc is None or fill.quantity > held:
                    raise JournalError(
                        f"fill {fill.fill_id}: sells {fill.quantity} {fill.instrument}, "
                        f"more than held ({held})"
                    )
                acc.held -= fill.quantity
                acc.sold += fill.quantity
                acc.sell_value += value
                acc.sell_intended += intended
                acc.exit_fills += 1
                acc.exit_verdict = fill.verdict_id
                acc.exit_decision_time = proposal.created_at
                acc.exit_reason = reason
                if fill.risk_decision_id not in acc.exit_risk:
                    acc.exit_risk.append(fill.risk_decision_id)
            acc.fees += fill.fee
            acc.last_fill = fill.filled_at
            if acc.held == 0:
                done.append(_trade(acc))
                del books[key]
    trades = done + [_trade(acc) for acc in books.values()]
    trades.sort(key=lambda t: (t.decision_time, t.trade_id))
    return trades, position


# ---------------------------------------------------------------- DuckDB

_D = "DECIMAL(38,12)"
_COLUMNS: dict[str, str] = {
    "trade_id": "VARCHAR",
    "strategy_id": "VARCHAR",
    "verdict_id": "VARCHAR",
    "exit_verdict_id": "VARCHAR",
    "entry_risk_decision_ids": "VARCHAR[]",
    "exit_risk_decision_ids": "VARCHAR[]",
    "experiment_id": "VARCHAR",
    "market": "VARCHAR",
    "venue": "VARCHAR",
    "instrument": "VARCHAR",
    "side": "VARCHAR",
    "quote_currency": "VARCHAR",
    "status": "VARCHAR",
    "decision_time": "TIMESTAMPTZ",
    "order_time": "TIMESTAMPTZ",
    "entry_time": "TIMESTAMPTZ",
    "exit_decision_time": "TIMESTAMPTZ",
    "exit_time": "TIMESTAMPTZ",
    "holding_seconds": "DOUBLE",
    "quantity": _D,
    "intended_entry_price": _D,
    "entry_price": _D,
    "intended_exit_price": _D,
    "exit_price": _D,
    "entry_slippage_bps": "DOUBLE",
    "exit_slippage_bps": "DOUBLE",
    "entry_fills": "INTEGER",
    "exit_fills": "INTEGER",
    "fees": _D,
    "fee_currency": "VARCHAR",
    "spread_paid": _D,
    "fx_cost": _D,
    "gross_pnl": _D,
    "net_pnl": _D,
    "net_return": "DOUBLE",
    "max_adverse_excursion": _D,
    "max_favourable_excursion": _D,
    "entry_reason": "VARCHAR",
    "feature_snapshot": "VARCHAR",
    "feature_version": "VARCHAR",
    "judge_outputs": "VARCHAR",
    "model_versions": "VARCHAR",
    "exit_reason": "VARCHAR",
    "regime_after": "VARCHAR",
    "ledger_sequence": "BIGINT",
    "ledger_hash": "VARCHAR",
}


@dataclass(frozen=True)
class JournalMeta:
    trades: int
    ledger_sequence: int
    ledger_hash: str | None
    built_at: datetime


def journal_path(state_root: Path) -> Path:
    return state_root / "journal.duckdb"


def _connect(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute("SET TimeZone = 'UTC'")
    return con


def build_journal(
    ledger: LedgerLike, path: Path, *, clock: Any = lambda: datetime.now(UTC)
) -> JournalMeta:
    """Rebuild the journal from the whole ledger into a new file, then swap it in."""
    trades, pos = trades_from_ledger(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".building")
    tmp.unlink(missing_ok=True)
    meta = JournalMeta(len(trades), pos.sequence, pos.hash, clock())
    con = _connect(tmp)
    try:
        cols = ", ".join(f"{n} {t}" for n, t in _COLUMNS.items())
        con.execute(f"CREATE TABLE trades ({cols})")
        marks = ", ".join("?" for _ in _COLUMNS)
        rows = [
            [
                *(list(v) if isinstance(v, tuple) else v for v in t.__dict__.values()),
                pos.sequence,
                pos.hash,
            ]
            for t in trades
        ]
        if rows:
            con.executemany(f"INSERT INTO trades VALUES ({marks})", rows)
        con.execute(
            "CREATE TABLE journal_meta (schema_version INTEGER, trades INTEGER, "
            "ledger_sequence BIGINT, ledger_hash VARCHAR, built_at TIMESTAMPTZ)"
        )
        con.execute(
            "INSERT INTO journal_meta VALUES (?, ?, ?, ?, ?)",
            [SCHEMA_VERSION, meta.trades, meta.ledger_sequence, meta.ledger_hash, meta.built_at],
        )
    finally:
        con.close()
    os.replace(tmp, path)
    return meta


def export_journal(
    db: Path,
    out: Path,
    *,
    fmt: Literal["csv", "parquet"],
    start: datetime | None = None,
    end: datetime | None = None,
) -> int:
    """Write trades decided in [start, end) to CSV or Parquet; returns the row count.
    Every row carries `ledger_sequence` and `ledger_hash`."""
    if fmt not in ("csv", "parquet"):
        raise ValueError(f"format {fmt!r}: csv or parquet")
    where, params = [], []
    if start is not None:
        where.append("decision_time >= ?")
        params.append(start)
    if end is not None:
        where.append("decision_time < ?")
        params.append(end)
    sql = "SELECT * FROM trades"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY decision_time, trade_id"
    con = _connect(db, read_only=True)
    try:
        rel = con.sql(sql, params=params)
        n = len(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            rel.write_csv(str(out), header=True)
        else:
            rel.write_parquet(str(out))
        return n
    finally:
        con.close()
