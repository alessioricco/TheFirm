"""Pieces of `firm paper run`: venue bars at each close, and a paper account that
survives restarts. Synthetic data in the venue's format; no network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from factories import T0
from firm.control.paper import OPENED, RestBars, balances_after, paper_account
from firm.core.domain import Fill, LedgerEventType, Side
from firm.data.fetch import Fetcher
from firm.data.providers.base import NS
from firm.data.synthetic import coinbase_candles_payload, synthetic_bars
from firm.execution.adapter import InstrumentRules
from firm.ledger.store import Ledger

D = Decimal
START = datetime(2026, 9, 1, tzinfo=UTC)
BARS = synthetic_bars(120, seed=3, start=START, tf="1d")


class Venue:
    """Coinbase-style candles endpoint: serves the bars that have closed by `now`."""

    def __init__(self) -> None:
        self.now = START
        self.calls = 0

    def __call__(self, url: str) -> bytes:
        self.calls += 1
        q = parse_qs(urlparse(url).query)
        lo = pd.Timestamp(q["start"][0]).timestamp()
        hi = pd.Timestamp(q["end"][0]).timestamp()
        # the venue also serves the still-forming bar; the parser must drop it
        page = [b for b in BARS if lo <= b.open_time <= hi and b.open_time <= self.now.timestamp()]
        return coinbase_candles_payload(page)


def rest(tmp_path: Path, venue: Venue) -> RestBars:
    fetcher = Fetcher(
        tmp_path, transport=venue, clock=lambda: venue.now, sleep=lambda s: None, log=lambda s: None
    )
    return RestBars(fetcher, "coinbase", "BTC-USD", "1d")


def day(i: int, hours: float = 0) -> datetime:
    return START + timedelta(days=i, hours=hours)


def test_warm_up_then_one_new_closed_bar_per_close(tmp_path: Path) -> None:
    venue = Venue()
    bars = rest(tmp_path, venue)
    venue.now = day(60, 5)  # 05:00 on day 60: day 59 closed at midnight, day 60 still forming
    history = bars.warm_up(venue.now, 40)
    assert len(history) == 40
    assert history[-1]["open_time_ns"] == int(day(59).timestamp()) * NS  # never the forming bar

    async def poll(at: datetime):
        venue.now = at
        return await bars(at)

    calls = venue.calls
    assert asyncio.run(poll(day(60, 23))) == [] and venue.calls == calls  # not due: no request
    (new,) = asyncio.run(poll(day(61, 0) + timedelta(seconds=15)))  # day 60 closed + grace
    assert new["open_time_ns"] == int(day(60).timestamp()) * NS
    assert asyncio.run(poll(day(61, 1))) == []  # next one is due tomorrow


def test_a_late_venue_is_retried_and_downtime_returns_every_missed_bar(tmp_path: Path) -> None:
    venue = Venue()
    bars = rest(tmp_path, venue)
    venue.now = day(60, 5)
    bars.warm_up(venue.now, 40)

    async def poll(at: datetime, published_until: datetime):
        venue.now = published_until
        return await bars(at)

    due = day(61) + timedelta(seconds=15)
    assert asyncio.run(poll(due, day(60, 23))) == []  # venue hasn't published day 60 yet
    assert bars.next_due == due + timedelta(seconds=30)
    missed = asyncio.run(poll(day(64, 2), day(64, 2)))  # back after three days down
    assert [b["open_time_ns"] for b in missed] == [
        int(day(i).timestamp()) * NS for i in (60, 61, 62, 63)
    ]


RULES = {
    "BTC-USD": InstrumentRules(
        instrument="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        tick_size=D("0.01"),
        lot_size=D("0.00000001"),
        min_quantity=D("0.00000001"),
        min_notional=D("1"),
    )
}


def fill(n: int, side: Side, qty: str, price: str, fee: str, venue: str = "coinbase") -> Fill:
    return Fill(
        fill_id=f"c-{n}-f1",
        client_order_id=f"c-{n}",
        intent_id=f"i-{n}",
        verdict_id="v-1",
        risk_decision_id=f"r-{n}",
        venue=venue,
        instrument="BTC-USD",
        side=side,
        quantity=D(qty),
        price=D(price),
        fee=D(fee),
        fee_currency="USD",
        filled_at=T0 + timedelta(minutes=n),
    )


def opened(ledger: Ledger, usd: str, n: int = 0) -> None:
    ledger.append(
        LedgerEventType.MARKET_STATUS,
        "opened",
        {"kind": OPENED, "market": "crypto", "venue": "coinbase", "balances": {"USD": usd}},
        event_id=f"open-{n}",
    )


def test_the_paper_account_is_rebuilt_from_the_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    assert paper_account(ledger, "crypto", "coinbase") == (None, [])
    opened(ledger, "6250.00")
    for f in (
        fill(1, Side.BUY, "0.02", "50000", "8"),
        fill(2, Side.SELL, "0.01", "51000", "4"),
        fill(3, Side.BUY, "1", "1", "0", venue="kraken"),
    ):  # another venue: not ours
        ledger.append(
            LedgerEventType.FILL, "fill", f.model_dump(mode="json"), event_id=f"fill:{f.fill_id}"
        )
    opening, fills = paper_account(ledger, "crypto", "coinbase")
    assert opening == {"USD": D("6250.00")} and len(fills) == 2
    assert balances_after(opening, fills, RULES) == {
        "USD": D("6250") - D("1000") - D("8") + D("510") - D("4"),
        "BTC": D("0.01"),
    }


def test_rebuilt_balances_that_go_negative_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        balances_after({"USD": D(100)}, [fill(1, Side.BUY, "1", "50000", "0")], RULES)
