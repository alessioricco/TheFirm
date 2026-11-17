"""The risk desk, rule by rule, against the owner's real risk profile.

Numbers: paper capital and equity £10,000; BTC at $50,000; USD->GBP 0.8, so
0.05 BTC = £2,000. Limits from config/risk/default.yaml: single order 10%
(£1,000), position 20% (£2,000), crypto market 60%, one exchange 50%, total
100%, daily loss 3%, drawdown 10% per market and 15% total.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from factories import T0, order_proposal, verdict
from firm.core.config import load_risk
from firm.core.domain import (
    CheckResult,
    CheckStatus,
    LedgerEventType,
    RiskAction,
    Side,
    VerdictOutcome,
    build_intent,
)
from firm.core.events import CollectingSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.core.messages.subjects import HaltScope, OwnerCommand, OwnerCommandKind
from firm.core.risk_state import Holding, RiskState
from firm.risk.desk import RiskDesk

PROFILE = load_risk(Path(__file__).resolve().parents[2] / "config" / "risk" / "default.yaml")
D = Decimal


def state(**kw) -> RiskState:
    fields = dict(
        as_of=T0,
        equity_gbp=D(10_000),
        peak_equity_gbp=D(10_000),
        day_start_equity_gbp=D(10_000),
        holdings=(),
        market_pnl_gbp={},
        market_peak_pnl_gbp={},
        fx_to_gbp={"USD": D("0.8")},
    )
    fields.update(kw)
    return RiskState(**fields)


def btc(qty: str, venue: str = "kraken", instrument: str = "BTC-USD") -> Holding:
    return Holding(
        market="crypto",
        venue=venue,
        instrument=instrument,
        quantity=D(qty),
        mark_price=D(50_000),
        quote_currency="USD",
    )


def proposal(qty: str, **kw):
    return order_proposal(quantity=D(qty), venue="kraken", **kw)


@pytest.fixture
def desk() -> RiskDesk:
    return RiskDesk(PROFILE)


# --- the happy path, and the order path stays intact


def test_small_buy_is_approved_and_buildable(desk) -> None:
    p = proposal("0.01")  # £400
    d = desk.decide(verdict(), p, state())
    assert d.action is RiskAction.APPROVE and d.approved_quantity == D("0.01")
    assert build_intent(p, d, intent_id="i", client_order_id="c", created_at=T0).quantity == D(
        "0.01"
    )


# --- verdict gate


@pytest.mark.parametrize(
    "v",
    [
        verdict(verdict_id="v-other"),
        verdict(strategy_id="someone-else"),
        verdict(
            outcome=VerdictOutcome.FAIL,
            checks=(CheckResult(name="x", status=CheckStatus.FAILED, detail="d"),),
        ),
        verdict(
            outcome=VerdictOutcome.UNVERIFIED,
            checks=(CheckResult(name="x", status=CheckStatus.NOT_RUN, detail="d"),),
        ),
    ],
)
def test_only_a_matching_passed_verdict_can_trade(desk, v) -> None:
    d = desk.decide(v, proposal("0.01"), state())
    assert d.action is RiskAction.REJECT and "verdict" in d.reason


# --- size limits shrink buys to the tightest one


def test_single_order_cap_shrinks(desk) -> None:
    d = desk.decide(verdict(), proposal("0.05"), state())  # £2,000 > £1,000 cap
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.025")
    assert "single order" in d.reason


def test_position_cap_counts_what_is_already_held(desk) -> None:
    held = state(holdings=(btc("0.0375"),))  # £1,500 held; position cap £2,000
    d = desk.decide(verdict(), proposal("0.02"), held)  # £800 asked, £500 left
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.0125")
    assert "position" in d.reason


def test_exchange_cap(desk) -> None:
    # £4,800 already on kraken in other coins; exchange cap £5,000 -> £200 left
    other = Holding(
        market="crypto",
        venue="kraken",
        instrument="ETH-USD",
        quantity=D(2),
        mark_price=D(3_000),
        quote_currency="USD",
    )
    d = desk.decide(verdict(), proposal("0.01"), state(holdings=(other,)))  # £400 asked
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.005")
    assert "exchange" in d.reason


def test_market_cap(desk) -> None:
    # £5,800 in crypto across exchanges; market cap £6,000 -> £200 left
    hs = (
        Holding(
            market="crypto",
            venue="coinbase",
            instrument="ETH-USD",
            quantity=D(2),
            mark_price=D(3_000),
            quote_currency="USD",
        ),
        Holding(
            market="crypto",
            venue="kraken",
            instrument="SOL-USD",
            quantity=D("12.5"),
            mark_price=D(100),
            quote_currency="USD",
        ),
    )  # £4,800 + £1,000
    d = desk.decide(verdict(), proposal("0.01"), state(holdings=hs))
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.005")
    assert "market" in d.reason


def test_total_cap_spans_markets(desk) -> None:
    shares = Holding(
        market="us_equities",
        venue="xnas",
        instrument="NVDA.XNAS",
        quantity=D("62.5"),
        mark_price=D(100),
        quote_currency="USD",
    )  # £5,000
    crypto = Holding(
        market="crypto",
        venue="coinbase",
        instrument="ETH-USD",
        quantity=D(2),
        mark_price=D(3_000),
        quote_currency="USD",
    )  # £4,800
    d = desk.decide(verdict(), proposal("0.01"), state(holdings=(shares, crypto)))
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.01") / 2
    assert "total" in d.reason


def test_nothing_left_means_reject(desk) -> None:
    full = state(holdings=(btc("0.05"),))  # position cap reached
    d = desk.decide(verdict(), proposal("0.01"), full)
    assert d.action is RiskAction.REJECT and "position" in d.reason


def test_missing_fx_rate_is_refused_not_guessed(desk) -> None:
    d = desk.decide(verdict(), proposal("0.01", quote_currency="USDT"), state())
    assert d.action is RiskAction.REJECT and "FX" in d.reason


# --- sells: long-only spot, risk-reducing


def test_sell_capped_at_what_is_held(desk) -> None:
    held = state(holdings=(btc("0.004"),))
    d = desk.decide(verdict(), proposal("0.01", side=Side.SELL), held)
    assert d.action is RiskAction.RESIZE and d.approved_quantity == D("0.004")
    d2 = desk.decide(verdict(), proposal("0.01", side=Side.SELL, proposal_id="p-2"), state())
    assert d2.action is RiskAction.REJECT and "nothing to sell" in d2.reason


# --- daily loss limit


def test_daily_loss_limit_blocks_buys_but_not_sells(desk) -> None:
    down = state(equity_gbp=D(9_690), peak_equity_gbp=D(10_000), holdings=(btc("0.004"),))
    buy = desk.decide(verdict(), proposal("0.001"), down)
    assert buy.action is RiskAction.REJECT and "daily loss" in buy.reason
    sell = desk.decide(verdict(), proposal("0.004", side=Side.SELL, proposal_id="p-s"), down)
    assert sell.action is RiskAction.APPROVE


# --- drawdowns trip kill switches; only the owner releases them


def test_global_drawdown_trips_the_global_kill_switch(desk) -> None:
    deep = state(equity_gbp=D(8_400), day_start_equity_gbp=D(8_400), peak_equity_gbp=D(10_000))
    d = desk.decide(verdict(), proposal("0.001"), deep)
    assert d.action is RiskAction.REJECT and "drawdown" in d.reason
    assert desk.halted() and desk.halted("crypto")
    calm = state()
    assert (
        desk.decide(verdict(), proposal("0.001", proposal_id="p-9"), calm).action
        is RiskAction.REJECT
    )  # still halted although conditions recovered
    desk.resume(
        OwnerCommand(
            command_id="cmd-1",
            kind=OwnerCommandKind.RESUME,
            scope=HaltScope.GLOBAL,
            reason="reviewed",
            issued_at=T0,
        )
    )
    assert not desk.halted()
    assert (
        desk.decide(verdict(), proposal("0.001", proposal_id="p-10"), calm).action
        is RiskAction.APPROVE
    )


def test_market_drawdown_halts_only_that_market(desk) -> None:
    hurt = state(market_pnl_gbp={"crypto": D(-600)}, market_peak_pnl_gbp={"crypto": D(500)})
    d = desk.decide(verdict(), proposal("0.001"), hurt)  # 1,100 below peak = 11% of equity
    assert d.action is RiskAction.REJECT and desk.halted("crypto")
    assert not desk.halted() and not desk.halted("us_equities")


def test_owner_kill_and_resume_commands(desk) -> None:
    desk.command(
        OwnerCommand(
            command_id="k",
            kind=OwnerCommandKind.KILL,
            scope=HaltScope.MARKET,
            market="crypto",
            reason="owner",
            issued_at=T0,
        )
    )
    assert desk.decide(verdict(), proposal("0.001"), state()).action is RiskAction.REJECT
    desk.command(
        OwnerCommand(
            command_id="r",
            kind=OwnerCommandKind.RESUME,
            scope=HaltScope.MARKET,
            market="crypto",
            reason="owner",
            issued_at=T0,
        )
    )
    assert not desk.halted("crypto")


def test_limits_cannot_be_changed_through_the_desk(desk) -> None:
    with pytest.raises(ValidationError):  # the profile is frozen
        desk.profile.limits.max_drawdown_pct = 99  # type: ignore[misc]
    assert not any(hasattr(desk, m) for m in ("set_limit", "update_limits", "set_profile"))


# --- deterministic and recorded


def test_decisions_are_deterministic_and_recorded() -> None:
    sink = CollectingSink()
    a = RiskDesk(PROFILE, sink=sink).decide(verdict(), proposal("0.05"), state())
    b = RiskDesk(PROFILE).decide(verdict(), proposal("0.05"), state())
    assert a == b and a.decision_id.startswith("r-")
    ((kind, reason, payload, event_id),) = sink.events
    assert kind is LedgerEventType.RISK_DECISION and event_id == f"risk:{a.decision_id}"
    assert payload["action"] == "resize" and reason


def test_halts_are_recorded() -> None:
    sink = CollectingSink()
    deep = state(equity_gbp=D(8_400), day_start_equity_gbp=D(8_400))
    RiskDesk(PROFILE, sink=sink).decide(verdict(), proposal("0.001"), deep)
    kinds = [e[0] for e in sink.events]
    assert LedgerEventType.RISK_HALT in kinds and LedgerEventType.RISK_DECISION in kinds


# --- heartbeat


def test_heartbeat_and_monitor() -> None:
    desk = RiskDesk(PROFILE)
    beats = [desk.heartbeat(T0 + timedelta(seconds=i)) for i in range(3)]
    assert [b.sequence for b in beats] == [0, 1, 2]
    mon = HeartbeatMonitor(timeout=timedelta(seconds=5))
    assert not mon.alive(T0)  # nothing heard yet
    mon.beat(beats[-1])
    assert mon.alive(T0 + timedelta(seconds=6))
    assert not mon.alive(T0 + timedelta(seconds=8))
    with pytest.raises(ValueError):
        mon.beat(beats[0])  # going backwards is refused
