"""Wire a strategy file, a dataset version and the config into one verification.

Used by `firm verify` and by the tests. A hand-written strategy's in-sample
run is recorded as one trial (idempotent: the same code, params, dataset and
window are the same trial, so re-verifying doesn't inflate the count).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from firm.core.config import (
    DataQualityConfig,
    load_config,
    load_data_quality,
    load_fee_schedule,
    load_verifier_config,
)
from firm.core.domain import StrategyCandidate, StrategySpec, TrialRecord
from firm.core.events import NULL_SINK, EventSink
from firm.data.dataset import open_dataset
from firm.data.quality import implausible_bars
from firm.verify.backtest import metrics, run_backtest
from firm.verify.state import OosVault, TrialRegistry, oos_scope
from firm.verify.strategy import load_strategy
from firm.verify.verifier import (
    VerificationResult,
    bars_fingerprint,
    candidate_key,
    cost_bps,
    legacy_candidate_key,
    split_in_sample,
    verify,
)

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
    sink: EventSink = NULL_SINK,
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

    registry = TrialRegistry(state, sink=sink)
    # A trial is identified by code, params and the bars it read (content), so
    # re-processing the same bars into a new dataset version is not a new trial.
    # A trial recorded before 2026-11-16 (keyed by dataset version + window) is reused.
    name, code = loaded.strategy.name, loaded.code_sha256
    legacy_trial = "t-" + _h([name, code, p, dataset_version, window])[:16]
    trial_id = "t-" + _h([name, code, p, bars_fingerprint(bars)])[:16]
    if legacy_trial in registry.raw_ids():
        registry.alias(legacy_trial, trial_id)  # same trial, now keyed by content
    # Data quality first: on implausible bars nothing is judged and no trial is counted.
    rule = load_data_quality(config_root)
    issues = [
        f"{q.as_dict()['open_time']}: {q.reason}"
        for q in implausible_bars(bars.to_dict("records"), rule, time_key="open_time")
    ]
    # The same candidate on cleaned bars (see quarantine_equivalents) is the same
    # experiment: its trial is linked to the opened one's, not counted again.
    equivalents = (
        quarantine_equivalents(
            OosVault(state),
            strategy_id=name,
            scope=oos_scope(name, (instrument,), tf),
            code_sha256=code,
            params=p,
            bars=bars,
            data_root=data_root,
            venue=venue,
            instrument=instrument,
            tf=tf,
            rule=rule,
        )
        if not issues
        else {}
    )
    for eq in equivalents.values():
        for old_trial in (
            "t-" + _h([name, code, p, eq.fingerprint])[:16],
            "t-" + _h([name, code, p, eq.dataset_version, eq.window])[:16],
        ):
            if old_trial in registry.raw_ids():
                registry.alias(old_trial, trial_id)
    quarantined = sum(
        len(f.quarantined)
        for f in ds.manifest.files
        if (f.kind, f.venue, f.instrument, f.tf, f.derived_from)
        == ("bars", venue, instrument, tf, None)
    )
    split = split_in_sample(bars, vcfg.oos.fraction)
    in_sample = bars.iloc[:split].reset_index(drop=True)
    is_metrics = (
        metrics(
            run_backtest(
                in_sample,
                loaded.strategy.signals(in_sample.copy(), p),
                cost_bps=cost_bps(profile, fees),
                position=vcfg.sizing.position_pct / 100,
            ),
            0,
            split,
            1.0,
        )
        if split > 1 and not issues
        else {}
    )
    if not issues:
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
        vault=OosVault(state, sink=sink),
        now=now,
        sink=sink,
        data_issues=issues,
        quality_rule=rule.rule_id,
        quarantined=quarantined,
        equivalent_keys={k: eq.reason for k, eq in equivalents.items()},
    )


@dataclass(frozen=True)
class Equivalent:
    """An opened candidate proved equal to this one but for quarantined bars."""

    reason: str
    fingerprint: str  # of the bars the opening saw
    dataset_version: str
    window: list[str]


def quarantine_equivalents(
    vault: OosVault,
    *,
    strategy_id: str,
    scope: str,
    code_sha256: str,
    params: Mapping[str, Any],
    bars: pd.DataFrame,
    data_root: Path,
    venue: str,
    instrument: str,
    tf: str,
    rule: DataQualityConfig,
) -> dict[str, Equivalent]:
    """Opened candidates in `scope` that differ from this one only by quarantined bars.

    For each first opening, the bars it saw are rebuilt from the dataset it
    cited, up to the end of its hidden period; they must hash to the opened
    key (else nothing is proved). Dropping the bars `rule` finds implausible
    must then give exactly `bars`, the bars this run reads. Same code and
    parameters, the same hidden period, minus bars nobody should trust: the
    same candidate, so the vault may link it instead of refusing (firm rule 4
    still refuses everything else)."""
    events = vault.events()
    aliases = {e["from"]: e["to"] for e in events if e["event"] == "alias"}
    target = bars_fingerprint(bars)
    out: dict[str, Equivalent] = {}
    for e in events:
        if (
            e["event"] != "first_opening"
            or e["strategy_id"] != strategy_id
            or e.get("scope", scope) != scope
        ):
            continue
        opened = e["candidate_key"]
        try:
            old = open_dataset(data_root, e["dataset_version"]).bars(
                venue, instrument, tf, as_of=datetime.fromisoformat(e["window"][1])
            )
        except (FileNotFoundError, LookupError, KeyError, ValueError):
            continue  # the opening's data is gone or unreadable: can't prove anything
        if old.empty:
            continue
        old_window = [old["open_time"].iloc[0].isoformat(), old["close_time"].iloc[-1].isoformat()]
        seen = {
            candidate_key(code_sha256, params, bars_fingerprint(old)),
            legacy_candidate_key(code_sha256, params, e["dataset_version"], old_window),
        }
        if opened not in seen and aliases.get(opened) not in seen:
            continue
        bad = implausible_bars(old.to_dict("records"), rule, time_key="open_time")
        if not bad:
            continue
        drop = {pd.Timestamp(q.open_time_ns, unit="ns", tz="UTC") for q in bad}
        cleaned = old[~old["open_time"].isin(drop)].reset_index(drop=True)
        if bars_fingerprint(cleaned) == target:
            when = ", ".join(q.as_dict()["open_time"][:10] for q in bad)
            out[opened] = Equivalent(
                reason=f"same code and parameters; the opened bars ({e['dataset_version']}) "
                f"minus {len(bad)} bar(s) quarantined under rule {rule.rule_id} ({when}) are "
                "exactly the bars read now",
                fingerprint=bars_fingerprint(old),
                dataset_version=e["dataset_version"],
                window=old_window,
            )
    return out
