"""What `firm paper run` prints while it waits: every bar's decision and a
periodic status line, so a quiet hour never looks like a hang."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from firm.control.progress import Progress, describe_outcome, next_close, status_line
from firm.core.domain import Side
from firm.execution.adapter import Balance, InstrumentRules, Position
from firm.execution.portfolio import MarketAccount
from firm.execution.worker import Outcome

D = Decimal
RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=D("0.1"),
    lot_size=D("0.0001"),
    min_quantity=D("0.0001"),
    min_notional=D("5"),
)


def account(cash: str, held: str, mark: str) -> MarketAccount:
    return MarketAccount(
        market="crypto",
        venue="coinbase",
        balances=(Balance(currency="USD", total=D(cash), available=D(cash)),),
        positions=(Position(instrument="BTC-USD", side=Side.BUY, quantity=D(held)),)
        if D(held)
        else (),
        marks={"BTC-USD": D(mark)},
        rules={"BTC-USD": RULES},
    )


def test_next_close_is_the_end_of_the_current_bar() -> None:
    now = datetime(2026, 11, 18, 13, 25, 7, tzinfo=UTC)
    assert next_close(now, "1h") == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
    assert next_close(now, "1d") == datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
    on_the_hour = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
    assert next_close(on_the_hour, "1h") == datetime(2026, 11, 18, 15, 0, tzinfo=UTC)


def test_a_skipped_bar_is_printed_with_its_reason_and_target() -> None:
    o = Outcome(datetime(2026, 11, 18, 14, 0, tzinfo=UTC), "skip", "at target", 1.0)
    line = describe_outcome(o)
    assert "14:00" in line and "skip" in line and "at target" in line and "1.000" in line


def test_the_status_line_shows_price_position_equity_and_the_next_close() -> None:
    now = datetime(2026, 11, 18, 13, 25, tzinfo=UTC)
    line = status_line(now, "1h", "BTC-USD", account("5000", "0.1", "60000"), trades=42)
    assert "60000" in line  # last price
    assert "0.1 BTC" in line  # position
    assert "5000" in line and "11000" in line  # cash and equity in USD
    assert "42 trades" in line
    assert "next bar 14:00" in line and "35m" in line


class FakeWorker:
    def __init__(self) -> None:
        self.outcomes: list[Outcome] = []
        self.acc = account("10000", "0", "60000")

    async def account(self) -> MarketAccount:
        return self.acc


async def _collect(progress: Progress, at: datetime) -> None:
    await progress.poll(at)


def test_progress_prints_each_new_outcome_once_and_status_on_its_interval() -> None:
    import asyncio

    worker = FakeWorker()
    lines: list[str] = []
    p = Progress(worker, "BTC-USD", "1h", every_seconds=60, log=lines.append)
    t = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)

    asyncio.run(_collect(p, t))
    assert len(lines) == 1 and "status" in lines[0]  # the first poll prints the status

    worker.outcomes.append(Outcome(t, "skip", "at target", 0.0))
    asyncio.run(_collect(p, t.replace(second=30)))
    assert len(lines) == 2 and "at target" in lines[1]  # new bar: printed, no status yet

    asyncio.run(_collect(p, t.replace(second=45)))
    assert len(lines) == 2  # nothing new, interval not reached

    p.count_trade()
    asyncio.run(_collect(p, t.replace(minute=1)))
    assert len(lines) == 3 and "status" in lines[2] and "1 trades" in lines[2]


def test_status_can_be_turned_off() -> None:
    import asyncio

    worker = FakeWorker()
    lines: list[str] = []
    p = Progress(worker, "BTC-USD", "1h", every_seconds=0, log=lines.append)
    asyncio.run(_collect(p, datetime(2026, 11, 18, 13, 0, tzinfo=UTC)))
    assert lines == []
