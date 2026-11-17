"""Coinbase Exchange public REST (no key).

- Candles `/products/BTC-USD/candles?granularity=86400&start=&end=`:
  `[[time, low, high, open, close, volume], ...]`, newest first, at most 300.
  No 4h or 1w granularity.
- Trades `/products/BTC-USD/trades[?after=<id>]`:
  `[{"time":iso,"trade_id":n,"price":"p","size":"s","side":"buy|sell"}, ...]`;
  `side` is the MAKER's side, so the taker's is the opposite.
- Rules `/products/BTC-USD`: `{"id":"BTC-USD","quote_increment":"0.01",
  "base_increment":"0.00000001","min_market_funds":"1","status":"online",
  "trading_disabled":false,...}` (no separate minimum size: one increment).
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode

import pandas as pd

from firm.core.timeframes import tf_seconds
from firm.data.providers.base import (
    NS,
    BarRow,
    Provider,
    ProviderError,
    Request,
    RulesRow,
    TradeRow,
    check_bar,
    closed_only,
    known_trades,
    windows,
)
from firm.data.providers.registry import register

API = "https://api.exchange.coinbase.com"
GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def _list(payload: bytes) -> list:
    doc = json.loads(payload)
    if not isinstance(doc, list):
        raise ProviderError(f"coinbase error: {doc}")
    return doc


class Coinbase(Provider):
    name = "coinbase"
    description = "Coinbase Exchange spot, public REST"
    kinds = frozenset({"bars", "trades"})
    timeframes = frozenset(GRANULARITY)
    max_bars_per_request = 300
    min_interval_s = 0.4
    licence = "coinbase public market data: personal use; check terms before redistribution"
    history_note = "candles back to 2015 for BTC-USD, 300 per request"

    def bar_requests(
        self, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> list[Request]:
        self.supports("bars", tf)
        if start is None:
            start = (pd.Timestamp(end) - pd.Timedelta(seconds=tf_seconds(tf) * 300)).to_pydatetime()
        out = []
        for lo, hi in windows(start, end, tf, 300):
            q = urlencode(
                {
                    "granularity": GRANULARITY[tf],
                    "start": pd.Timestamp(lo).isoformat(),
                    "end": pd.Timestamp(hi).isoformat(),
                }
            )
            out.append(Request(f"{API}/products/{symbol}/candles?{q}", "bars"))
        return out

    def parse_bars(self, payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
        step = tf_seconds(tf) * NS
        rows = [
            check_bar(
                BarRow(
                    open_time_ns=int(r[0]) * NS,
                    close_time_ns=int(r[0]) * NS + step,
                    open=float(r[3]),
                    high=float(r[2]),
                    low=float(r[1]),
                    close=float(r[4]),
                    volume=float(r[5]),
                )
            )
            for r in _list(payload)
        ]
        rows.sort(key=lambda r: r["open_time_ns"])
        return closed_only(rows, captured_at)

    def trade_request(self, symbol: str, cursor: str | None) -> Request:
        suffix = f"?{urlencode({'after': cursor})}" if cursor is not None else ""
        return Request(f"{API}/products/{symbol}/trades{suffix}", "trades")

    def next_trade_cursor(self, payload: bytes) -> str | None:
        rows = _list(payload)
        return str(min(int(t["trade_id"]) for t in rows)) if rows else None

    def parse_trades(self, payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
        out = [
            TradeRow(
                time_ns=int(pd.Timestamp(t["time"]).value),
                trade_id=str(t["trade_id"]),
                price=float(t["price"]),
                size=float(t["size"]),
                taker_side={"buy": "sell", "sell": "buy"}[t["side"]],  # side is the maker's
            )
            for t in _list(payload)
        ]
        out.sort(key=lambda t: (t["time_ns"], t["trade_id"]))
        return known_trades(out, captured_at)

    def rules_url(self, symbol: str) -> str:
        return f"{API}/products/{symbol}"

    def parse_rules(self, payload: bytes, symbol: str) -> RulesRow:
        p = json.loads(payload)
        if p.get("id") != symbol:
            raise ProviderError(f"coinbase: expected product {symbol}, got {p.get('message', p)}")
        return RulesRow(
            tick_size=str(p["quote_increment"]),
            lot_size=str(p["base_increment"]),
            min_quantity=str(p["base_increment"]),
            min_notional=str(p["min_market_funds"]),
            tradable=p.get("status") == "online" and not p.get("trading_disabled", False),
        )


PROVIDER = register(Coinbase())
