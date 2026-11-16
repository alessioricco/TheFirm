"""Trial count: every trial behind the candidate must be recorded, and the
in-sample Sharpe must survive the deflated-Sharpe correction for the global
number of trials (firm rule 5)."""

from __future__ import annotations

from firm.core.domain import CheckResult, CheckStatus
from firm.verify.context import VerifyContext
from firm.verify.stats import deflated_sharpe

NAME = "trial_count"


def run(ctx: VerifyContext) -> CheckResult:
    th = ctx.config.trials
    known = ctx.registry.ids()
    missing = [t for t in ctx.trial_ids if t not in known]
    if not ctx.trial_ids or missing:
        return CheckResult(
            name=NAME,
            status=CheckStatus.FAILED,
            detail=f"unrecorded trials: {missing or 'candidate lists none'}",
        )
    n_trials = max(1, ctx.registry.count())
    r = ctx.in_sample_backtest().net[1 : ctx.oos_start]
    m = deflated_sharpe(r, n_trials, ctx.registry.sharpes())
    ok = m["dsr"] >= th.min_dsr
    return CheckResult(
        name=NAME,
        status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
        metrics=m,
        detail=f"deflated Sharpe {m['dsr']:.4f} after {n_trials} trials (min {th.min_dsr})",
    )
