"""Parameter sensitivity: nudge every numeric parameter up and down; the
in-sample net Sharpe must not collapse anywhere. A perturbation the strategy
rejects counts as a collapse (we can't show it is robust there)."""

from __future__ import annotations

from collections.abc import Mapping

from firm.core.domain import CheckResult, CheckStatus
from firm.verify.backtest import metrics
from firm.verify.context import VerifyContext
from firm.verify.strategy import ParamValue

NAME = "parameter_sensitivity"


def perturbations(
    params: Mapping[str, ParamValue], steps: tuple[float, ...]
) -> list[tuple[str, float, dict[str, ParamValue]]]:
    out = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        for step in steps:
            new: ParamValue
            if isinstance(value, int):
                new = round(value * (1 + step))
                if new == value:
                    new = value + (1 if step > 0 else -1)
            else:
                new = value * (1 + step)
            out.append((key, step, dict(params) | {key: new}))
    return out


def run(ctx: VerifyContext) -> CheckResult:
    th = ctx.config.sensitivity
    n, ppy = ctx.oos_start, ctx.periods_per_year
    base = metrics(ctx.in_sample_backtest(), 0, n, ppy)["sharpe"]
    cases = perturbations(ctx.params, th.relative_steps)
    if not cases:
        return CheckResult(
            name=NAME, status=CheckStatus.NOT_RUN, detail="no numeric parameters to perturb"
        )
    worst, failures = float("inf"), []
    for key, step, params in cases:
        try:
            s = metrics(ctx.in_sample_backtest(params), 0, n, ppy)["sharpe"]
        except Exception as exc:
            failures.append(f"{key}{step:+.0%}: rejected ({type(exc).__name__})")
            worst = float("-inf")
            continue
        worst = min(worst, s)
        if base <= 0 or s < th.min_ratio_to_base * base:
            failures.append(f"{key}{step:+.0%}: Sharpe {s:.3f}")
    m = {
        "base_sharpe": base,
        "worst_sharpe": worst if worst != float("-inf") else -1e308,
        "perturbations": float(len(cases)),
        "failures": float(len(failures)),
    }
    if failures:
        return CheckResult(
            name=NAME,
            status=CheckStatus.FAILED,
            metrics=m,
            detail=f"collapse vs base {base:.3f} (min ratio {th.min_ratio_to_base}): "
            + "; ".join(failures[:6]),
        )
    return CheckResult(
        name=NAME,
        status=CheckStatus.PASSED,
        metrics=m,
        detail=f"{len(cases)} perturbations, worst Sharpe {worst:.3f} vs base {base:.3f}",
    )
