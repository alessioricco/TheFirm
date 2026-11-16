"""Wire a strategy file, a dataset version and the config into one verification.

Used by `firm verify` and by the tests. A hand-written strategy's in-sample
run is recorded as one trial (idempotent: the same code, params, dataset and
window are the same trial, so re-verifying doesn't inflate the count).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firm.core.config import (
    load_config,
    load_fee_schedule,
    load_verifier_config,
)
from firm.core.domain import StrategyCandidate, StrategySpec, TrialRecord
from firm.data.dataset import open_dataset
from firm.verify.backtest import metrics, run_backtest
from firm.verify.state import OosVault, TrialRegistry
from firm.verify.strategy import load_strategy
from firm.verify.verifier import VerificationResult, cost_bps, split_in_sample, verify

FAR_FUTURE = datetime(2262, 1, 1, tzinfo=UTC)  # within pandas' timestamp range


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def verify_strategy_file(
    *,
    strategy_path: Path,
    class_name: str,
    params: Mapping[str, Any] | None,
    data_root: Path,
    dataset_version: str,
    venue: str,
    instrument: str,
    tf: str,
    config_root: Path,
    jurisdiction: str,
    market: str,
    state: Path,
    now: datetime,
    as_of: datetime | None = None,
    start: datetime | None = None,
    verifier_name: str = "default",
) -> VerificationResult:
    loaded = load_strategy(strategy_path, class_name)
    p = dict(loaded.strategy.default_params) | dict(params or {})
    cfg = load_config(config_root, jurisdiction, [market])
    profile = cfg.markets[market]
    fees = load_fee_schedule(config_root, profile.costs.fee_schedule_for(venue))
    vcfg = load_verifier_config(config_root, verifier_name)
    vcfg_sha = hashlib.sha256(
        (config_root / "verify" / f"{verifier_name}.yaml").read_bytes()
    ).hexdigest()

    ds = open_dataset(data_root, dataset_version)
    bars = ds.bars(venue, instrument, tf, as_of=as_of or FAR_FUTURE, start=start)
    if bars.empty:
        raise LookupError(f"no bars for {venue}:{instrument} {tf} in {dataset_version}")
    window = [bars["open_time"].iloc[0].isoformat(), bars["close_time"].iloc[-1].isoformat()]

    registry = TrialRegistry(state)
    trial_id = (
        "t-" + _h([loaded.strategy.name, loaded.code_sha256, p, dataset_version, window])[:16]
    )
    split = split_in_sample(bars, vcfg.oos.fraction)
    in_sample = bars.iloc[:split].reset_index(drop=True)
    is_metrics = (
        metrics(
            run_backtest(
                in_sample,
                loaded.strategy.signals(in_sample.copy(), p),
                cost_bps=cost_bps(profile, fees),
            ),
            0,
            split,
            1.0,
        )
        if split > 1
        else {}
    )
    registry.record(
        TrialRecord(
            trial_id=trial_id,
            experiment_id="manual",
            strategy_id=loaded.strategy.name,
            parameters=p,
            metrics={k: v for k, v in is_metrics.items() if k == "sharpe_per_bar"},
            dataset_version=dataset_version,
            recorded_at=now,
        )
    )

    candidate = StrategyCandidate(
        candidate_id="c-" + _h([trial_id])[:16],
        strategy_id=loaded.strategy.name,
        version=loaded.code_sha256[:12],
        code_path=strategy_path.name,
        code_sha256=loaded.code_sha256,
        spec=StrategySpec(
            hypothesis=f"hand-written {loaded.strategy.name}",
            market_profiles=(market,),
            instruments=(instrument,),
            timeframe=tf,
            parameters=p,
        ),
        trial_ids=(trial_id,),
        created_at=now,
    )
    return verify(
        candidate=candidate,
        strategy=loaded.strategy,
        bars=bars,
        dataset_version=dataset_version,
        venue=venue,
        market=profile,
        fees=fees,
        config=vcfg,
        config_sha256=vcfg_sha,
        registry=registry,
        vault=OosVault(state),
        now=now,
    )
