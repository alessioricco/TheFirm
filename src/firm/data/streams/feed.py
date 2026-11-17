"""Run one live stream: connect, subscribe, parse, reconnect, capture, watch staleness.

- Reconnects with exponential backoff (1, 2, 4 ... capped) after any drop,
  and resubscribes. Trades seen before a reconnect (snapshots, replays) are
  de-duplicated by trade ID.
- Every message that carried trades is kept and written as a capture per
  symbol every `flush_interval` (and on stop): JSON lines of
  `{received_at, message}`, exactly as received, so live data can be
  normalised like downloads and backtested point-in-time later.
- A symbol is stale when no trade arrived within `stale_after`; changes
  (stale / fresh / disconnected / connected) are emitted as FEED_STATUS
  events. The market worker blocks new paper orders on a stale feed.

`connect`, `clock` and `sleep` are injectable: tests use a fake socket.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from firm.core.domain import LedgerEventType
from firm.core.events import NULL_SINK, EventSink
from firm.data.providers.base import TradeRow
from firm.data.store import write_capture
from firm.data.streams import registry
from firm.data.streams.base import StreamError

TradeCallback = Callable[[str, TradeRow], None]


def _now() -> datetime:
    return datetime.now(UTC)


def _default_connect(url: str) -> Any:
    import websockets

    return websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**22)


class Feed:
    def __init__(
        self,
        provider: str,
        symbols: list[str],
        *,
        root: Path | None = None,
        sink: EventSink = NULL_SINK,
        on_trade: TradeCallback | None = None,
        on_gap: Callable[[datetime], None] | None = None,
        connect: Callable[[str], Any] = _default_connect,
        clock: Callable[[], datetime] = _now,
        sleep: Callable[[float], Any] = asyncio.sleep,
        stale_after: timedelta = timedelta(seconds=60),
        flush_interval: timedelta = timedelta(seconds=60),
        max_backoff: float = 60.0,
        dedupe_window: int = 50_000,
    ) -> None:
        self.provider = registry.get(provider)
        self.symbols = list(symbols)
        self.root = root
        self.sink = sink
        self.on_trade = on_trade
        self.on_gap = on_gap  # told when trades may have been missed (bars must drop partials)
        self._connect = connect
        self._clock = clock
        self._sleep = sleep
        self.stale_after = stale_after
        self.flush_interval = flush_interval
        self.max_backoff = max_backoff
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._window = dedupe_window
        self._buffer: dict[str, list[str]] = {s: [] for s in self.symbols}
        self._last_flush = clock()
        self._last_trade: dict[str, datetime] = {}
        self._status: dict[str, str] = {}
        self.started = clock()
        self.connections = 0
        self.trades = 0

    # ------------------------------------------------------------------ status

    def _set_status(self, symbol: str, status: str, detail: str, at: datetime) -> None:
        if self._status.get(symbol) == status:
            return
        self._status[symbol] = status
        payload = {
            "provider": self.provider.name,
            "symbol": symbol,
            "status": status,
            "detail": detail,
            "at": at.isoformat(),
        }
        self.sink.emit(
            LedgerEventType.FEED_STATUS, f"{self.provider.name} {symbol} {status}", payload
        )

    def check(self, now: datetime | None = None) -> dict[str, bool]:
        """Staleness per symbol (True = stale); emits status changes."""
        now = now or self._clock()
        out = {}
        for s in self.symbols:
            last = self._last_trade.get(s, self.started)
            stale = now - last > self.stale_after
            out[s] = stale
            if stale:
                self._set_status(s, "stale", f"no trade since {last.isoformat()}", now)
            elif s in self._last_trade:
                self._set_status(s, "fresh", "trades arriving", now)
        return out

    def last_trade_at(self, symbol: str) -> datetime | None:
        return self._last_trade.get(symbol)

    # ------------------------------------------------------------------ messages

    def handle(self, message: str) -> list[tuple[str, TradeRow]]:
        """Process one raw message (also used by tests and replays)."""
        now = self._clock()
        try:
            trades = self.provider.parse(message)
        except StreamError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            self.sink.emit(
                LedgerEventType.FEED_STATUS,
                f"{self.provider.name} unparseable message",
                {
                    "provider": self.provider.name,
                    "error": repr(exc)[:300],
                    "message": message[:300],
                    "at": now.isoformat(),
                },
            )
            return []
        fresh = []
        symbols_in_message = set()
        for symbol, trade in trades:
            if symbol not in self._buffer:
                continue
            symbols_in_message.add(symbol)
            key = (symbol, trade["trade_id"])
            if key in self._seen:
                continue
            self._seen[key] = None
            if len(self._seen) > self._window:
                self._seen.popitem(last=False)
            fresh.append((symbol, trade))
            t = pd.Timestamp(trade["time_ns"], unit="ns", tz="UTC").to_pydatetime()
            self._last_trade[symbol] = max(self._last_trade.get(symbol, t), t)
            self.trades += 1
            if self.on_trade is not None:
                self.on_trade(symbol, trade)
        line = json.dumps({"received_at": now.isoformat(), "message": message})
        for symbol in symbols_in_message:
            self._buffer[symbol].append(line)
        if now - self._last_flush >= self.flush_interval:
            self.flush()
        return fresh

    def flush(self) -> int:
        """Write buffered messages as one capture per symbol; returns captures written."""
        now = self._clock()
        self._last_flush = now
        if self.root is None:
            for s in self._buffer:
                self._buffer[s] = []
            return 0
        written = 0
        for symbol, lines in self._buffer.items():
            if not lines:
                continue
            write_capture(
                self.root,
                ("\n".join(lines) + "\n").encode(),
                provider=self.provider.name,
                kind="trades",
                provider_symbol=symbol,
                tf=None,
                fmt="jsonl",
                captured_at=now,
                request={"url": self.provider.url(self.symbols), "symbols": self.symbols},
                via="websockets",
                licence=self.provider.licence,
            )
            self._buffer[symbol] = []
            written += 1
        return written

    # ------------------------------------------------------------------ connection

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        url = self.provider.url(self.symbols)
        try:
            while not stop.is_set():
                try:
                    async with self._connect(url) as ws:
                        self.connections += 1
                        for msg in self.provider.subscribe_messages(self.symbols):
                            await ws.send(msg)
                        for s in self.symbols:
                            self._set_status(s, "connected", "subscribed", self._clock())
                        attempt = 0
                        async for message in ws:
                            self.handle(message if isinstance(message, str) else message.decode())
                            if stop.is_set():
                                break
                except StreamError:
                    raise  # a refused subscription won't fix itself by retrying
                except (TimeoutError, OSError, ConnectionError) as exc:
                    self._disconnected(repr(exc))
                except Exception as exc:  # websockets' ConnectionClosed and friends
                    if type(exc).__module__.startswith("websockets"):
                        self._disconnected(repr(exc))
                    else:
                        raise
                else:
                    if not stop.is_set():
                        self._disconnected("stream ended")
                if stop.is_set():
                    break
                attempt += 1
                await self._sleep(min(self.max_backoff, 2.0 ** (attempt - 1)))
        finally:
            self.flush()

    def _disconnected(self, why: str) -> None:
        if self.on_gap is not None:
            self.on_gap(self._clock())
        for s in self.symbols:
            self._set_status(s, "disconnected", why[:200], self._clock())
