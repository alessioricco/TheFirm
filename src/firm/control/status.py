"""Paper trading status from the ledger alone (`firm paper status`, dashboard).

Read-only, and it never talks to a running paper run: everything comes from
the ledger that run writes (one per machine), plus the instrument records
for currencies. So it works while a run is going, after it stopped, or on a
copy of the state.

- **accounts**: per market and venue, the opening balances, the balances
  now (opening plus every fill since), the number of fills and the last one;
- **runs**: whether the last start has a matching stop. A run killed without
  a chance to record its stop still shows as started: the ledger can't know.
- **feeds**: the latest status of each live stream (fresh, stale, ...);
- **heartbeat**: the latest risk-heartbeat state per market;
- **halts**: the risk halts recorded (kill switch, drawdown, daily loss);
- **trades**: closed and open trades and net P&L per quote currency, from the
  same rebuild as the trade journal.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from firm.control.paper import OPENED
from firm.core.domain import Fill, LedgerEventType, Side
from firm.data.reference import latest_instruments
from firm.ledger.journal import JournalError, trades_from_ledger
from firm.ledger.store import Ledger


def _currencies(data_root: Path | None, venue: str, instrument: str) -> tuple[str, str] | None:
    if data_root is not None:
        try:
            rec = latest_instruments(data_root).get((venue, instrument))
        except (OSError, ValueError):
            rec = None
        if rec is not None:
            return rec[1].base, rec[1].quote
    parts = instrument.split("-")  # crypto firm IDs are BASE-QUOTE
    return (parts[0], parts[1]) if len(parts) == 2 and "." not in instrument else None


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def paper_status(ledger: Ledger, data_root: Path | None = None) -> dict[str, Any]:
    accounts: dict[tuple[str, str], dict[str, Any]] = {}
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    feeds: dict[tuple[str, str], dict[str, Any]] = {}
    heartbeat: dict[str, dict[str, Any]] = {}
    halts: list[dict[str, Any]] = []
    fills_by_venue: dict[str, list[Fill]] = defaultdict(list)
    last_sequence, events = None, 0
    for e in ledger.read():
        events += 1
        last_sequence = e.sequence
        p, t = e.payload, e.event_type
        if t is LedgerEventType.MARKET_STATUS:
            key = (p.get("market"), p.get("venue"))
            kind = p.get("kind")
            if kind == OPENED:
                accounts[key] = {
                    "market": key[0],
                    "venue": key[1],
                    "opened_at": p.get("at") or e.recorded_at.isoformat(),
                    "opening": dict(p["balances"]),
                    "opened_at_sequence": e.sequence,
                }
                fills_by_venue[key[1]] = []  # an account opened again starts over
            elif kind == "started":
                runs[key] = {
                    "market": key[0],
                    "venue": key[1],
                    "running": True,
                    "verdict_id": p.get("verdict_id"),
                    "started_at": p.get("at"),
                    "stopped_at": None,
                    "failure": None,
                }
            elif kind == "stopped" and key in runs:
                runs[key] |= {
                    "running": False,
                    "stopped_at": p.get("at"),
                    "failure": p.get("failure"),
                }
            elif kind in ("heartbeat_lost", "heartbeat_restored"):
                heartbeat[p.get("market")] = {
                    "state": "lost" if kind == "heartbeat_lost" else "ok",
                    "at": p.get("at"),
                }
        elif t is LedgerEventType.FILL:
            fills_by_venue[p.get("venue")].append(Fill.model_validate(p))
        elif t is LedgerEventType.FEED_STATUS and "status" in p:
            feeds[(p.get("provider"), p.get("symbol"))] = {
                "provider": p.get("provider"),
                "symbol": p.get("symbol"),
                "status": p.get("status"),
                "detail": p.get("detail"),
                "at": p.get("at"),
            }
        elif t is LedgerEventType.RISK_HALT:
            halts.append(
                {
                    "scope": p.get("scope"),
                    "market": p.get("market"),
                    "reason": p.get("reason"),
                    "at": p.get("issued_at"),
                }
            )

    for (_market, venue), acc in accounts.items():
        fills = fills_by_venue.get(venue, [])
        balances: dict[str, Decimal] | None = {c: Decimal(v) for c, v in acc["opening"].items()}
        for f in fills:
            cur = _currencies(data_root, venue, f.instrument)
            if cur is None or balances is None:
                balances = None
                continue
            base, quote = cur
            sign = 1 if f.side is Side.BUY else -1
            balances[base] = balances.get(base, Decimal(0)) + sign * f.quantity
            balances[quote] = balances.get(quote, Decimal(0)) - sign * f.quantity * f.price
            balances[f.fee_currency] = balances.get(f.fee_currency, Decimal(0)) - f.fee
        acc["balances"] = {c: _plain(v) for c, v in balances.items()} if balances else None
        acc["fills"] = len(fills)
        acc["last_fill"] = (
            {
                "at": fills[-1].filled_at.isoformat(),
                "instrument": fills[-1].instrument,
                "side": fills[-1].side.value,
                "quantity": _plain(fills[-1].quantity),
                "price": _plain(fills[-1].price),
            }
            if fills
            else None
        )
        del acc["opened_at_sequence"]

    pnl: dict[str, Decimal] = defaultdict(Decimal)
    try:
        trades, _pos = trades_from_ledger(ledger)
        trade_error = None
    except JournalError as exc:  # the rebuild is strict; status still shows the rest
        trades, trade_error = [], str(exc)
    for tr in trades:
        if tr.status == "closed" and tr.net_pnl is not None:
            pnl[tr.quote_currency] += tr.net_pnl
    return {
        "ledger": {"events": events, "last_sequence": last_sequence},
        "accounts": list(accounts.values()),
        "runs": list(runs.values()),
        "feeds": list(feeds.values()),
        "heartbeat": heartbeat,
        "halts": halts[-20:],
        "trades": {
            "closed": sum(1 for tr in trades if tr.status == "closed"),
            "open": sum(1 for tr in trades if tr.status == "open"),
            "net_pnl": {c: _plain(v) for c, v in pnl.items()},
            "error": trade_error,
        },
    }


def describe(status: dict[str, Any]) -> list[str]:
    """`firm paper status` as text."""
    lines = [f"ledger: {status['ledger']['events']} events"]
    if not status["accounts"] and not status["runs"]:
        lines.append("no paper account in this ledger yet (`firm paper run` opens one)")
    for r in status["runs"]:
        state = "running (last start has no stop)" if r["running"] else "stopped"
        if r["failure"]:
            state += f" by a failure: {r['failure']}"
        lines.append(
            f"run {r['market']} on {r['venue']}: {state}; verdict {r['verdict_id']}, "
            f"started {r['started_at']}"
            + (f", stopped {r['stopped_at']}" if r["stopped_at"] else "")
        )
    for a in status["accounts"]:
        bal = a["balances"]
        lines.append(
            f"account {a['market']} on {a['venue']} (opened {a['opened_at']}): "
            + (", ".join(f"{c} {v}" for c, v in bal.items()) if bal else "balances unknown")
            + f"; {a['fills']} fills"
        )
        if a["last_fill"]:
            lf = a["last_fill"]
            lines.append(
                f"  last fill {lf['at']}: {lf['side']} {lf['quantity']} {lf['instrument']} "
                f"@ {lf['price']}"
            )
    t = status["trades"]
    if t["error"]:
        lines.append(f"trades: can't rebuild from this ledger ({t['error']})")
    else:
        pnl = ", ".join(f"{c} {v}" for c, v in t["net_pnl"].items()) or "none closed"
        lines.append(f"trades: {t['closed']} closed, {t['open']} open; net P&L {pnl}")
    for f in status["feeds"]:
        lines.append(f"feed {f['provider']} {f['symbol']}: {f['status']} ({f['at']})")
    for market, hb in status["heartbeat"].items():
        lines.append(f"risk heartbeat {market}: {hb['state']} ({hb['at']})")
    for h in status["halts"]:
        lines.append(f"halt {h['scope']} {h['market'] or ''}: {h['reason']} ({h['at']})")
    return lines
