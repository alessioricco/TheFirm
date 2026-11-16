import pytest

from firm.execution.adapter import LiveTradingRefused, VenueMode, ensure_mode_allowed
from firm.execution.simulator import PaperSimulator


def test_live_mode_refused_while_live_trading_false() -> None:
    with pytest.raises(LiveTradingRefused):
        ensure_mode_allowed(VenueMode.LIVE, live_trading=False)


@pytest.mark.parametrize("mode", [VenueMode.PAPER, VenueMode.SANDBOX])
def test_paper_and_sandbox_allowed(mode: VenueMode) -> None:
    ensure_mode_allowed(mode, live_trading=False)


def test_adapter_construction_refuses_live() -> None:
    with pytest.raises(LiveTradingRefused):
        PaperSimulator(VenueMode.LIVE, live_trading=False)


def test_simulator_supports_only_paper() -> None:
    assert PaperSimulator(VenueMode.PAPER, live_trading=False).mode is VenueMode.PAPER
    with pytest.raises(ValueError, match="does not support"):
        PaperSimulator(VenueMode.SANDBOX, live_trading=False)
