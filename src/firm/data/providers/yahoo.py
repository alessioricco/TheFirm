"""Yahoo Finance chart endpoint (unofficial, no key). Use with care.

`https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?period1=<s>&period2=<s>&interval=1d&events=div,split`
-> `{"chart":{"result":[{"meta":{...},"timestamp":[...],
     "indicators":{"quote":[{"open":[],"high":[],"low":[],"close":[],"volume":[]}],
                   "adjclose":[{"adjclose":[]}]},
     "events":{"splits":{"<ts>":{"date":ts,"numerator":n,"denominator":d}},
               "dividends":{...}}}],"error":null}}`

- Not an official API: it can change, rate-limit (HTTP 429) or disappear
  without notice. Yahoo's terms allow personal use only; we never
  redistribute (the store is git-ignored).
- OHLC and volume are SPLIT-ADJUSTED relative to TODAY (adjusting is
  lookahead: a split today rewrites old prices). The parser undoes it with
  the split events in the same response, so stored prices are what traded at
  the time. Yahoo only returns events inside the requested window, so the
  plan always asks up to a far-future `period2`: the response then carries
  every split up to now (and the bars after `end`, which is harmless).
  Found by a live check: a January-2024-only request for NVDA returned
  prices ten times too low (its June 2024 10:1 split was outside the window).
  Dividends are not applied to OHLC (only to `adjclose`, which we ignore).
- `timestamp` is the bar's open. For daily equity bars the real close is a
  few hours later; we set `close_time = open + 1 day`, which is later than
  the true close, so a bar is never treated as known too early.
- Rows with missing OHLC (halts, holidays in some feeds) are skipped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import pandas as pd

from firm.core.timeframes import tf_seconds
from firm.data.providers.base import (
    NS,
    BarRow,
    Provider,
    ProviderError,
    Request,
    check_bar,
    closed_only,
)
from firm.data.providers.registry import register

API = "https://query1.finance.yahoo.com/v8/finance/chart"
INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d", "1w": "1wk"}
FAR_FUTURE = 4_102_444_800  # 2100-01-01: makes Yahoo include every split up to now


def _result(payload: bytes) -> dict[str, Any]:
    doc = json.loads(payload)
    chart = doc.get("chart") if isinstance(doc, dict) else None
    if not chart or chart.get("error") or not chart.get("result"):
        raise ProviderError(f"yahoo error: {chart.get('error') if chart else str(doc)[:200]}")
    result: dict[str, Any] = chart["result"][0]
    return result


def split_factors(timestamps: list[int], splits: dict[str, Any]) -> list[float]:
    """Factor that turns each split-adjusted price back into the traded price.

    A bar before a split of n:d (n new shares for d old) traded at n/d times
    its adjusted price; factors multiply across later splits.
    """
    events = sorted(
        (int(s["date"]), float(s["numerator"]) / float(s["denominator"])) for s in splits.values()
    )
    out = []
    for t in timestamps:
        f = 1.0
        for date, ratio in events:
            if t < date:
                f *= ratio
        out.append(f)
    return out


class Yahoo(Provider):
    name = "yahoo"
    description = "Yahoo Finance chart API (unofficial), equities and indices"
    kinds = frozenset({"bars"})
    timeframes = frozenset(INTERVALS)
    max_bars_per_request = None  # one request returns the whole range (intraday ranges are capped)
    min_interval_s = 1.0
    price_adjustment = "split_adjusted"  # raw bytes; parse_bars undoes it
    day_boundary = "session"  # bars start at the exchange session open, not 00:00 UTC
    week_start = "monday"
    licence = "yahoo finance: personal use only, never redistribute; unofficial endpoint"
    history_note = "daily history for decades; intraday limited (1m: ~7 days, 60m: ~730 days)"

    def bar_requests(
        self, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> list[Request]:
        self.supports("bars", tf)
        lo = int(pd.Timestamp(start).timestamp()) if start is not None else 0
        q = urlencode(
            {
                "period1": lo,
                "period2": FAR_FUTURE,  # see module doc: events only come inside the window
                "interval": INTERVALS[tf],
                "events": "div,split",
            }
        )
        return [Request(f"{API}/{quote(symbol)}?{q}", "bars")]

    def parse_bars(self, payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
        res = _result(payload)
        ts: list[int] = res.get("timestamp") or []
        quote_ = res["indicators"]["quote"][0]
        factors = split_factors(ts, (res.get("events") or {}).get("splits") or {})
        step = tf_seconds(tf) * NS
        rows = []
        for i, t in enumerate(ts):
            o, h, lo, c = (quote_[k][i] for k in ("open", "high", "low", "close"))
            if None in (o, h, lo, c):
                continue
            f = factors[i]
            v = quote_["volume"][i] or 0
            rows.append(
                check_bar(
                    BarRow(
                        open_time_ns=int(t) * NS,
                        close_time_ns=int(t) * NS + step,
                        open=float(o) * f,
                        high=float(h) * f,
                        low=float(lo) * f,
                        close=float(c) * f,
                        volume=float(v) / f,
                    )
                )
            )
        rows.sort(key=lambda r: r["open_time_ns"])
        return closed_only(rows, captured_at)


PROVIDER = register(Yahoo())
