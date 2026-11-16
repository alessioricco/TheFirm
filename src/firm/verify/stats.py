"""Trial-count correction: the deflated Sharpe ratio.

Bailey & López de Prado (2014), "The Deflated Sharpe Ratio". The more
strategies were tried, the higher the Sharpe ratio the best one reaches by
luck alone (`expected_max_sharpe`); the DSR is the probability that the
observed Sharpe beats that bar, given the sample length and the returns'
skewness and kurtosis. All Sharpe ratios here are per bar (not annualised).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """SR0: the expected maximum of `n_trials` Sharpe ratios under the null (true SR 0)."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    z1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def moments(r: np.ndarray) -> tuple[float, float]:
    """Sample skewness and (non-excess) kurtosis."""
    d = r - r.mean()
    m2 = float(np.mean(d**2))
    if m2 == 0.0:
        return 0.0, 3.0
    return float(np.mean(d**3)) / m2**1.5, float(np.mean(d**4)) / m2**2


def probabilistic_sharpe(
    sr: float, benchmark: float, n_obs: int, skew: float, kurt: float
) -> float:
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if n_obs < 2 or denom <= 0:
        return 0.0
    return _N.cdf((sr - benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def deflated_sharpe(
    returns: np.ndarray, n_trials: int, trial_sharpes: list[float]
) -> dict[str, float]:
    """DSR of `returns` after `n_trials` trials.

    The variance of Sharpe ratios across trials comes from the recorded
    trials when at least two have one; otherwise from the null approximation
    1/(T-1) (variance of a Sharpe estimate when the true Sharpe is 0).
    """
    t = len(returns)
    sd = float(np.std(returns, ddof=1)) if t > 1 else 0.0
    sr = float(np.mean(returns)) / sd if sd > 0 else 0.0
    skew, kurt = moments(returns)
    if len(trial_sharpes) >= 2:
        var = float(np.var(trial_sharpes, ddof=1))
        source = 1.0
    else:
        var = 1.0 / max(t - 1, 1)
        source = 0.0
    sr0 = expected_max_sharpe(n_trials, var)
    return {
        "dsr": probabilistic_sharpe(sr, sr0, t, skew, kurt),
        "sharpe_per_bar": sr,
        "expected_max_sharpe": sr0,
        "sharpe_variance": var,
        "variance_from_trials": source,
        "n_trials": float(n_trials),
        "skew": skew,
        "kurtosis": kurt,
    }
