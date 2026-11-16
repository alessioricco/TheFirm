"""The paper simulator: the first `VenueAdapter`.

Milestone 2 fills orders against live data with fees, spread, slippage and
partial fills, and never at a price that wasn't available. Until then every
trading method raises `NotImplementedError`; the conformance suite marks
those tests `xfail`.
"""

from __future__ import annotations

from firm.core.domain import OrderIntent, OrderType, OrderUpdate, TimeInForce
from firm.execution.adapter import (
    Balance,
    InstrumentRules,
    Position,
    RateLimitPolicy,
    ReconciliationSnapshot,
    VenueAdapter,
    VenueCapabilities,
    VenueMode,
)

_NOT_YET = "paper simulator: milestone 2"


class PaperSimulator(VenueAdapter):
    """Paper mode only. It has no sandbox or live counterpart."""

    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            venue="simulator",
            modes=frozenset({VenueMode.PAPER}),
            order_types=frozenset(OrderType),
            time_in_force=frozenset(TimeInForce),
            venue_side_stops=True,
            partial_fills=True,
            rate_limit=RateLimitPolicy(max_requests=1_000, per_seconds=1.0),
        )

    async def instrument_rules(self, instrument: str) -> InstrumentRules:
        raise NotImplementedError(_NOT_YET)

    async def submit(self, intent: OrderIntent) -> OrderUpdate:
        raise NotImplementedError(_NOT_YET)

    async def cancel(self, client_order_id: str) -> OrderUpdate:
        raise NotImplementedError(_NOT_YET)

    async def order_status(self, client_order_id: str) -> OrderUpdate:
        raise NotImplementedError(_NOT_YET)

    async def open_orders(self) -> tuple[OrderUpdate, ...]:
        raise NotImplementedError(_NOT_YET)

    async def positions(self) -> tuple[Position, ...]:
        raise NotImplementedError(_NOT_YET)

    async def balances(self) -> tuple[Balance, ...]:
        raise NotImplementedError(_NOT_YET)

    async def reconcile(self) -> ReconciliationSnapshot:
        raise NotImplementedError(_NOT_YET)
