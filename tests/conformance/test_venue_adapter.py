"""The VenueAdapter conformance suite (SPEC "Integration layer").

Every adapter must pass all of it before it can be enabled. Tests use only
the adapter interface (no simulator internals), so real venues' adapters
run the same tests against their sandboxes, with synthetic fixtures here.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from factories import order_intent
from firm.core.domain import ORDER_TRANSITIONS, OrderStatus, OrderType, Side
from firm.execution.adapter import LiveTradingRefused, VenueAdapter, VenueMode


def run(coro):  # small helper; no pytest-asyncio dependency
    return asyncio.run(coro)


def intent(**kw):
    kw.setdefault("venue", "simulator")
    return order_intent(**kw)


def test_refuses_live_mode_while_live_trading_false(adapter_class: type[VenueAdapter]) -> None:
    with pytest.raises(LiveTradingRefused):
        adapter_class(VenueMode.LIVE, live_trading=False)


def test_declares_capabilities(adapter: VenueAdapter) -> None:
    caps = adapter.capabilities()
    assert caps.order_types and caps.time_in_force
    assert adapter.mode in caps.modes and caps.rate_limit.max_requests > 0


def test_mode_is_never_live_in_v1(adapter: VenueAdapter) -> None:
    assert adapter.mode is not VenueMode.LIVE


def test_submit_is_idempotent_on_client_order_id(adapter: VenueAdapter) -> None:
    order = intent(order_type=OrderType.LIMIT, limit_price=Decimal(40_000))
    first, second = run(adapter.submit(order)), run(adapter.submit(order))
    assert first == second
    assert [o.client_order_id for o in run(adapter.open_orders())].count("c-1") == 1


def test_reusing_a_client_order_id_for_another_order_is_refused(adapter: VenueAdapter) -> None:
    run(adapter.submit(intent(order_type=OrderType.LIMIT, limit_price=Decimal(40_000))))
    with pytest.raises(ValueError):
        run(adapter.submit(intent(order_type=OrderType.LIMIT, limit_price=Decimal(39_000))))


def test_cancel_open_limit_order_and_lifecycle_is_legal(adapter: VenueAdapter) -> None:
    order = intent(order_type=OrderType.LIMIT, limit_price=Decimal(40_000))
    accepted = run(adapter.submit(order))
    cancelled = run(adapter.cancel(order.client_order_id))
    assert accepted.status is OrderStatus.ACCEPTED and cancelled.status is OrderStatus.CANCELLED
    assert cancelled.status in ORDER_TRANSITIONS[accepted.status]
    assert run(adapter.cancel(order.client_order_id)).status is OrderStatus.CANCELLED


@pytest.mark.parametrize(
    "kw",
    [
        dict(quantity=Decimal("0.00001")),  # below the minimum quantity
        dict(quantity=Decimal("0.00015")),  # not a lot multiple
        dict(order_type=OrderType.LIMIT, limit_price=Decimal("40000.05")),  # off tick
        dict(instrument="NOT-LISTED"),
    ],
)
def test_rejects_orders_breaking_instrument_rules(adapter: VenueAdapter, kw) -> None:
    assert run(adapter.submit(intent(**kw))).status is OrderStatus.REJECTED


def test_rejects_unsupported_order_type(adapter: VenueAdapter) -> None:
    missing = set(OrderType) - adapter.capabilities().order_types
    if not missing:
        pytest.skip("adapter supports every order type")
    kind = missing.pop()
    kw = {"limit_price": Decimal(40_000)} if "limit" in kind else {}
    kw |= {"stop_price": Decimal(30_000)} if "stop" in kind else {}
    assert run(adapter.submit(intent(order_type=kind, **kw))).status is OrderStatus.REJECTED


def test_venue_side_stop_rests_at_venue(adapter: VenueAdapter) -> None:
    order = intent(side=Side.SELL, order_type=OrderType.STOP, stop_price=Decimal(30_000))
    assert run(adapter.submit(order)).status is OrderStatus.ACCEPTED
    assert any(o.client_order_id == order.client_order_id for o in run(adapter.open_orders()))


def test_reconcile_returns_positions_balances_open_orders(adapter: VenueAdapter) -> None:
    run(adapter.submit(intent(order_type=OrderType.LIMIT, limit_price=Decimal(40_000))))
    snap = run(adapter.reconcile())
    assert snap.venue == adapter.capabilities().venue
    assert [o.client_order_id for o in snap.open_orders] == ["c-1"]
    assert snap.balances == run(adapter.balances())
    assert snap.positions == run(adapter.positions())


def test_unknown_orders_are_a_lookup_error(adapter: VenueAdapter) -> None:
    with pytest.raises(LookupError):
        run(adapter.order_status("never-submitted"))
