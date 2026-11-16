"""Market-data providers behind one contract (`base.Provider`) and a registry.

Built-in: kraken, coinbase, binance (crypto), yahoo (equities, unofficial).
To add one: a module here that registers a `Provider`, imported below.
"""

from firm.data.providers import binance, coinbase, kraken, yahoo  # noqa: F401  (register)
from firm.data.providers.base import (
    BarRow,
    Provider,
    ProviderError,
    Request,
    TradeRow,
)
from firm.data.providers.registry import available, get, register

__all__ = [
    "BarRow",
    "Provider",
    "ProviderError",
    "Request",
    "TradeRow",
    "available",
    "get",
    "register",
]
