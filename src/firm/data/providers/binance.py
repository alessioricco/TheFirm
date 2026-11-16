"""Binance spot, public market data (no key).

Host: `https://data-api.binance.vision` (Binance's market-data-only endpoint,
public, no key, same responses as api.binance.com). Binance is a DATA source
here; trading there is not permitted by the UK jurisdiction profile.

- Klines `/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime=<ms>&endTime=<ms>&limit=1000`:
  `[[openTime_ms,"o","h","l","c","volume",closeTime_ms,"quoteVol",trades,"takerBase","takerQuote","0"],...]`,
  oldest first, at most 1000. The last bar may still be forming.
- Aggregate trades `/api/v3/aggTrades?symbol=BTCUSDT&limit=1000[&fromId=<id>]`:
  `[{"a":id,"p":"price","q":"qty","f":first,"l":last,"T":ms,"m":isBuyerMaker,"M":bool},...]`;
  `m` true means the buyer was the maker, so the taker SOLD.
- Errors: `{"code":-1121,"msg":"Invalid symbol."}`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from firm.core.timeframes import tf_seconds
from firm.data.providers.base import (
    NS,
    BarRow,
    Provider,
    ProviderError,
    Request,
    TradeRow,
    check_bar,
    closed_only,
    known_trades,
    windows,
)
from firm.data.providers.registry import register

API = "https://data-api.binance.vision/api/v3"
INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"}
LIMIT = 1000


def _list(payload: bytes) -> list[Any]:
    doc = json.loads(payload)
    if isinstance(doc, dict) and "code" in doc:
        raise ProviderError(f"binance error {doc.get('code')}: {doc.get('msg')}")
    if not isinstance(doc, list):
        raise ProviderError(f"binance: unexpected payload {str(doc)[:200]}")
    return doc


def _ms(ts: datetime) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


class Binance(Provider):
    name = "binance"
    description = "Binance spot, public market-data REST"
    kinds = frozenset({"bars", "trades"})
    timeframes = frozenset(INTERVALS)
    max_bars_per_request = LIMIT
    min_interval_s = 0.25
    licence = "binance public market data: personal use; check terms before redistribution"
    history_note = "klines from each pair's listing (BTCUSDT: 2017-08), 1000 per request"

    def bar_requests(
        self, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> list[Request]:
        self.supports("bars", tf)
        if start is None:
            start = (
                pd.Timestamp(end) - pd.Timedelta(seconds=tf_seconds(tf) * LIMIT)
            ).to_pydatetime()
        out = []
        for lo, hi in windows(start, end, tf, LIMIT):
            q = urlencode(
                {
                    "symbol": symbol,
                    "interval": INTERVALS[tf],
                    "startTime": _ms(lo),
                    "endTime": _ms(hi) - 1,  # inclusive on Binance: stop before the next window
                    "limit": LIMIT,
                }
            )
            out.append(Request(f"{API}/klines?{q}", "bars"))
        return out

    def parse_bars(self, payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
        step = tf_seconds(tf) * NS
        rows = [
            check_bar(
                BarRow(
                    open_time_ns=int(r[0]) * 1_000_000,
                    close_time_ns=int(r[0]) * 1_000_000 + step,
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[5]),
                )
            )
            for r in _list(payload)
        ]
        rows.sort(key=lambda r: r["open_time_ns"])
        return closed_only(rows, captured_at)

    def trade_request(self, symbol: str, cursor: str | None) -> Request:
        q: dict[str, Any] = {"symbol": symbol, "limit": LIMIT}
        if cursor is not None:
            q["fromId"] = cursor
        return Request(f"{API}/aggTrades?{urlencode(q)}", "trades")

    def next_trade_cursor(self, payload: bytes) -> str | None:
        rows = _list(payload)
        if len(rows) < LIMIT:  # a short page means we've caught up
            return None
        return str(max(int(t["a"]) for t in rows) + 1)

    def parse_trades(self, payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
        out = [
            TradeRow(
                time_ns=int(t["T"]) * 1_000_000,
                trade_id=str(t["a"]),
                price=float(t["p"]),
                size=float(t["q"]),
                taker_side="sell" if t["m"] else "buy",  # m: buyer was maker
            )
            for t in _list(payload)
        ]
        out.sort(key=lambda t: (t["time_ns"], t["trade_id"]))
        return known_trades(out, captured_at)


PROVIDER = register(Binance())
