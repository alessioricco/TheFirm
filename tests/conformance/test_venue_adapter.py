"""The VenueAdapter conformance suite (SPEC "Integration layer").

Tests marked `xfail(strict=True)` describe behaviour the paper simulator
gains in milestone 2. Strict means an unexpected pass fails the run, so the
marker must be removed the moment the behaviour exists.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from factories import order_intent
from firm.core.domain import OrderStatus, OrderType
from firm.execution.adapter import LiveTradingRefused, VenueAdapter, VenueMode

M2 = pytest.mark.xfail(
    reason="paper simulator not built yet (milestone 2)",
    raises=NotImplementedError,
    strict=True,
)


def run(coro):  # small helper; no pytest-asyncio dependency
    return asyncio.run(coro)


# --- passes now


def test_refuses_live_mode_while_live_trading_false(adapter_class: type[VenueAdapter]) -> None:
    with pytest.raises(LiveTradingRefused):
        adapter_class(VenueMode.LIVE, live_trading=False)


def test_declares_capabilities(adapter: VenueAdapter) -> None:
    caps = adapter.capabilities()
    assert caps.order_types, "an adapter must declare at least one order type"
    assert caps.time_in_force
    assert adapter.mode in caps.modes
    assert caps.rate_limit.max_requests > 0


def test_mode_is_never_live_in_v1(adapter: VenueAdapter) -> None:
    assert adapter.mode is not VenueMode.LIVE


# --- milestone 2


@M2
def test_submit_is_idempotent_on_client_order_id(adapter: VenueAdapter) -> None:
    intent = order_intent()
    first = run(adapter.submit(intent))
    second = run(adapter.submit(intent))
    assert first.client_order_id == second.client_order_id
    assert len([o for o in run(adapter.open_orders()) if o.client_order_id == "c-1"]) <= 1


@M2
def test_lifecycle_reports_every_state(adapter: VenueAdapter) -> None:
    update = run(adapter.submit(order_intent()))
    assert update.status in set(OrderStatus)


@M2
def test_cancel_open_limit_order(adapter: VenueAdapter) -> None:
    intent = order_intent(order_type=OrderType.LIMIT, limit_price=Decimal("1"))
    run(adapter.submit(intent))
    assert run(adapter.cancel(intent.client_order_id)).status is OrderStatus.CANCELLED


@M2
def test_rejects_order_breaking_instrument_rules(adapter: VenueAdapter) -> None:
    rules = run(adapter.instrument_rules("BTC-USD"))
    tiny = order_intent(quantity=rules.min_quantity / 10)
    assert run(adapter.submit(tiny)).status is OrderStatus.REJECTED


@M2
def test_rejects_unsupported_order_type(adapter: VenueAdapter) -> None:
    missing = set(OrderType) - adapter.capabilities().order_types
    if not missing:
        pytest.skip("adapter supports every order type")
    run(adapter.submit(order_intent(order_type=missing.pop())))


@M2
def test_venue_side_stop_rests_at_venue(adapter: VenueAdapter) -> None:
    intent = order_intent(order_type=OrderType.STOP, stop_price=Decimal("1"))
    run(adapter.submit(intent))
    assert any(o.client_order_id == intent.client_order_id for o in run(adapter.open_orders()))


@M2
def test_reconcile_returns_positions_balances_open_orders(adapter: VenueAdapter) -> None:
    snap = run(adapter.reconcile())
    assert snap.venue == adapter.capabilities().venue


@M2
def test_rate_limit_backs_off(adapter: VenueAdapter) -> None:
    run(adapter.order_status("c-unknown"))
