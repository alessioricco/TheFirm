"""Backtest engine: timing, costs and metrics against hand-computed numbers."""

import numpy as np
import pandas as pd
import pytest

from firm.verify.backtest import SignalError, metrics, run_backtest, sharpe_per_bar


def _bars(opens, closes) -> pd.DataFrame:
    return pd.DataFrame({"open": opens, "close": closes})


def test_fill_at_next_open_and_gap_held_by_previous_position() -> None:
    bars = _bars([100, 100, 110, 121], [100, 110, 121, 121])
    sig = pd.Series([1.0, 1.0, 0.0, 0.0])  # decided at closes of bars 0, 1, 2, 3
    r = run_backtest(bars, sig, cost_bps=0.0)
    # bar0: flat. bar1: long from open (target[0]) 100->110 = +10%.
    # bar2: gap 110->110 held (target[0]) = 0, day long (target[1]) 110->121 = +10%.
    # bar3: gap 121->121 by target[1]; day flat (target[2] = 0).
    assert np.allclose(r.net, [0.0, 0.10, 0.10, 0.0])
    assert np.allclose(r.turnover, [0.0, 1.0, 0.0, 1.0])


def test_no_same_bar_fill() -> None:
    bars = _bars([100, 100], [100, 200])
    r = run_backtest(bars, pd.Series([0.0, 1.0]), cost_bps=0.0)
    assert r.net[1] == 0.0  # the signal at bar 1's close can't earn bar 1's move


def test_costs_charged_per_unit_traded() -> None:
    bars = _bars([100.0] * 4, [100.0] * 4)
    r = run_backtest(bars, pd.Series([1.0, 0.0, 1.0, 1.0]), cost_bps=50.0)
    assert np.allclose(r.net, [0.0, -0.005, -0.005, -0.005])


def test_warmup_nan_is_flat_and_out_of_range_refused() -> None:
    bars = _bars([1.0] * 3, [1.0] * 3)
    run_backtest(bars, pd.Series([np.nan, 0.5, 1.0]), cost_bps=0)
    for bad in ([0, -0.1, 0], [0, 1.5, 0]):
        with pytest.raises(SignalError):
            run_backtest(bars, pd.Series(bad, dtype=float), cost_bps=0)
    with pytest.raises(SignalError):
        run_backtest(bars, pd.Series([0.0, 1.0]), cost_bps=0)


def test_metrics() -> None:
    r = np.array([0.0, 0.01, -0.02, 0.03])
    assert sharpe_per_bar(r) == pytest.approx(np.mean(r) / np.std(r, ddof=1))
    bars = _bars([100, 100, 110, 99], [100, 110, 99, 99])
    res = run_backtest(bars, pd.Series([1.0, 1.0, 1.0, 1.0]), cost_bps=0)
    m = metrics(res, 0, 4, 365)
    assert m["total_return"] == pytest.approx(1.10 * 0.90 - 1)
    assert m["max_drawdown"] == pytest.approx(0.10)


def test_position_scales_returns_and_costs_but_not_sharpe() -> None:
    """The verifier sizes like the risk desk (owner, 2026-11-18: positions are
    capped at 20%, never 100%): a target of 1 holds `position` of equity."""
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.02, 300))
    opens = np.concatenate([[100.0], closes[:-1]]) * (1 + rng.normal(0, 0.001, 300))
    bars = _bars(opens, closes)
    sig = pd.Series(rng.uniform(0, 1, 300))
    full = run_backtest(bars, sig, cost_bps=20.0)
    fifth = run_backtest(bars, sig, cost_bps=20.0, position=0.2)
    assert np.allclose(fifth.net, 0.2 * full.net)
    assert np.allclose(fifth.turnover, 0.2 * full.turnover)
    assert sharpe_per_bar(fifth.net) == pytest.approx(sharpe_per_bar(full.net))
    m_full, m_fifth = metrics(full, 0, 300, 365), metrics(fifth, 0, 300, 365)
    assert m_fifth["exposure"] == pytest.approx(0.2 * m_full["exposure"])
    assert m_fifth["max_drawdown"] < m_full["max_drawdown"]
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            run_backtest(bars, sig, cost_bps=0, position=bad)
