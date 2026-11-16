"""Shared conformance suite: every `VenueAdapter` must pass it before it can be enabled.

Add an adapter by appending a factory to `ADAPTERS`. Factories build the
adapter in a non-live mode with recorded fixtures; tests never touch a network.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from firm.execution.adapter import VenueAdapter, VenueMode
from firm.execution.simulator import PaperSimulator

ADAPTERS: dict[str, Callable[[], VenueAdapter]] = {
    "paper_simulator": lambda: PaperSimulator(VenueMode.PAPER, live_trading=False),
}

ADAPTER_CLASSES: dict[str, type[VenueAdapter]] = {"paper_simulator": PaperSimulator}


@pytest.fixture(params=sorted(ADAPTERS))
def adapter(request: pytest.FixtureRequest) -> VenueAdapter:
    return ADAPTERS[request.param]()


@pytest.fixture(params=sorted(ADAPTER_CLASSES))
def adapter_class(request: pytest.FixtureRequest) -> type[VenueAdapter]:
    return ADAPTER_CLASSES[request.param]
