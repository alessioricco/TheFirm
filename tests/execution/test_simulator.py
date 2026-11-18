"""The paper simulator's fill model, rule by rule.

Costs: taker 80 bps, maker 40 bps (Kraken entry tier), spread 2 bps,
slippage 5 bps -> a market order pays 1 + 5 = 6 bps on price, then the fee.
Participation 10%: each market event fills at most 10% of its volume.
"""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from factories import order_intent
from firm.core.domain import ORDER_TRANSITIONS, OrderStatus, OrderType, Side, TimeInForce
from firm.execution.adapter import InstrumentRules, VenueMode
from firm.execution.simulator import MarketBar, MarketTrade, PaperSimulator, SimConfig

D = Decimal
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=D("0.1"),
    lot_size=D("0.0001"),
    min_quantity=D("0.0001"),
    min_notional=D("5"),
)


def config(**kw) -> SimConfig:
    fields = dict(
        venue="kraken",
        rules={"BTC-USD": RULES},
        maker_bps=D(40),
        taker_bps=D(80),
        spread_bps=D(2),
        slippage_bps=D(5),
        participation=D("0.1"),
        balances={"USD": D(100_000)},
    )
    fields.update(kw)
    return SimConfig(**fields)


def sim(**kw) -> PaperSimulator:
    s = PaperSimulator(VenueMode.PAPER, live_trading=False, config=config(**kw))
    s.on_market(
        MarketTrade(
            instrument="BTC-USD", time=T0 - timedelta(seconds=1), price=D(50_000), size=D(100)
        )
    )  # a last price before any order
    return s


def run(coro):
    return asyncio.run(coro)


def trade(sec: float, price: str, size: str = "100") -> MarketTrade:
    return MarketTrade(
        instrument="BTC-USD", time=T0 + timedelta(seconds=sec), price=D(price), size=D(size)
    )


def intent(**kw):
    kw.setdefault("venue", "kraken")
    kw.setdefault("created_at", T0)
    return order_intent(**kw)


# --- market orders


def test_market_buy_fills_on_the_next_trade_with_costs_and_taker_fee() -> None:
    s = sim()
    up = run(s.submit(intent(quantity=D("0.1"))))
    assert up.status is OrderStatus.ACCEPTED and up.filled_quantity == 0  # no same-instant fill
    s.on_market(trade(1, "50000"))
    (fill,) = s.fills()
    assert fill.price == D("50030.0")  # 50,000 x (1 + 6 bps)
    assert fill.fee == fill.quantity * fill.price * D("0.008") and fill.fee_currency == "USD"
    assert run(s.order_status("c-1")).status is OrderStatus.FILLED
    usd = next(b for b in run(s.balances()) if b.currency == "USD")
    assert usd.total == D(100_000) - D("0.1") * D("50030.0") - fill.fee
    btc = next(p for p in run(s.positions()) if p.instrument == "BTC-USD")
    assert btc.quantity == D("0.1")


def test_market_sell_receives_less_than_the_trade_price() -> None:
    s = sim(balances={"USD": D(0), "BTC": D(1)})
    run(s.submit(intent(side=Side.SELL, quantity=D("0.1"))))
    s.on_market(trade(1, "50000"))
    assert s.fills()[0].price == D("49970.0")


def test_bar_fills_never_outside_what_traded() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.1"))))
    s.on_market(
        MarketBar(
            instrument="BTC-USD",
            open_time=T0 + timedelta(minutes=1),
            close_time=T0 + timedelta(minutes=2),
            open=D(50_000),
            high=D("50010"),
            low=D(49_900),
            close=D(49_950),
            volume=D(100),
        )
    )
    assert s.fills()[0].price == D("50010")  # 50,030 would be above the bar's high: clipped


def test_bars_that_opened_before_the_order_are_ignored() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.1"), created_at=T0 + timedelta(seconds=30))))
    s.on_market(
        MarketBar(
            instrument="BTC-USD",
            open_time=T0,
            close_time=T0 + timedelta(minutes=1),
            open=D(40_000),
            high=D(50_000),
            low=D(40_000),
            close=D(50_000),
            volume=D(100),
        )
    )
    assert s.fills() == []  # that bar's open was before the order existed


