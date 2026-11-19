"""Lookahead truncation.

Run the strategy on the in-sample data, then again with the data cut at each
of N decision times. A causal strategy produces exactly the same signals up
to the cut; any difference means some signal used data from after its own
bar, and the strategy fails.
"""

from __future__ import annotations

import numpy as np

from firm.core.domain import CheckResult, CheckStatus
from firm.sandbox.client import SandboxError
from firm.verify.context import VerifyContext

NAME = "lookahead_truncation"


def decision_points(n: int, k: int) -> list[int]:
    """Up to k cut indices, evenly spaced from 10% of the data to the last bar."""
    first = max(1, n // 10)
    return sorted({round(float(x)) for x in np.linspace(first, n - 1, num=min(k, n - first))})


def run(ctx: VerifyContext) -> CheckResult:
    bars = ctx.in_sample
    full = ctx.signals(bars).to_numpy(dtype=float, na_value=np.nan)
    tol = ctx.config.lookahead.tolerance
    points = decision_points(len(bars), ctx.config.lookahead.truncation_points)
    worst, first_bad, bad = 0.0, None, 0
    for t in points:
        try:
            cut = ctx.signals(bars.iloc[: t + 1]).to_numpy(dtype=float, na_value=np.nan)
        except SandboxError:
            raise  # the sandbox failed, not the strategy: the verifier makes it UNVERIFIED
        except Exception as exc:  # a strategy that can't run on less data is not causal-safe
            return CheckResult(
                name=NAME,
                status=CheckStatus.FAILED,
                detail=f"strategy failed on data cut at bar {t}: {exc!r}",
            )
        ref = full[: t + 1]
        nan_mismatch = np.isnan(cut) != np.isnan(ref)
        diff = np.where(np.isnan(cut) | np.isnan(ref), 0.0, np.abs(cut - ref))
        d = float(diff.max()) if len(diff) else 0.0
        if nan_mismatch.any():
            d = max(d, float("inf"))
        if d > tol:
            bad += 1
            if first_bad is None:
                first_bad = t
        worst = max(worst, d)
    metrics = {
        "decision_points": float(len(points)),
        "violations": float(bad),
        "max_abs_diff": worst if np.isfinite(worst) else 1e308,
    }
    if bad:
        when = bars["close_time"].iloc[first_bad].isoformat()
        return CheckResult(
            name=NAME,
            status=CheckStatus.FAILED,
            metrics=metrics,
            detail=f"signals change when future data is removed: {bad} of "
            f"{len(points)} cuts, first at bar {first_bad} ({when})",
        )
    return CheckResult(
        name=NAME,
        status=CheckStatus.PASSED,
        metrics=metrics,
        detail=f"signals identical with data cut at {len(points)} decision times",
    )
