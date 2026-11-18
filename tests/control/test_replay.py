"""Replay: history through the real paper path must agree with the verifier's backtest.

Synthetic daily bars, the example moving-average strategy, no network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from factories import verdict
from firm.control.replay import replay
from firm.core.config import load_config, load_fee_schedule
from firm.core.domain import LedgerEventType
from firm.core.events import CollectingSink
from firm.data.synthetic import synthetic_bars
from firm.execution.adapter import InstrumentRules
from firm.execution.worker import Deployment
from firm.verify.strategy import load_strategy
from firm.verify.verifier import cost_bps

D = Decimal
REPO = Path(__file__).resolve().parents[2]
CFG = load_config(REPO / "config", "uk", ["crypto"])
PROFILE = CFG.markets["crypto"]
FEES = load_fee_schedule(REPO / "config", PROFILE.costs.fee_schedule_for("coinbase"))
STRATEGY = load_strategy(
    REPO / "examples/strategies/ma_crossover.py", "MovingAverageCrossover"
).strategy
RULES = InstrumentRules(
    instrument="BTC-USD",
    base_currency="BTC",
    quote_currency="USD",
    tick_size=D("0.01"),
    lot_size=D("0.00000001"),
    min_quantity=D("0.00000001"),
    min_notional=D("1"),
)
UNCAPPED = CFG.risk.model_copy(
    update={
        "limits": CFG.risk.limits.model_copy(
            update={
                "max_order_notional_pct": 100,
                "daily_loss_limit_pct": 100,
                "max_drawdown_pct": 100,
            }
        ),
        "markets": {
            "crypto": CFG.risk.markets["crypto"].model_copy(
                update={
                    "max_position_pct": 100,
                    "max_market_exposure_pct": 100,
                    "exposure_cap_per_exchange_pct": 100,
                    "max_drawdown_pct": 100,
                    "paper_allocation_pct": 100,
                }
            )
        },
    }
)


def _bars(n: int = 400) -> pd.DataFrame:
    rows = synthetic_bars(n, seed=11, start=datetime(2024, 1, 1, tzinfo=UTC), tf="1d")
    df = pd.DataFrame([r.__dict__ for r in rows])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="s", utc=True)
    df["close_time"] = df["open_time"] + pd.Timedelta(days=1)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]


def _deployment(params: dict, lookback: int) -> Deployment:
    return Deployment(
        verdict=verdict(strategy_id="ma_crossover", verdict_id="v-replay"),
        strategy_id="ma_crossover",
        params=params,
        market="crypto",
        venue="coinbase",
        instrument="BTC-USD",
        tf="1d",
        quote_currency="USD",
        lookback_bars=lookback,
        rebalance_threshold_pct=PROFILE.execution.rebalance_threshold_pct,
        cost_buffer_bps=(
            (1 + D(str(PROFILE.costs.spread_bps / 2 + PROFILE.costs.slippage_bps)) / 10_000)
            * (1 + D(str(FEES.taker_bps)) / 10_000)
            - 1
        )
        * 10_000,  # as PaperSimulator.taker_buffer_bps
    )


def _run(risk=UNCAPPED, params=None, lookback=60, sink=None):
    params = params or {"fast": 5, "slow": 20}
    return asyncio.run(
        replay(
            _bars(),
            deployment=_deployment(params, lookback),
            strategy=STRATEGY,
            profile=PROFILE,
            fees=FEES,
            risk=risk,
            rules=RULES,
            gbp_per_quote=D("0.8"),
            decide_from=100,
            cost_bps=cost_bps(PROFILE, FEES),
            **({"sink": sink} if sink else {}),
        )
    )


def test_uncapped_paper_trades_exactly_like_the_backtest() -> None:
    r = _run()
    s = r.summary()
    assert s["bars_decided"] == 300
    assert s["backtest_trades"] >= 6  # enough round trips to mean something
    assert s["signal_mismatches"] == 0
    assert s["missed"] == 0 and s["extra_orders"] == 0
    assert s["fills"] == s["paper_orders"]  # one full fill per order at the next open
    # Same trades, same timing: returns agree within the cost buffer and rounding.
    assert r.paper_return == pytest.approx(r.backtest_return, abs=0.005)
    assert r.paper_exposure == pytest.approx(r.backtest_exposure, abs=0.005)


def test_fills_happen_at_the_next_bar_open_with_costs_against_us() -> None:
    r = _run()
    bars = _bars()
    opens = dict(zip(bars["open_time"].dt.to_pydatetime(), bars["open"], strict=True))
    slips = r.fill_slippage_bps(opens)
    assert len(slips) == len(r.fills) > 0  # every fill is at some bar's open time
    half_spread_plus_slip = PROFILE.costs.spread_bps / 2 + PROFILE.costs.slippage_bps
    assert all(0 <= s <= half_spread_plus_slip + 1 for s in slips)
    decided = {b.close_time for b in r.bars if b.action == "submitted"}
    assert all(f.filled_at in decided for f in r.fills)  # decided at a close = next open


def test_every_fill_traces_to_the_verdict() -> None:
    sink = CollectingSink()
    r = _run(sink=sink)
    assert r.fills and all(f.verdict_id == "v-replay" for f in r.fills)
    intents = {
        payload["client_order_id"]: payload
        for kind, _, payload, _ in sink.events
        if kind is LedgerEventType.ORDER_INTENT
    }
    for f in r.fills:
        i = intents[f.client_order_id]
        assert (i["verdict_id"], i["risk_decision_id"]) == (f.verdict_id, f.risk_decision_id)


def test_the_risk_caps_show_up_as_reasons_not_silence() -> None:
    capped = _run(risk=CFG.risk)
    s = capped.summary()
    assert s["signal_mismatches"] == 0
    assert capped.paper_exposure < 0.5 * capped.backtest_exposure  # 20% position cap
    # Every backtest trade either has an order or a recorded reason.
    for b in capped.missed:
        assert b.reason and b.action in ("skip", "rejected")


def test_a_lookback_too_short_for_the_strategy_is_caught() -> None:
    r = _run(lookback=10, params={"fast": 5, "slow": 20})  # needs 20 bars
    assert r.summary()["signal_mismatches"] > 0


def test_needs_history_before_the_first_decision() -> None:
    with pytest.raises(ValueError, match="history"):
        asyncio.run(
            replay(
                _bars(),
                deployment=_deployment({"fast": 5, "slow": 20}, 200),
                strategy=STRATEGY,
                profile=PROFILE,
                fees=FEES,
                risk=UNCAPPED,
                rules=RULES,
                gbp_per_quote=D("0.8"),
                decide_from=100,
                cost_bps=10.0,
            )
        )


def test_dates_are_utc() -> None:
    assert _run().bars[0].close_time.utcoffset() == timedelta(0)
