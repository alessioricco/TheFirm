"""Live market data streams behind one contract (`base.StreamProvider`).

Built in: kraken_ws, coinbase_ws, binance_ws. Names match market profiles'
`data.realtime`. `feed.Feed` runs a stream; `bars.BarBuilder` makes closed
bars from its trades.
"""

from firm.data.streams import binance_ws, coinbase_ws, kraken_ws  # noqa: F401  (register)
from firm.data.streams.base import StreamError, StreamProvider
from firm.data.streams.registry import available, get, register

__all__ = ["StreamError", "StreamProvider", "available", "get", "register"]
