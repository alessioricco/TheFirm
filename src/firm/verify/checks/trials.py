"""Trial count: every trial behind the candidate must be recorded, and the
in-sample Sharpe must survive the deflated-Sharpe correction for the global
number of trials (firm rule 5). Trial Sharpes are put on the candidate's bar
frequency before their variance is taken (DECISIONS 2026-11-19)."""

from __future__ import annotations

from firm.core.domain import CheckResult, CheckStatus
from firm.verify.context import VerifyContext
from firm.verify.state import opening_trial_id
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
    # The candidate's own OOS opening (if any, from an earlier run) is not a
    # trial that could have influenced its selection: leaving it out keeps a
    # replay's report identical to the first run's.
    own_opening = opening_trial_id(ctx.scope, ctx.candidate_key)
    n_trials = max(1, len(known - {own_opening}))
    r = ctx.in_sample_backtest().net[1 : ctx.oos_start]
    # every trial's Sharpe on the candidate's bar frequency (a daily trial's
    # per-bar Sharpe is not comparable with an hourly one's)
    sharpes, unscaled = ctx.registry.scaled_sharpes(ctx.periods_per_year, ctx.trial_periods)
    m = deflated_sharpe(r, n_trials, sharpes)
    m["scale_periods_per_year"] = ctx.periods_per_year
    m["trial_sharpes_unscaled"] = float(unscaled)
    ok = m["dsr"] >= th.min_dsr
    return CheckResult(
        name=NAME,
        status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
        metrics=m,
        detail=f"deflated Sharpe {m['dsr']:.4f} after {n_trials} trials (min {th.min_dsr})"
        + (f"; {unscaled} trial Sharpe(s) of unknown timeframe left out" if unscaled else ""),
    )
