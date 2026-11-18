"""The gate between a verifier report and paper trading.

`verdict_for_deployment` turns a stored report into the `Verdict` a
`Deployment` needs, and refuses unless all of these hold:

- the report is intact: its verdict ID is the hash of its own content;
- the outcome is pass (unverified is never a pass);
- it is for this exact strategy code (sha256 of the file) and parameters;
- it is for this venue, instrument and timeframe;
- its data is real: every row of the dataset traces to a capture made by
  `api` (downloaded or streamed). Synthetic or untraceable data can test
  code, never promote a strategy (owner rule, TODO "Strategies on real data").
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from firm.core.domain import CheckResult, Verdict, VerdictOutcome


class DeploymentRefused(ValueError):
    """The report can't put this strategy on paper."""


def position_fraction(report: bytes) -> Decimal:
    """The share of equity a target of 1 held when the verifier judged the
    strategy; paper trades it at that size. Reports from before sizing was
    recorded (2026-11-18) were judged fully invested."""
    sizing = json.loads(report).get("sizing") or {}
    return Decimal(str(sizing.get("position_pct", 100))) / 100


def verdict_for_deployment(
    report: bytes,
    *,
    code_sha256: str,
    params: Mapping[str, Any],
    venue: str,
    instrument: str,
    tf: str,
    capture_methods: Mapping[str, int],
    issued_at: datetime,
) -> Verdict:
    body = json.loads(report)
    claimed = body.pop("verdict_id", None)
    actual = "v-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    if claimed != actual:
        raise DeploymentRefused("report altered: its content doesn't hash to its verdict ID")
    if body["outcome"] != VerdictOutcome.PASS.value:
        raise DeploymentRefused(f"verdict {claimed} is {body['outcome']}, not pass")
    cand, data = body["candidate"], body["data"]
    if cand["code_sha256"] != code_sha256:
        raise DeploymentRefused("the strategy file changed since it was verified")
    if dict(cand["parameters"]) != dict(params):
        raise DeploymentRefused(f"parameters differ from the verified ones ({cand['parameters']})")
    if (data["venue"], tuple(data["instruments"]), data["timeframe"]) != (
        venue,
        (instrument,),
        tf,
    ):
        raise DeploymentRefused(
            f"verified on {data['venue']} {data['instruments']} {data['timeframe']}, "
            f"not {venue} {instrument} {tf}"
        )
    not_real = {m: n for m, n in capture_methods.items() if m != "api"}
    if not capture_methods or not_real:
        raise DeploymentRefused(
            f"dataset {data['dataset_version']} is not all real data: {dict(capture_methods)}"
        )
    checks = tuple(CheckResult.model_validate(c) for c in body["checks"])
    summary = "; ".join(f"{c.name}={c.status.value}" for c in checks)
    return Verdict(
        verdict_id=actual,
        candidate_id=cand["candidate_id"],
        strategy_id=cand["strategy_id"],
        dataset_version=data["dataset_version"],
        outcome=VerdictOutcome.PASS,
        checks=checks,
        report=f"pass: {summary}",
        issued_at=issued_at,
    )
