"""The verification desk: candidate + dataset version -> `Verdict` + report.

Order: in-sample checks first (lookahead, costs, sensitivity, trial count).
The hidden out-of-sample period is opened only if they all pass, so a
strategy can't spend its one opening on a version that is already rejected.

The report is deterministic: no wall-clock time, sorted keys, fixed float
rounding. Same dataset version + same candidate + same state -> same bytes.
The verdict ID is the hash of the report body, and `issued_at` lives only on
the `Verdict` object (which goes to the ledger), never in the report.

Exit codes: 0 pass, 1 fail, 2 unverified.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from firm import __version__
from firm.core.config import FeeSchedule, MarketProfile, VerifierConfig
from firm.core.domain import (
    CheckResult,
    CheckStatus,
    LedgerEventType,
    StrategyCandidate,
    TrialRecord,
    Verdict,
    VerdictOutcome,
)
from firm.core.events import NULL_SINK, EventSink
from firm.core.timeframes import periods_per_year
from firm.verify.backtest import metrics
from firm.verify.checks import costs, lookahead, oos, sensitivity, trials
from firm.verify.context import VerifyContext
from firm.verify.state import OosVault, TrialRegistry, oos_scope
from firm.verify.strategy import Strategy

EXIT_CODES = {VerdictOutcome.PASS: 0, VerdictOutcome.FAIL: 1, VerdictOutcome.UNVERIFIED: 2}
IN_SAMPLE_CHECKS = (lookahead, costs, sensitivity, trials)


def cost_bps(market: MarketProfile, fees: FeeSchedule) -> float:
    """Cost per unit of exposure traded: taker fee + half the spread + slippage."""
    return fees.taker_bps + market.costs.spread_bps / 2 + market.costs.slippage_bps


def split_in_sample(bars: pd.DataFrame, fraction: float) -> int:
    """Index of the first hidden bar: the last `fraction` of the window is held back."""
    return len(bars) - round(len(bars) * fraction)


def bars_fingerprint(bars: pd.DataFrame) -> str:
    """SHA-256 of exactly the bars a verification read: times and OHLCV, fixed byte order.

    Two dataset versions holding the same bars (e.g. after re-normalising) give
    the same fingerprint; any changed, added or removed bar changes it."""
    h = hashlib.sha256(f"bars:{len(bars)}".encode())
    for col in ("open_time", "close_time"):
        h.update(np.ascontiguousarray(pd.DatetimeIndex(bars[col]).asi8, dtype="<i8").tobytes())
    for col in ("open", "high", "low", "close", "volume"):
        h.update(np.ascontiguousarray(bars[col].to_numpy(dtype=float), dtype="<f8").tobytes())
    return h.hexdigest()


def candidate_key(code_sha256: str, params: Mapping[str, Any], fingerprint: str) -> str:
    """What makes a candidate *this* candidate: its code, parameters and the bars it saw."""
    blob = json.dumps(
        {"code": code_sha256, "params": dict(params), "bars": fingerprint}, sort_keys=True
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def legacy_candidate_key(
    code_sha256: str, params: Mapping[str, Any], dataset_version: str, window: list[str]
) -> str:
    """The key used before 2026-11-16 (dataset version + window), to recognise old openings."""
    blob = json.dumps(
        {"code": code_sha256, "params": dict(params), "dataset": dataset_version, "window": window},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _round(x: Any) -> Any:
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        return float(f"{x:.10g}")
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_round(v) for v in x]
    return x


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    report: bytes  # canonical JSON, byte-reproducible

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict.outcome]


def verify(
    *,
    candidate: StrategyCandidate,
    strategy: Strategy,
    bars: pd.DataFrame,
    dataset_version: str,
    venue: str,
    market: MarketProfile,
    fees: FeeSchedule,
    config: VerifierConfig,
    config_sha256: str,
    registry: TrialRegistry,
    vault: OosVault,
    now: datetime,
    sink: EventSink = NULL_SINK,
    data_issues: Sequence[str] = (),
    quality_rule: str | None = None,
    quarantined: int = 0,
    equivalent_keys: Mapping[str, str] | None = None,
    trial_periods: Callable[[TrialRecord], float | None] | None = None,
) -> VerificationResult:
    """`bars` are the whole evaluation window from the dataset (as_of applied)."""
    params: Mapping[str, Any] = candidate.spec.parameters
    split = split_in_sample(bars, config.oos.fraction)
    window = [bars["open_time"].iloc[0].isoformat(), bars["close_time"].iloc[-1].isoformat()]
    fingerprint = bars_fingerprint(bars)
    ctx = VerifyContext(
        strategy=strategy,
        params=params,
        strategy_id=candidate.strategy_id,
        candidate_key=candidate_key(candidate.code_sha256, params, fingerprint),
        legacy_keys=(legacy_candidate_key(candidate.code_sha256, params, dataset_version, window),),
        scope=oos_scope(
            candidate.strategy_id, candidate.spec.instruments, candidate.spec.timeframe
        ),
        config_key=hashlib.sha256(
            f"{cost_bps(market, fees)!r}|{config_sha256}".encode()
        ).hexdigest()[:16],
        trial_ids=candidate.trial_ids,
        dataset_version=dataset_version,
        in_sample=bars.iloc[:split].reset_index(drop=True),
        full=bars.reset_index(drop=True),
        cost_bps=cost_bps(market, fees),
        position=config.sizing.position_pct / 100,
        periods_per_year=periods_per_year(market.calendar, candidate.spec.timeframe),
        config=config,
        registry=registry,
        vault=vault,
        now=now,
        equivalent_keys=dict(equivalent_keys or {}),
        trial_periods=trial_periods or (lambda _t: None),
    )

    quality = CheckResult(
        name="data_quality",
        status=CheckStatus.NOT_RUN if data_issues else CheckStatus.PASSED,
        detail=(
            f"{len(data_issues)} implausible bar(s) in the window, e.g. {data_issues[0]}; "
            "nothing was judged and no trial was counted"
            if data_issues
            else f"no implausible bars; {quarantined} quarantined by the normaliser"
            f" (rule {quality_rule})"
        ),
        metrics={"implausible": float(len(data_issues)), "quarantined": float(quarantined)},
    )
    results: list[CheckResult] = [quality]
    if data_issues:
        why = "data quality: the window has implausible bars; fix the data first"
        for mod in (*IN_SAMPLE_CHECKS, oos):
            results.append(CheckResult(name=mod.NAME, status=CheckStatus.NOT_RUN, detail=why))
    elif split < config.min_bars:
        why = f"only {split} in-sample bars (min {config.min_bars}); nothing was judged"
        for mod in (*IN_SAMPLE_CHECKS, oos):
            results.append(CheckResult(name=mod.NAME, status=CheckStatus.NOT_RUN, detail=why))
    else:
        for mod in IN_SAMPLE_CHECKS:
            try:
                results.append(mod.run(ctx))
            except Exception as exc:
                results.append(
                    CheckResult(
                        name=mod.NAME, status=CheckStatus.FAILED, detail=f"check raised {exc!r}"
                    )
                )
        if all(r.status is CheckStatus.PASSED for r in results):
            is_sharpe = metrics(ctx.in_sample_backtest(), 0, split, ctx.periods_per_year)["sharpe"]
            try:
                results.append(oos.run(ctx, is_sharpe))
            except Exception as exc:
                results.append(
                    CheckResult(
                        name=oos.NAME, status=CheckStatus.FAILED, detail=f"check raised {exc!r}"
                    )
                )
        else:
            results.append(
                CheckResult(
                    name=oos.NAME,
                    status=CheckStatus.NOT_RUN,
                    detail="in-sample checks failed; out-of-sample kept closed",
                )
            )

    results = [
        CheckResult(name=r.name, status=r.status, detail=r.detail, metrics=_round(r.metrics))
        for r in results
    ]
    statuses = {r.status for r in results}
    outcome = (
        VerdictOutcome.FAIL
        if CheckStatus.FAILED in statuses
        else VerdictOutcome.UNVERIFIED
        if CheckStatus.NOT_RUN in statuses
        else VerdictOutcome.PASS
    )
    body: dict[str, Any] = {
        "firm_version": __version__,
        "verifier": {"name": config.verifier, "config_sha256": config_sha256},
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "strategy_id": candidate.strategy_id,
            "version": candidate.version,
            "code_sha256": candidate.code_sha256,
            "parameters": dict(params),
            "trial_ids": list(candidate.trial_ids),
        },
        "data": {
            "dataset_version": dataset_version,
            "venue": venue,
            "instruments": list(candidate.spec.instruments),
            "timeframe": candidate.spec.timeframe,
            "window": window,
            "bars_sha256": fingerprint,
            "hidden_from": bars["open_time"].iloc[split].isoformat() if split < len(bars) else None,
            "bars_in_sample": split,
            "bars_hidden": len(bars) - split,
            "quality_rule": quality_rule,
            "quarantined": quarantined,
        },
        "costs": _round(
            {
                "market": market.market,
                "fee_schedule": fees.fee_schedule,
                "fee_source": fees.source,
                "fee_as_of": fees.as_of.isoformat(),
                "cost_bps_per_unit_traded": ctx.cost_bps,
            }
        ),
        "sizing": {"position_pct": config.sizing.position_pct},
        "checks": [r.model_dump(mode="json") for r in results],
        "outcome": outcome.value,
    }
    verdict_id = "v-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    body["verdict_id"] = verdict_id
    report = (json.dumps(body, sort_keys=True, indent=1) + "\n").encode()
    summary = "; ".join(f"{r.name}={r.status.value}" for r in results)
    verdict = Verdict(
        verdict_id=verdict_id,
        candidate_id=candidate.candidate_id,
        strategy_id=candidate.strategy_id,
        dataset_version=dataset_version,
        outcome=outcome,
        checks=tuple(results),
        report=f"{outcome.value}: {summary}",
        issued_at=now,
    )
    emit_verdict(sink, json.loads(report))
    return VerificationResult(verdict=verdict, report=report)


def emit_verdict(sink: EventSink, body: dict[str, Any]) -> None:
    payload = verdict_event_payload(body)
    sink.emit(
        LedgerEventType.VERDICT,
        f"{payload['outcome']} for {payload['strategy_id']} on {payload['dataset_version']}",
        payload,
        event_id=f"verdict:{payload['verdict_id']}",
    )


def verdict_event_payload(body: dict[str, Any]) -> dict[str, Any]:
    """The ledger's record of a verdict: deterministic (no wall-clock time), so the
    same verdict emitted twice is one event. The full report stays in the state store;
    its hash ties the two together."""
    report_bytes = (json.dumps(body, sort_keys=True, indent=1) + "\n").encode()
    return {
        "verdict_id": body["verdict_id"],
        "outcome": body["outcome"],
        "strategy_id": body["candidate"]["strategy_id"],
        "candidate_id": body["candidate"]["candidate_id"],
        "code_sha256": body["candidate"]["code_sha256"],
        "dataset_version": body["data"]["dataset_version"],
        "bars_sha256": body["data"].get("bars_sha256"),
        "checks": {c["name"]: c["status"] for c in body["checks"]},
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
    }
