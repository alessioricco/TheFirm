"""Kraken public REST (no key).

- OHLC `/0/public/OHLC?pair=XBTUSD&interval=1440[&since=<s>]`:
  `{"error":[],"result":{"XXBTZUSD":[[time,"o","h","l","c","vwap","vol",count],...],"last":s}}`;
  `time` is the bar's open (unix s). Only the most recent 720 bars are served,
  and the last one is still forming.
- Trades `/0/public/Trades?pair=XBTUSD[&since=<ns>]`:
  `[["price","vol",time,"b|s","m|l","misc",id],...]`, side is the taker's; the
  `last` field is the cursor for the next page.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

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
)
from firm.data.providers.registry import register

API = "https://api.kraken.com/0/public"
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def _result(payload: bytes) -> tuple[list[Any], Any]:
    doc = json.loads(payload)
    if doc.get("error"):
        raise ProviderError(f"kraken error: {doc['error']}")
    series = [v for k, v in doc["result"].items() if k != "last"]
    if len(series) != 1:
        raise ProviderError(f"expected one pair in kraken result, got {len(series)}")
    return series[0], doc["result"].get("last")


class Kraken(Provider):
    name = "kraken"
    description = "Kraken spot, public REST"
    kinds = frozenset({"bars", "trades"})
    timeframes = frozenset(INTERVAL_MINUTES)
    max_bars_per_request = 720
    min_interval_s = 1.2
    licence = "kraken public market data: personal use; check terms before redistribution"
    history_note = "OHLC: last 720 bars per timeframe only; older history needs trades"
    week_start = "thursday"  # weekly bars are epoch-aligned; 1970-01-01 was a Thursday

    def bar_requests(
        self, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> list[Request]:
        self.supports("bars", tf)
        q = urlencode({"pair": symbol, "interval": INTERVAL_MINUTES[tf]})
        return [Request(f"{API}/OHLC?{q}", "bars", "last 720 bars (Kraken limit)")]

    def parse_bars(self, payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
        step = tf_seconds(tf) * NS
        rows, _ = _result(payload)
        out = [
            check_bar(
                BarRow(
                    open_time_ns=int(r[0]) * NS,
                    close_time_ns=int(r[0]) * NS + step,
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=float(r[6]),
                )
            )
            for r in rows
        ]
        return closed_only(out, captured_at)

    def trade_request(self, symbol: str, cursor: str | None) -> Request:
        q: dict[str, Any] = {"pair": symbol}
        if cursor is not None:
            q["since"] = cursor
        return Request(f"{API}/Trades?{urlencode(q)}", "trades")

    def next_trade_cursor(self, payload: bytes) -> str | None:
        rows, last = _result(payload)
        return str(last) if rows and last is not None else None

    def parse_trades(self, payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
        rows, _ = _result(payload)
        out = [
            TradeRow(
                time_ns=round(float(r[2]) * 1_000_000) * 1_000,  # microsecond precision
                trade_id=str(r[6]) if len(r) > 6 else f"{r[2]}-{r[0]}-{r[1]}",
                price=float(r[0]),
                size=float(r[1]),
                taker_side={"b": "buy", "s": "sell"}[r[3]],
            )
            for r in rows
        ]
        return known_trades(out, captured_at)


PROVIDER = register(Kraken())
