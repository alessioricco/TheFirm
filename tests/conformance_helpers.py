"""A minimal paper-simulator config for tests that only need a valid adapter."""

from decimal import Decimal

from firm.execution.adapter import InstrumentRules
from firm.execution.simulator import SimConfig


def paper_config() -> SimConfig:
    rules = InstrumentRules(
        instrument="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )
    return SimConfig(
        venue="simulator",
        rules={"BTC-USD": rules},
        maker_bps=Decimal(40),
        taker_bps=Decimal(80),
        spread_bps=Decimal(2),
        slippage_bps=Decimal(5),
        participation=Decimal("0.1"),
        balances={"USD": Decimal(100_000)},
    )
