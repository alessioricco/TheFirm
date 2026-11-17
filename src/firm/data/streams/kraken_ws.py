"""Kraken WebSocket v2, public trades (no key).

`wss://ws.kraken.com/v2`; send
`{"method":"subscribe","params":{"channel":"trade","symbol":["BTC/USD"]}}`.
Trades: `{"channel":"trade","type":"snapshot"|"update","data":[{"symbol":"BTC/USD",
"side":"buy"|"sell","price":p,"qty":q,"ord_type":"market","trade_id":n,
"timestamp":"2026-11-16T12:00:00.123456Z"}]}`; `side` is the taker's.
Noise: heartbeats, status, subscription acks. Errors: an ack with
`"success": false`.
"""

from __future__ import annotations

import json

from firm.data.providers.base import TradeRow
from firm.data.streams.base import StreamError, StreamProvider, SymbolTrades, iso_ns
from firm.data.streams.registry import register


class KrakenWS(StreamProvider):
    name = "kraken_ws"
    description = "Kraken WebSocket v2 public trades"
    licence = "kraken public market data: personal use; check terms before redistribution"

    def url(self, symbols: list[str]) -> str:
        return "wss://ws.kraken.com/v2"

    def subscribe_messages(self, symbols: list[str]) -> list[str]:
        return [
            json.dumps({"method": "subscribe", "params": {"channel": "trade", "symbol": symbols}})
        ]

    def parse(self, message: str) -> SymbolTrades:
        doc = json.loads(message)
        if doc.get("success") is False:
            raise StreamError(f"kraken_ws: {doc.get('error')}")
        if doc.get("channel") != "trade":
            return []
        return [
            (
                t["symbol"],
                TradeRow(
                    time_ns=iso_ns(t["timestamp"]),
                    trade_id=str(t["trade_id"]),
                    price=float(t["price"]),
                    size=float(t["qty"]),
                    taker_side=t["side"],
                ),
            )
            for t in doc.get("data", [])
        ]


PROVIDER = register(KrakenWS())
