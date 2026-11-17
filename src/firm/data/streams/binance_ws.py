"""Binance public aggregate-trade streams (no key), market-data host.

`wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade`
(combined stream; the URL subscribes). Messages:
`{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":ms,"s":"BTCUSDT","a":id,
"p":"price","q":"qty","f":first,"l":last,"T":ms,"m":isBuyerMaker,"M":bool}}`;
`m` true means the buyer was the maker, so the taker SOLD. A data source for
the lab only (not permitted for real money in the UK profile).
"""

from __future__ import annotations

import json

from firm.data.providers.base import TradeRow
from firm.data.streams.base import StreamError, StreamProvider, SymbolTrades
from firm.data.streams.registry import register


class BinanceWS(StreamProvider):
    name = "binance_ws"
    description = "Binance public aggTrade streams (data-stream.binance.vision)"
    licence = "binance public market data: personal use; check terms before redistribution"

    def url(self, symbols: list[str]) -> str:
        streams = "/".join(f"{s.lower()}@aggTrade" for s in symbols)
        return f"wss://data-stream.binance.vision/stream?streams={streams}"

    def parse(self, message: str) -> SymbolTrades:
        doc = json.loads(message)
        if "code" in doc and "msg" in doc:
            raise StreamError(f"binance_ws {doc['code']}: {doc['msg']}")
        data = doc.get("data", doc)
        if data.get("e") != "aggTrade":
            return []
        return [
            (
                data["s"],
                TradeRow(
                    time_ns=int(data["T"]) * 1_000_000,
                    trade_id=str(data["a"]),
                    price=float(data["p"]),
                    size=float(data["q"]),
                    taker_side="sell" if data["m"] else "buy",
                ),
            )
        ]


PROVIDER = register(BinanceWS())
