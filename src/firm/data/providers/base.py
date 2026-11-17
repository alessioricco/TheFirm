"""The contract every market-data source implements.

A provider turns "give me bars (or trades) for this symbol" into a plan of
HTTP requests, and the bytes that come back into canonical rows. It never
does I/O itself: `firm.data.fetch` runs the plan (retries, politeness,
captures), so every provider is testable with recorded-format synthetic
payloads and no network.

Rules every parser follows (golden rule 3):

- A bar is emitted only if it had closed when it was captured
  (`close_time <= captured_at`); a still-forming bar never enters the store.
- Prices are stored unadjusted. A source that only serves adjusted prices
  must undo the adjustment (see `yahoo`) or say so in `price_adjustment`.
- Bad payloads raise `ProviderError`; nothing is silently dropped except
  rows the source itself marks as empty.

To add a source: write a module in this package with a `Provider` subclass,
register an instance at import (`register(MySource())`), add the import to
`__init__.py`, and add tests with synthetic payloads in its exact format.
A library-backed provider (yfinance, ccxt, ...) fits the same contract:
its request plan names calls instead of URLs, and it needs owner approval
as a new dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, TypedDict

import pandas as pd

from firm.core.timeframes import alignment_for, tf_seconds

NS = 1_000_000_000
Kind = Literal["bars", "trades"]
Adjustment = Literal["none", "split_adjusted", "split_and_dividend_adjusted"]


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


class RulesRow(TypedDict):
    """A venue's trading rules for one instrument, as published (strings, exact)."""

    tick_size: str  # price increment
    lot_size: str  # quantity increment
    min_quantity: str
    min_notional: str  # in the quote currency
    tradable: bool


class TradeRow(TypedDict):
    time_ns: int
    trade_id: str
    price: float
    size: float
    taker_side: str  # always the TAKER's side: "buy" | "sell"


@dataclass(frozen=True)
class Request:
    """One HTTP GET in a fetch plan."""

    url: str
    kind: Kind
    note: str = ""


def ns(ts: datetime) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return int(t.value)


def check_bar(row: BarRow) -> BarRow:
    lo, hi = row["low"], row["high"]
    if not (lo <= min(row["open"], row["close"]) and max(row["open"], row["close"]) <= hi):
        raise ProviderError(f"inconsistent OHLC at {row['open_time_ns']}: {row}")
    if row["volume"] < 0:
        raise ProviderError(f"negative volume at {row['open_time_ns']}")
    return row


def closed_only(rows: list[BarRow], captured_at: datetime) -> list[BarRow]:
    cutoff = ns(captured_at)
    return [r for r in rows if r["close_time_ns"] <= cutoff]


def known_trades(rows: list[TradeRow], captured_at: datetime) -> list[TradeRow]:
    cutoff = ns(captured_at)
    return [t for t in rows if t["time_ns"] <= cutoff]


def windows(
    start: datetime, end: datetime, tf: str, max_bars: int
) -> list[tuple[datetime, datetime]]:
    """Contiguous [lo, hi) windows of at most `max_bars` bars covering [start, end)."""
    step = pd.Timedelta(seconds=tf_seconds(tf) * max_bars)
    out, t = [], pd.Timestamp(start)
    stop = pd.Timestamp(end)
    while t < stop:
        hi = min(t + step, stop)
        out.append((t.to_pydatetime(), hi.to_pydatetime()))
        t = hi
    return out


class Provider(ABC):
    """A market-data source. Subclasses set the class attributes and parsers."""

    name: ClassVar[str]
    description: ClassVar[str]
    kinds: ClassVar[frozenset[Kind]]
    timeframes: ClassVar[frozenset[str]]
    max_bars_per_request: ClassVar[int | None]  # None: one request returns the whole range
    min_interval_s: ClassVar[float]  # pause between requests (public rate limits)
    price_adjustment: ClassVar[Adjustment] = "none"  # of the RAW bytes, before parsing
    # How the source aligns its bars: "utc" days (00:00 UTC) or exchange "session"s;
    # and which weekday its weekly bars start on (None: no weekly bars).
    day_boundary: ClassVar[Literal["utc", "session"]] = "utc"
    week_start: ClassVar[Literal["monday", "thursday"] | None] = None
    licence: ClassVar[str]
    history_note: ClassVar[str] = ""

    def alignment(self, tf: str) -> str:
        """How this source's `tf` bars are aligned (see firm.core.timeframes)."""
        return alignment_for(tf, day_boundary=self.day_boundary, week_start=self.week_start)

    def supports(self, kind: Kind, tf: str | None = None) -> None:
        if kind not in self.kinds:
            raise ProviderError(f"{self.name} has no {kind}")
        if kind == "bars" and tf not in self.timeframes:
            raise ProviderError(f"{self.name} has no {tf} bars; has {sorted(self.timeframes)}")

    # --- bars

    @abstractmethod
    def bar_requests(
        self, symbol: str, tf: str, *, start: datetime | None, end: datetime
    ) -> list[Request]:
        """The requests that cover [start, end) (or what the source allows)."""

    @abstractmethod
    def parse_bars(self, payload: bytes, *, tf: str, captured_at: datetime) -> list[BarRow]:
        """Closed bars, oldest first, unadjusted."""

    # --- trades (cursor-paged; optional)

    def trade_request(self, symbol: str, cursor: str | None) -> Request:
        raise ProviderError(f"{self.name} has no trades")

    def next_trade_cursor(self, payload: bytes) -> str | None:
        """Cursor for the next page, or None when there is nothing further."""
        raise ProviderError(f"{self.name} has no trades")

    def parse_trades(self, payload: bytes, *, captured_at: datetime) -> list[TradeRow]:
        raise ProviderError(f"{self.name} has no trades")

    # --- instrument rules (optional): tick, lot, minimums, as the venue publishes them

    def rules_url(self, symbol: str) -> str:
        raise ProviderError(f"{self.name} publishes no instrument rules")

    def parse_rules(self, payload: bytes, symbol: str) -> RulesRow:
        raise ProviderError(f"{self.name} publishes no instrument rules")
