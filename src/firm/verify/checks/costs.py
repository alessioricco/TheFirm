"""Cost realism: the in-sample result must survive the market profile's costs,
and must not lose money with costs multiplied by the stress factor."""

from __future__ import annotations

from firm.core.domain import CheckResult, CheckStatus
from firm.verify.backtest import metrics
from firm.verify.context import VerifyContext

NAME = "cost_realism"


def run(ctx: VerifyContext) -> CheckResult:
    th = ctx.config.costs
    n, ppy = ctx.oos_start, ctx.periods_per_year
    gross = metrics(ctx.in_sample_backtest(cost_multiplier=0.0), 0, n, ppy)["sharpe"]
    net = metrics(ctx.in_sample_backtest(), 0, n, ppy)
    stressed = metrics(ctx.in_sample_backtest(cost_multiplier=th.stress_multiplier), 0, n, ppy)
    m = {
        "gross_sharpe": gross,
        "net_sharpe": net["sharpe"],
        "stressed_net_sharpe": stressed["sharpe"],
        "cost_bps": ctx.cost_bps,
        "turnover": net["turnover"],
    }
    ok = net["sharpe"] >= th.min_net_sharpe and stressed["sharpe"] >= th.stress_min_net_sharpe
    detail = (
        f"net Sharpe {net['sharpe']:.3f} (min {th.min_net_sharpe}), at {th.stress_multiplier}x "
        f"costs {stressed['sharpe']:.3f} (min {th.stress_min_net_sharpe}); gross {gross:.3f}"
    )
    return CheckResult(
        name=NAME, status=CheckStatus.PASSED if ok else CheckStatus.FAILED, detail=detail, metrics=m
    )
