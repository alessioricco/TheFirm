"""Coinbase Exchange WebSocket, public `matches` channel (no key).

`wss://ws-feed.exchange.coinbase.com`; send
`{"type":"subscribe","product_ids":["BTC-USD"],"channels":["matches"]}`.
Trades: `{"type":"match"|"last_match","trade_id":n,"side":"buy"|"sell",
"size":"q","price":"p","product_id":"BTC-USD","time":"...Z",...}`; `side` is
the MAKER's, so the taker's is the opposite. Noise: `subscriptions`,
`heartbeat`. Errors: `{"type":"error","message":...}`.
"""

from __future__ import annotations

import json

from firm.data.providers.base import TradeRow
from firm.data.streams.base import StreamError, StreamProvider, SymbolTrades, iso_ns
from firm.data.streams.registry import register


class CoinbaseWS(StreamProvider):
    name = "coinbase_ws"
    description = "Coinbase Exchange WebSocket public matches"
    licence = "coinbase public market data: personal use; check terms before redistribution"

    def url(self, symbols: list[str]) -> str:
        return "wss://ws-feed.exchange.coinbase.com"

    def subscribe_messages(self, symbols: list[str]) -> list[str]:
        return [json.dumps({"type": "subscribe", "product_ids": symbols, "channels": ["matches"]})]

    def parse(self, message: str) -> SymbolTrades:
        doc = json.loads(message)
        kind = doc.get("type")
        if kind == "error":
            raise StreamError(f"coinbase_ws: {doc.get('message')} {doc.get('reason', '')}")
        if kind not in ("match", "last_match"):
            return []
        return [
            (
                doc["product_id"],
                TradeRow(
                    time_ns=iso_ns(doc["time"]),
                    trade_id=str(doc["trade_id"]),
                    price=float(doc["price"]),
                    size=float(doc["size"]),
                    taker_side={"buy": "sell", "sell": "buy"}[doc["side"]],  # side is the maker's
                ),
            )
        ]


PROVIDER = register(CoinbaseWS())
