"""Live streams: each provider's exact message format, closed bars from trades,
and the feed runner (reconnect, de-duplication, captures, staleness).

Synthetic messages only, in each venue's documented format; no network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from firm.core.domain import LedgerEventType
from firm.core.events import CollectingSink
from firm.data import streams
from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_trades
from firm.data.providers.base import NS, TradeRow
from firm.data.reference import InstrumentRecord, write_instrument
from firm.data.store import iter_captures
from firm.data.streams.bars import BarBuilder
from firm.data.streams.base import StreamError
from firm.data.streams.feed import Feed

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def kraken_msg(*trades: tuple[int, float, float, str], symbol: str = "BTC/USD") -> str:
    return json.dumps(
        {
            "channel": "trade",
            "type": "update",
            "data": [
                {
                    "symbol": symbol,
                    "side": side,
                    "price": p,
                    "qty": q,
                    "ord_type": "market",
                    "trade_id": tid,
                    "timestamp": iso(T0 + timedelta(seconds=tid)),
                }
                for tid, p, q, side in trades
            ],
        }
    )


# --- provider formats


def test_registry_lists_the_three_streams() -> None:
    assert streams.available() == ["binance_ws", "coinbase_ws", "kraken_ws"]
    with pytest.raises(ValueError, match="unknown stream provider"):
        streams.get("nope")


def test_kraken_trades_noise_and_errors() -> None:
    k = streams.get("kraken_ws")
    assert json.loads(k.subscribe_messages(["BTC/USD"])[0])["params"] == {
        "channel": "trade",
        "symbol": ["BTC/USD"],
    }
    ((sym, t),) = k.parse(kraken_msg((7, 50_000.5, 0.25, "sell")))
    assert sym == "BTC/USD" and t == TradeRow(
        time_ns=int((T0 + timedelta(seconds=7)).timestamp()) * NS,
        trade_id="7",
        price=50_000.5,
        size=0.25,
        taker_side="sell",
    )
    assert k.parse('{"channel":"heartbeat"}') == []
    assert k.parse('{"method":"subscribe","success":true,"result":{}}') == []
    with pytest.raises(StreamError, match="Currency pair not supported"):
        k.parse('{"method":"subscribe","success":false,"error":"Currency pair not supported"}')


def test_coinbase_side_is_the_makers_so_the_taker_is_opposite() -> None:
    c = streams.get("coinbase_ws")
    msg = {
        "type": "match",
        "trade_id": 11,
        "maker_order_id": "a",
        "taker_order_id": "b",
        "side": "buy",
        "size": "0.5",
        "price": "50000.00",
        "product_id": "BTC-USD",
        "sequence": 1,
        "time": iso(T0),
    }
    ((sym, t),) = c.parse(json.dumps(msg))
    assert sym == "BTC-USD" and t["taker_side"] == "sell" and t["price"] == 50_000.0
    assert c.parse('{"type":"subscriptions","channels":[]}') == []
    with pytest.raises(StreamError):
        c.parse('{"type":"error","message":"Failed to subscribe","reason":"bad product"}')


def test_binance_buyer_maker_means_the_taker_sold() -> None:
    b = streams.get("binance_ws")
    assert b.url(["BTCUSDT", "ETHUSDT"]).endswith("streams=btcusdt@aggTrade/ethusdt@aggTrade")
    data = {
        "e": "aggTrade",
        "E": 1,
        "s": "BTCUSDT",
        "a": 5,
        "p": "50000.1",
        "q": "0.01",
        "f": 1,
        "l": 2,
        "T": int(T0.timestamp() * 1000),
        "m": True,
        "M": True,
    }
    ((sym, t),) = b.parse(json.dumps({"stream": "btcusdt@aggTrade", "data": data}))
    assert (
        sym == "BTCUSDT" and t["taker_side"] == "sell" and t["time_ns"] == int(T0.timestamp()) * NS
    )
    with pytest.raises(StreamError):
        b.parse('{"code": 2, "msg": "Invalid request"}')


# --- bars from trades


def tr(sec: float, price: float, size: float = 1.0, tid: str = "x") -> TradeRow:
    return TradeRow(
        time_ns=int((T0.timestamp() + sec) * NS),
        trade_id=tid,
        price=price,
        size=size,
        taker_side="buy",
    )


def test_bars_close_only_after_their_period() -> None:
    b = BarBuilder("1m", started_ns=int(T0.timestamp()) * NS)
    assert b.add(tr(1, 100)) == [] and b.add(tr(30, 105)) == [] and b.add(tr(59, 98, 2)) == []
    assert b.close_due(int((T0.timestamp() + 59.9) * NS)) == []  # still forming
    (bar,) = b.add(tr(61, 101))  # first trade of the next minute closes it
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]) == (
        100,
        105,
        98,
        98,
        4,
    )
    assert bar["open_time_ns"] == int(T0.timestamp()) * NS
    assert bar["close_time_ns"] == bar["open_time_ns"] + 60 * NS


def test_quiet_markets_close_by_time_gaps_stay_gaps_and_late_trades_are_dropped() -> None:
    b = BarBuilder("1m", started_ns=int(T0.timestamp()) * NS)
    b.add(tr(5, 100))
    (bar,) = b.close_due(int((T0.timestamp() + 60) * NS))
    assert bar["close"] == 100
    assert b.add(tr(30, 999)) == [] and b.late_trades == 1  # never rewrites a known bar
    (nxt,) = b.add(tr(200, 101)) or b.close_due(int((T0.timestamp() + 240) * NS))
    assert nxt["open_time_ns"] == int(T0.timestamp() + 180) * NS  # minute 2 had no trades


def test_bars_not_watched_from_their_start_are_dropped_not_passed_off_as_complete() -> None:
    b = BarBuilder("1m", started_ns=int((T0.timestamp() + 20) * NS))  # joined 20 s into a minute
    b.add(tr(-30, 90))  # snapshot trade from before we connected
    b.add(tr(25, 100))
    assert b.add(tr(61, 101)) == [] and b.incomplete == 2  # both partly seen minutes dropped
    (bar,) = b.add(tr(121, 102))
    assert bar["open"] == 101  # minute 1 was watched whole
    b.mark_gap(int((T0.timestamp() + 130) * NS))  # disconnected during minute 2
    b.add(tr(150, 103))
    assert b.add(tr(185, 104)) == [] and b.incomplete == 3
    assert b.add(tr(245, 105))[0]["open"] == 104


# --- the feed runner


class FakeSocket:
    def __init__(self, messages: list[str], end: Exception | None) -> None:
        self.messages, self.end, self.sent = messages, end, []

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        if self.messages:
            return self.messages.pop(0)
        if self.end is not None:
            raise self.end
        raise StopAsyncIteration


class Clock:
    def __init__(self) -> None:
        self.now = T0 + timedelta(minutes=5)

    def __call__(self) -> datetime:
        return self.now


def record(root: Path) -> None:
    write_instrument(
        root,
        InstrumentRecord(
            venue="kraken",
            instrument="BTC-USD",
            asset_class="crypto",
            base="BTC",
            quote="USD",
            provider_symbols={"kraken": "XBTUSD", "kraken_ws": "BTC/USD"},
            listed_at=datetime(2013, 1, 1, tzinfo=UTC),
            known_at=datetime(2013, 1, 1, tzinfo=UTC),
        ),
    )


def test_feed_reconnects_dedupes_captures_and_reads_back(tmp_path: Path) -> None:
    record(tmp_path)
    clock, sleeps, sink, seen = Clock(), [], CollectingSink(), []
    sockets = [
        FakeSocket(
            [kraken_msg((1, 100.0, 1.0, "buy"), (2, 101.0, 1.0, "sell"))], ConnectionError("reset")
        ),
        FakeSocket([kraken_msg((2, 101.0, 1.0, "sell"), (3, 102.0, 2.0, "buy"))], None),
    ]
    stop = asyncio.Event()
    gaps: list[datetime] = []

    def connect(url: str) -> FakeSocket:
        if len(sockets) == 1:  # last socket: the run stops after its message
            stop.set()
        return sockets.pop(0)

    async def sleep(s: float) -> None:
        sleeps.append(s)

    feed = Feed(
        "kraken_ws",
        ["BTC/USD"],
        root=tmp_path,
        sink=sink,
        connect=connect,
        clock=clock,
        sleep=sleep,
        on_trade=lambda s, t: seen.append(t["trade_id"]),
        on_gap=gaps.append,
    )
    asyncio.run(feed.run(stop))

    assert feed.connections == 2 and sleeps[0] == 1.0  # backoff after the drop
    assert gaps == [clock.now]  # bar builders are told trades may be missing
    assert seen == ["1", "2", "3"]  # trade 2 replayed after reconnect: delivered once
    statuses = [e[2]["status"] for e in sink.events if e[0] is LedgerEventType.FEED_STATUS]
    assert statuses[:3] == ["connected", "disconnected", "connected"]
    assert len(list(iter_captures(tmp_path, provider="kraken_ws", kind="trades"))) == 1

    files = normalize_trades(tmp_path, provider="kraken_ws", provider_symbol="BTC/USD")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    df = ds.trades("kraken", "BTC-USD", as_of=clock.now)
    assert df["trade_id"].tolist() == ["1", "2", "3"] and df["size"].tolist() == [1, 1, 2]


def test_capture_never_holds_trades_from_after_its_capture_time() -> None:
    k = streams.get("kraken_ws")
    line = json.dumps(
        {"received_at": iso(T0), "message": kraken_msg((1, 1.0, 1.0, "buy"), (90, 2.0, 1.0, "buy"))}
    )
    rows = k.parse_capture(line.encode(), symbol="BTC/USD", captured_at=T0 + timedelta(seconds=30))
    assert [r["trade_id"] for r in rows] == ["1"]


def test_staleness_is_reported_on_change_only() -> None:
    clock, sink = Clock(), CollectingSink()
    feed = Feed("kraken_ws", ["BTC/USD"], sink=sink, clock=clock, stale_after=timedelta(seconds=60))
    assert feed.check() == {"BTC/USD": False}
    clock.now += timedelta(seconds=61)
    assert feed.check() == {"BTC/USD": True} and feed.check() == {"BTC/USD": True}
    feed.handle(kraken_msg((360, 1.0, 1.0, "buy")))  # trade at T0+6min = clock.now-1s
    assert feed.check() == {"BTC/USD": False}
    statuses = [e[2]["status"] for e in sink.events]
    assert statuses == ["stale", "fresh"]


def test_a_refused_subscription_stops_the_feed_instead_of_retrying() -> None:
    bad = '{"method":"subscribe","success":false,"error":"Currency pair not supported"}'
    feed = Feed("kraken_ws", ["NOPE/USD"], connect=lambda u: FakeSocket([bad], None))
    with pytest.raises(StreamError):
        asyncio.run(feed.run(asyncio.Event()))


def test_garbage_is_logged_not_fatal() -> None:
    sink = CollectingSink()
    feed = Feed("coinbase_ws", ["BTC-USD"], sink=sink)
    assert feed.handle('{"type":"match","product_id":"BTC-USD"}') == []
    assert sink.events and "unparseable" in sink.events[0][1]
