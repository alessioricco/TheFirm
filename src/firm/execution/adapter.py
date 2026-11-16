"""The one `VenueAdapter` interface every venue goes through (golden rule 4).

Designed for real trading from day one (SPEC "Integration layer"):
capabilities declaration, idempotent submit keyed by client order ID, the
full order lifecycle, cancel, reconciliation, venue-side stops, rate limits,
instrument rules, and a `paper | sandbox | live` mode. `live` is refused
while `live_trading` is false, at construction time, by this base class.

Adapters only accept `OrderIntent`, which cannot exist without a verdict ID
and a risk decision ID (firm rule 2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field

from firm.core.base import CurrencyCode, FirmModel, Id
from firm.core.domain import OrderIntent, OrderType, OrderUpdate, Side, TimeInForce

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class VenueMode(StrEnum):
    PAPER = "paper"
    SANDBOX = "sandbox"
    LIVE = "live"


class LiveTradingRefused(RuntimeError):
    """Raised when an adapter is asked for `live` mode while live trading is off."""


def ensure_mode_allowed(mode: VenueMode, *, live_trading: bool) -> None:
    """The single gate between configuration and a live venue."""
    if mode is VenueMode.LIVE and not live_trading:
        raise LiveTradingRefused("live mode refused: live_trading is false")


class RateLimitPolicy(FirmModel):
    """What the venue tolerates; the adapter must back off before hitting it."""

    max_requests: int = Field(gt=0)
    per_seconds: float = Field(gt=0)
    max_orders_per_second: float | None = Field(default=None, gt=0)


class VenueCapabilities(FirmModel):
    """What this venue really supports. Never fake support the venue lacks."""

    venue: Id
    modes: frozenset[VenueMode]
    order_types: frozenset[OrderType]
    time_in_force: frozenset[TimeInForce]
    venue_side_stops: bool
    partial_fills: bool
    rate_limit: RateLimitPolicy


class InstrumentRules(FirmModel):
    """Orders are rounded and checked against these before they leave."""

    instrument: Id
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    tick_size: PositiveDecimal
    lot_size: PositiveDecimal
    min_quantity: PositiveDecimal
    min_notional: NonNegativeDecimal
    tradable: bool = True  # False for delisted / halted


class Position(FirmModel):
    instrument: Id
    side: Side
    quantity: NonNegativeDecimal
    average_price: PositiveDecimal | None = None


class Balance(FirmModel):
    currency: CurrencyCode
    total: NonNegativeDecimal
    available: NonNegativeDecimal


class ReconciliationSnapshot(FirmModel):
    """The venue's truth at one moment. Mismatches with our books halt trading."""

    venue: Id
    taken_at: AwareDatetime
    positions: tuple[Position, ...]
    balances: tuple[Balance, ...]
    open_orders: tuple[OrderUpdate, ...]


class VenueAdapter(ABC):
    """Base class for every venue: the paper simulator now, real venues later.

    Contract (checked by `tests/conformance/`):

    - `submit` is idempotent on `client_order_id`: submitting the same intent
      twice returns the same order, never a second one.
    - Every state an order passes through is reported as an `OrderUpdate`,
      and only transitions allowed by `firm.core.domain.ORDER_TRANSITIONS`
      happen.
    - Orders breaking `instrument_rules` are rejected before they leave.
    - Order types / time in force not in `capabilities()` are rejected.
    - `reconcile()` returns the venue's view; the caller compares it.
    """

    def __init__(self, mode: VenueMode, *, live_trading: bool) -> None:
        ensure_mode_allowed(mode, live_trading=live_trading)
        if mode not in self.capabilities().modes:
            raise ValueError(f"{type(self).__name__} does not support mode {mode}")
        self._mode = mode

    @property
    def mode(self) -> VenueMode:
        return self._mode

    @abstractmethod
    def capabilities(self) -> VenueCapabilities: ...

    @abstractmethod
    async def instrument_rules(self, instrument: str) -> InstrumentRules: ...

    @abstractmethod
    async def submit(self, intent: OrderIntent) -> OrderUpdate:
        """Idempotent on `intent.client_order_id`."""

    @abstractmethod
    async def cancel(self, client_order_id: str) -> OrderUpdate: ...

    @abstractmethod
    async def order_status(self, client_order_id: str) -> OrderUpdate: ...

    @abstractmethod
    async def open_orders(self) -> tuple[OrderUpdate, ...]: ...

    @abstractmethod
    async def positions(self) -> tuple[Position, ...]: ...

    @abstractmethod
    async def balances(self) -> tuple[Balance, ...]: ...

    @abstractmethod
    async def reconcile(self) -> ReconciliationSnapshot: ...
