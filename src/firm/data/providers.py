"""Kraken and Coinbase public market data: request URLs and response parsers.

Parsers are pure: provider bytes in, canonical rows out. A bar is emitted
only if it had closed when it was captured (`close_time <= captured_at`),
so a still-forming candle never enters the store (golden rule 3).

Formats (public REST, no keys needed):

- Kraken OHLC   `/0/public/OHLC?pair=XBTUSD&interval=1440[&since=<s>]`
  `{"error":[],"result":{"XXBTZUSD":[[time,"o","h","l","c","vwap","vol",count],...],"last":s}}`
  `time` is the bar's open (unix seconds). Only the last 720 bars are served.
- Kraken trades `/0/public/Trades?pair=XBTUSD[&since=<ns>]`
  `{"error":[],"result":{"XXBTZUSD":[["price","vol",time,"b|s","m|l","misc",id],...],"last":"ns"}}`
  side is the taker's side.
- Coinbase candles `/products/BTC-USD/candles?granularity=86400&start=&end=`
  `[[time, low, high, open, close, volume], ...]`, newest first, max 300 per call.
- Coinbase trades `/products/BTC-USD/trades[?after=<id>]`
  `[{"time":iso,"trade_id":n,"price":"p","size":"s","side":"buy|sell"}, ...]`
  side is the **maker's** side; the taker's is the opposite.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypedDict
from urllib.parse import urlencode

import pandas as pd

from firm.core.timeframes import tf_seconds

KRAKEN_API = "https://api.kraken.com/0/public"
COINBASE_API = "https://api.exchange.coinbase.com"

KRAKEN_INTERVAL_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}
COINBASE_GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

NS = 1_000_000_000


class ProviderError(ValueError):
    """The provider response is malformed or reports an error."""


class BarRow(TypedDict):
    open_time_ns: int
    close_time_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class TradeRow(TypedDict):
    time_ns: int
    trade_id: str
    price: float
    size: float
    taker_side: str


def _ns(ts: datetime) -> int:
    return int(pd.Timestamp(ts).value)


def _check_bar(row: BarRow) -> BarRow:
    lo, hi = row["low"], row["high"]
    if not (lo <= min(row["open"], row["close"]) and max(row["open"], row["close"]) <= hi):
        raise ProviderError(f"inconsistent OHLC at {row['open_time_ns']}: {row}")
    if row["volume"] < 0:
        raise ProviderError(f"negative volume at {row['open_time_ns']}")
    return row


def _closed(rows: list[BarRow], captured_at: datetime) -> list[BarRow]:
    cutoff = _ns(captured_at)
    return [r for r in rows if r["close_time_ns"] <= cutoff]


# --------------------------------------------------------------------------- Kraken


def kraken_ohlc_url(pair: str, tf: str, since: int | None = None) -> str:
    q: dict[str, Any] = {"pair": pair, "interval": KRAKEN_INTERVAL_MINUTES[tf]}
    if since is not None:
        q["since"] = since
    return f"{KRAKEN_API}/OHLC?{urlencode(q)}"


def kraken_trades_url(pair: str, since_ns: int | None = None) -> str:
    q: dict[str, Any] = {"pair": pair}
    if since_ns is not None:
        q["since"] = since_ns
    return f"{KRAKEN_API}/Trades?{urlencode(q)}"


def _kraken_result(payload: bytes) -> list[Any]:
    doc = json.loads(payload)
    if doc.get("error"):
        raise ProviderError(f"kraken error: {doc['error']}")
    series = [v for k, v in doc["result"].items() if k != "last"]
    if len(series) != 1:
        raise ProviderError(f"expected one pair in kraken result, got {len(series)}")
    rows: list[Any] = series[0]
    return rows


def parse_kraken_ohlc(payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
    step = tf_seconds(tf) * NS
    rows = [
        _check_bar(
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
        for r in _kraken_result(payload)
    ]
    return _closed(rows, captured_at)


def parse_kraken_trades(payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
    out: list[TradeRow] = []
    for r in _kraken_result(payload):
        side = {"b": "buy", "s": "sell"}[r[3]]
        out.append(
            TradeRow(
                time_ns=round(float(r[2]) * 1_000_000) * 1_000,  # microsecond precision
                trade_id=str(r[6]) if len(r) > 6 else f"{r[2]}-{r[0]}-{r[1]}",
                price=float(r[0]),
                size=float(r[1]),
                taker_side=side,
            )
        )
    cutoff = _ns(captured_at)
    return [t for t in out if t["time_ns"] <= cutoff]


# --------------------------------------------------------------------------- Coinbase


def coinbase_candles_url(product: str, tf: str, start: datetime, end: datetime) -> str:
    q = {
        "granularity": COINBASE_GRANULARITY[tf],
        "start": pd.Timestamp(start).isoformat(),
        "end": pd.Timestamp(end).isoformat(),
    }
    return f"{COINBASE_API}/products/{product}/candles?{urlencode(q)}"


def coinbase_trades_url(product: str, after: int | None = None) -> str:
    suffix = f"?{urlencode({'after': after})}" if after is not None else ""
    return f"{COINBASE_API}/products/{product}/trades{suffix}"


def parse_coinbase_candles(payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
    doc = json.loads(payload)
    if not isinstance(doc, list):
        raise ProviderError(f"coinbase error: {doc}")
    step = tf_seconds(tf) * NS
    rows = [
        _check_bar(
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
        for r in doc
    ]
    rows.sort(key=lambda r: r["open_time_ns"])
    return _closed(rows, captured_at)


def parse_coinbase_trades(payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
    doc = json.loads(payload)
    if not isinstance(doc, list):
        raise ProviderError(f"coinbase error: {doc}")
    out = [
        TradeRow(
            time_ns=int(pd.Timestamp(t["time"]).value),
            trade_id=str(t["trade_id"]),
            price=float(t["price"]),
            size=float(t["size"]),
            taker_side={"buy": "sell", "sell": "buy"}[t["side"]],  # side is the maker's
        )
        for t in doc
    ]
    out.sort(key=lambda t: (t["time_ns"], t["trade_id"]))
    cutoff = _ns(captured_at)
    return [t for t in out if t["time_ns"] <= cutoff]


# --------------------------------------------------------------------------- dispatch

BAR_PARSERS = {"kraken": parse_kraken_ohlc, "coinbase": parse_coinbase_candles}
TRADE_PARSERS = {"kraken": parse_kraken_trades, "coinbase": parse_coinbase_trades}
