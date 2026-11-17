"""Live market data over WebSockets: the streaming side of the provider layer.

A stream provider says where to connect, what to send to subscribe, and how
to turn each message into canonical trade rows (with the TAKER's side).
It does no I/O itself: `feed.Feed` connects, reconnects, stores captures and
tracks staleness, so providers are testable with synthetic messages in their
exact formats.

Live trades are stored as raw captures (JSON lines of `{received_at, message}`)
so live and backtest data are the same data; `parse_capture` reads them back
for the normaliser. Bars are built from trades by `bars.BarBuilder`, closed
only once their period has passed (golden rule 3).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

import pandas as pd

from firm.data.providers.base import TradeRow, known_trades


class StreamError(RuntimeError):
    """The stream reported an error (bad subscription, unknown symbol, ...)."""


SymbolTrades = list[tuple[str, TradeRow]]  # (provider symbol, trade)


def iso_ns(ts: str) -> int:
    return int(pd.Timestamp(ts).value)


class StreamProvider(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    licence: ClassVar[str]

    @abstractmethod
    def url(self, symbols: list[str]) -> str: ...

    def subscribe_messages(self, symbols: list[str]) -> list[str]:
        """JSON messages to send after connecting (empty if the URL subscribes)."""
        return []

    @abstractmethod
    def parse(self, message: str) -> SymbolTrades:
        """Trades in one message; [] for heartbeats, acks and other noise."""

    def parse_capture(
        self, payload: bytes, *, symbol: str, captured_at: datetime
    ) -> list[TradeRow]:
        """Trades for `symbol` from a stored capture (JSON lines)."""
        rows: list[TradeRow] = []
        for line in payload.decode().splitlines():
            if line.strip():
                rows += [t for s, t in self.parse(json.loads(line)["message"]) if s == symbol]
        rows.sort(key=lambda t: (t["time_ns"], t["trade_id"]))
        return known_trades(rows, captured_at)