# --- partial fills


def test_participation_cap_gives_partial_fills() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.25"))))
    s.on_market(trade(1, "50000", size="1"))  # 10% of 1 = 0.1
    assert run(s.order_status("c-1")).status is OrderStatus.PARTIALLY_FILLED
    s.on_market(trade(2, "50000", size="1"))
    s.on_market(trade(3, "50000", size="1"))
    assert [f.quantity for f in s.fills()] == [D("0.1"), D("0.1"), D("0.05")]
    assert run(s.order_status("c-1")).status is OrderStatus.FILLED


def test_ioc_fills_what_it_can_then_cancels() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.25"), time_in_force=TimeInForce.IOC)))
    s.on_market(trade(1, "50000", size="1"))
    up = run(s.order_status("c-1"))
    assert up.status is OrderStatus.CANCELLED and up.filled_quantity == D("0.1")


def test_fok_fills_completely_or_not_at_all() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.25"), time_in_force=TimeInForce.FOK)))
    s.on_market(trade(1, "50000", size="1"))
    up = run(s.order_status("c-1"))
    assert up.status is OrderStatus.CANCELLED and up.filled_quantity == 0 and s.fills() == []


def test_day_orders_expire_at_midnight_utc() -> None:
    s = sim()
    run(
        s.submit(
            intent(
                order_type=OrderType.LIMIT,
                limit_price=D(40_000),
                quantity=D("0.1"),
                time_in_force=TimeInForce.DAY,
            )
        )
    )
    s.on_market(trade(13 * 3600, "50000"))  # next day 01:00 UTC
    assert run(s.order_status("c-1")).status is OrderStatus.EXPIRED


# --- limit and stop orders


def test_resting_limit_buy_fills_at_its_limit_with_maker_fee() -> None:
    s = sim()
    run(s.submit(intent(order_type=OrderType.LIMIT, limit_price=D(49_000), quantity=D("0.1"))))
    s.on_market(trade(1, "49500"))
    assert s.fills() == []
    s.on_market(trade(2, "48900"))
    (fill,) = s.fills()
    assert fill.price == D(49_000) and fill.fee == fill.quantity * fill.price * D("0.004")


def test_stop_sell_rests_then_triggers_and_gaps_fill_at_the_gap() -> None:
    s = sim(balances={"USD": D(0), "BTC": D(1)})
    run(
        s.submit(
            intent(
                side=Side.SELL, order_type=OrderType.STOP, stop_price=D(48_000), quantity=D("0.1")
            )
        )
    )
    assert [o.client_order_id for o in run(s.open_orders())] == ["c-1"]  # rests at the venue
    s.on_market(trade(1, "49000"))
    assert s.fills() == []
    s.on_market(trade(2, "47000"))  # gapped through the stop
    assert s.fills()[0].price == D("46971.8")  # 47,000 - 6 bps, not the better 48,000


def test_stop_limit_triggers_into_a_limit() -> None:
    s = sim()
    run(
        s.submit(
            intent(
                order_type=OrderType.STOP_LIMIT,
                stop_price=D(51_000),
                limit_price=D(51_100),
                quantity=D("0.1"),
            )
        )
    )
    s.on_market(trade(1, "51200"))  # triggers; trades above the limit
    assert s.fills() == []
    s.on_market(trade(2, "51050"))
    assert s.fills()[0].price == D(51_100)


# --- rejections before anything leaves


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        (dict(quantity=D("0.00005")), "minimum quantity"),
        (dict(quantity=D("0.00015")), "lot size"),
        (
            dict(order_type=OrderType.LIMIT, limit_price=D("49000.05"), quantity=D("0.1")),
            "tick size",
        ),
        (dict(quantity=D("10")), "insufficient"),
        (dict(side=Side.SELL, quantity=D("0.1")), "insufficient"),
        (dict(instrument="ETH-USD", quantity=D("0.1")), "instrument"),
    ],
)
def test_bad_orders_are_rejected(kw, why) -> None:
    s = sim()
    up = run(s.submit(intent(**kw)))
    assert up.status is OrderStatus.REJECTED and why in up.reason


