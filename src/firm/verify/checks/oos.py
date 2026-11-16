"""Hidden out-of-sample: the final period, opened once per strategy,
instrument and timeframe through the vault (firm rule 4); each first opening
is recorded as a trial. Signals are computed on all data (the tail needs
in-sample history for warm-up), metrics only over the hidden bars."""

from __future__ import annotations

import hashlib
import json

from firm.core.domain import CheckResult, CheckStatus, TrialRecord
from firm.verify.backtest import metrics, run_backtest
from firm.verify.context import VerifyContext
from firm.verify.state import opening_trial_id

NAME = "hidden_out_of_sample"


def run(ctx: VerifyContext, in_sample_sharpe: float) -> CheckResult:
    th = ctx.config.oos
    window = [
        ctx.full["open_time"].iloc[ctx.oos_start].isoformat(),
        ctx.full["close_time"].iloc[-1].isoformat(),
    ]
    opening = ctx.vault.request(
        strategy_id=ctx.strategy_id,
        scope=ctx.scope,
        candidate_key=ctx.candidate_key,
        config_key=ctx.config_key,
        dataset_version=ctx.dataset_version,
        window=window,
        at=ctx.now,
    )
    if opening.access == "refused":
        return CheckResult(
            name=NAME,
            status=CheckStatus.FAILED,
            detail=f"out-of-sample already opened for another version of "
            f"{ctx.strategy_id} in scope {ctx.scope}; it can't be reused",
        )
    result = run_backtest(ctx.full, ctx.signals(ctx.full), cost_bps=ctx.cost_bps)
    m = metrics(result, ctx.oos_start, len(ctx.full), ctx.periods_per_year)
    digest = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
    if opening.access == "replay" and opening.sealed_hash is None:
        # unchanged strategy, new verifier config (costs/thresholds): seal it too
        ctx.vault.seal(
            strategy_id=ctx.strategy_id,
            scope=ctx.scope,
            candidate_key=ctx.candidate_key,
            config_key=ctx.config_key,
            result_sha256=digest,
            at=ctx.now,
        )
    if opening.access == "first_opening":
        ctx.registry.record(
            TrialRecord(
                trial_id=opening_trial_id(ctx.scope, ctx.candidate_key),
                experiment_id="oos_opening",
                strategy_id=ctx.strategy_id,
                parameters=dict(ctx.params),
                metrics={},
                dataset_version=ctx.dataset_version,
                recorded_at=ctx.now,
            )
        )
        ctx.vault.seal(
            strategy_id=ctx.strategy_id,
            scope=ctx.scope,
            candidate_key=ctx.candidate_key,
            config_key=ctx.config_key,
            result_sha256=digest,
            at=ctx.now,
        )
    elif opening.sealed_hash is not None and opening.sealed_hash != digest:
        return CheckResult(
            name=NAME,
            status=CheckStatus.FAILED,
            metrics=m,
            detail="replay does not reproduce the sealed out-of-sample result",
        )
    ratio_ok = in_sample_sharpe <= 0 or m["sharpe"] >= th.min_ratio_to_in_sample * in_sample_sharpe
    ok = m["sharpe"] >= th.min_sharpe and ratio_ok
    return CheckResult(
        name=NAME,
        status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
        metrics=m,
        detail=(
            f"OOS Sharpe {m['sharpe']:.3f} over {int(m['bars'])} bars (min {th.min_sharpe}, "
            f">= {th.min_ratio_to_in_sample} x in-sample {in_sample_sharpe:.3f})"
        ),
    )
