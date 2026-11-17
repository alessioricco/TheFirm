"""Shared conformance suite: every `VenueAdapter` must pass it before it can be enabled.

Add an adapter by appending a factory to `ADAPTERS`. Factories build the
adapter in a non-live mode with synthetic fixtures; tests never touch a network.
Each factory gets the shared test instrument (BTC-USD) and enough funds.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest

from factories import T0
from firm.execution.adapter import InstrumentRules, VenueAdapter, VenueMode
from firm.execution.simulator import MarketTrade, PaperSimulator, SimConfig

RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=Decimal("0.1"),
    lot_size=Decimal("0.0001"),
    min_quantity=Decimal("0.0001"),
    min_notional=Decimal("5"),
)


def _paper() -> VenueAdapter:
    sim = PaperSimulator(
        VenueMode.PAPER,
        live_trading=False,
        config=SimConfig(
            venue="simulator",
            rules={"BTC-USD": RULES},
            maker_bps=Decimal(40),
            taker_bps=Decimal(80),
            spread_bps=Decimal(2),
            slippage_bps=Decimal(5),
            participation=Decimal("0.1"),
            balances={"USD": Decimal(100_000), "BTC": Decimal(1)},
        ),
    )
    sim.on_market(
        MarketTrade(
            instrument="BTC-USD",
            time=T0 - timedelta(seconds=1),
            price=Decimal(50_000),
            size=Decimal(10),
        )
    )
    return sim


ADAPTERS: dict[str, Callable[[], VenueAdapter]] = {"paper_simulator": _paper}
ADAPTER_CLASSES: dict[str, type[VenueAdapter]] = {"paper_simulator": PaperSimulator}


@pytest.fixture(params=sorted(ADAPTERS))
def adapter(request: pytest.FixtureRequest) -> VenueAdapter:
    return ADAPTERS[request.param]()


@pytest.fixture(params=sorted(ADAPTER_CLASSES))
def adapter_class(request: pytest.FixtureRequest) -> type[VenueAdapter]:
    return ADAPTER_CLASSES[request.param]