def test_cash_is_reserved_for_open_buys() -> None:
    s = sim(balances={"USD": D(10_000)})
    a = run(
        s.submit(
            intent(
                order_type=OrderType.LIMIT,
                limit_price=D(40_000),
                quantity=D("0.2"),
                client_order_id="a",
            )
        )
    )  # ~£8k reserved
    b = run(
        s.submit(
            intent(
                order_type=OrderType.LIMIT,
                limit_price=D(40_000),
                quantity=D("0.2"),
                client_order_id="b",
            )
        )
    )
    assert a.status is OrderStatus.ACCEPTED and b.status is OrderStatus.REJECTED
    usd = next(x for x in run(s.balances()) if x.currency == "USD")
    assert usd.available < usd.total


def test_a_buy_sized_with_the_taker_buffer_is_affordable() -> None:
    """The worker sizes a full buy as cash / (price x (1 + buffer)); the buffer
    must cover what submit reserves, or a 100% target is always rejected."""
    s = sim(balances={"USD": D(10_000)})
    buffer = s.taker_buffer_bps()
    assert buffer == (D(1) + D(6) / 10_000) * (D(1) + D(80) / 10_000) * 10_000 - 10_000
    q = D(10_000) / (D(50_000) * (1 + buffer / 10_000))
    q = (q / RULES.lot_size).to_integral_value(rounding="ROUND_DOWN") * RULES.lot_size
    up = run(s.submit(intent(quantity=q)))
    assert up.status is OrderStatus.ACCEPTED, up.reason
    additive = D(80 + 1 + 5)  # fees and costs added, not compounded: too little
    assert additive < buffer


# --- lifecycle, idempotency, traceability, determinism


def test_cancel_and_terminal_states() -> None:
    s = sim()
    run(s.submit(intent(order_type=OrderType.LIMIT, limit_price=D(40_000), quantity=D("0.1"))))
    assert run(s.cancel("c-1")).status is OrderStatus.CANCELLED
    assert run(s.cancel("c-1")).status is OrderStatus.CANCELLED  # terminal: unchanged
    with pytest.raises(LookupError):
        run(s.cancel("nope"))


def test_submit_is_idempotent_and_conflicts_are_refused() -> None:
    s = sim()
    a = run(s.submit(intent(quantity=D("0.1"))))
    b = run(s.submit(intent(quantity=D("0.1"))))
    assert a == b and len(run(s.open_orders())) == 1
    with pytest.raises(ValueError, match="client order id"):
        run(s.submit(intent(quantity=D("0.2"))))


def test_every_transition_is_legal_and_every_fill_traces_to_its_verdict() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.25"), verdict_id="v-9", risk_decision_id="r-9")))
    for i in range(1, 4):
        s.on_market(trade(i, "50000", size="1"))
    hist = [u.status for u in s.history("c-1")]
    assert all(b in ORDER_TRANSITIONS[a] for a, b in itertools.pairwise(hist))
    assert all((f.verdict_id, f.risk_decision_id) == ("v-9", "r-9") for f in s.fills())


def test_same_events_give_the_same_fills() -> None:
    def go():
        s = sim()
        run(s.submit(intent(quantity=D("0.25"))))
        for i in range(1, 4):
            s.on_market(trade(i, str(50_000 + i * 10), size="1"))
        return s.fills()

    assert go() == go()


def test_reconcile_matches_balances_positions_and_open_orders() -> None:
    s = sim()
    run(s.submit(intent(quantity=D("0.1"), client_order_id="m")))
    run(
        s.submit(
            intent(
                order_type=OrderType.LIMIT,
                limit_price=D(40_000),
                quantity=D("0.1"),
                client_order_id="l",
            )
        )
    )
    s.on_market(trade(1, "50000"))
    snap = run(s.reconcile())
    assert snap.venue == "kraken"
    assert [o.client_order_id for o in snap.open_orders] == ["l"]
    assert snap.positions == run(s.positions()) and snap.balances == run(s.balances())


def test_needs_config_but_refuses_live_first() -> None:
    from firm.execution.adapter import LiveTradingRefused

    with pytest.raises(LiveTradingRefused):
        PaperSimulator(VenueMode.LIVE, live_trading=False)
    with pytest.raises(ValueError, match="config"):
        PaperSimulator(VenueMode.PAPER, live_trading=False)
