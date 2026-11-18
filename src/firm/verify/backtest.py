"""Event-driven bar backtest and its metrics. Deterministic, no I/O.

Timing (no same-bar fills):

- `target[i]` is decided at the close of bar i from rows <= i.
- It is filled at the open of bar i+1, paying `cost_bps` per unit of
  exposure traded (fees + half spread + slippage, from the market profile).
- Bar i's return: the position held through the gap close[i-1] -> open[i]
  (`target[i-2]`) plus the position held open[i] -> close[i] (`target[i-1]`).
- Sizing: a target of 1 holds `position` of equity (the risk desk's
  per-instrument cap), so returns, costs and exposure scale by it. A Sharpe
  ratio doesn't: every pass/fail check is unchanged by the size.

Returns are simple, per bar, as a fraction of equity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


class SignalError(ValueError):
    """A strategy produced signals the engine refuses (wrong length, out of range)."""


@dataclass(frozen=True)
class BacktestResult:
    net: np.ndarray  # per-bar net returns, aligned with bars (bar 0 = 0)
    gross: np.ndarray
    turnover: np.ndarray  # |change in exposure| filled at each bar's open
    targets: np.ndarray


def clean_targets(signals: pd.Series, n: int) -> np.ndarray:
    if len(signals) != n:
        raise SignalError(f"signals length {len(signals)} != bars {n}")
    t = signals.to_numpy(dtype=float, na_value=np.nan)
    t = np.where(np.isnan(t), 0.0, t)
    if np.any(t < 0) or np.any(t > 1):
        raise SignalError("targets must be within [0, 1] (spot, no leverage, no shorting)")
    return t


def run_backtest(
    bars: pd.DataFrame, signals: pd.Series, *, cost_bps: float, position: float = 1.0
) -> BacktestResult:
    if not 0 < position <= 1:
        raise ValueError(f"position {position} outside (0, 1]")
    n = len(bars)
    target = clean_targets(signals, n) * position
    o = bars["open"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    held_day = np.concatenate([[0.0], target[:-1]])  # exposure open[i] -> close[i]
    held_gap = np.concatenate([[0.0, 0.0], target[:-2]])[:n]  # close[i-1] -> open[i]
    prev_close = np.concatenate([[np.nan], c[:-1]])
    gap_ret = np.where(np.isnan(prev_close), 0.0, o / prev_close - 1.0)
    gross = held_gap * gap_ret + held_day * (c / o - 1.0)
    turnover = np.abs(held_day - np.concatenate([[0.0], held_day[:-1]]))
    net = gross - turnover * cost_bps / 1e4
    return BacktestResult(net=net, gross=gross, turnover=turnover, targets=target)


def sharpe_per_bar(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(r)) / sd


def metrics(
    result: BacktestResult, start: int, end: int, periods_per_year: float
) -> dict[str, float]:
    """Metrics over bars [start, end)."""
    r = result.net[start:end]
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    sr = sharpe_per_bar(r)
    return {
        "bars": float(len(r)),
        "total_return": float(equity[-1] - 1.0) if len(r) else 0.0,
        "sharpe": sr * math.sqrt(periods_per_year),
        "sharpe_per_bar": sr,
        "max_drawdown": float(np.max(1.0 - equity / peak)) if len(r) else 0.0,
        "turnover": float(np.sum(result.turnover[start:end])),
        "exposure": float(np.mean(np.concatenate([[0.0], result.targets[:-1]])[start:end]))
        if len(r)
        else 0.0,
    }
