"""The gate from verifier report to paper: intact, passing, same code and params,
same market, and real data only."""

from __future__ import annotations

import hashlib
import json

import pytest

from firm.data.dataset import capture_methods
from firm.execution.deploy import DeploymentRefused, verdict_for_deployment
from verify_helpers import EXAMPLES, NOW, run_verify

REAL = {"api": 2000}


@pytest.fixture(scope="module")
def passing(dataset, tmp_path_factory):
    result = run_verify(
        dataset, tmp_path_factory.mktemp("state"), "ma_crossover.py", "MovingAverageCrossover"
    )
    assert result.verdict.outcome.value == "pass"  # the M1 exit check's clean strategy
    sha = hashlib.sha256((EXAMPLES / "ma_crossover.py").read_bytes()).hexdigest()
    return result.report, sha, json.loads(result.report)["candidate"]["parameters"]


def gate(report, sha, params, **kw):
    fields = dict(
        code_sha256=sha,
        params=params,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        capture_methods=REAL,
        issued_at=NOW,
    )
    fields.update(kw)
    return verdict_for_deployment(report, **fields)


def test_a_passing_report_on_real_data_deploys(passing) -> None:
    v = gate(*passing)
    assert v.outcome.value == "pass" and v.verdict_id == json.loads(passing[0])["verdict_id"]


def test_synthetic_data_never_promotes_a_strategy(passing, dataset) -> None:
    root, version = dataset
    methods = capture_methods(root, version)
    assert methods == {"synthetic": 2000}
    with pytest.raises(DeploymentRefused, match="not all real data"):
        gate(*passing, capture_methods=methods)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda r, s, p: (r.replace(b'"pass"', b'"PASS"', 1), s, p), "altered"),
        (lambda r, s, p: (r, "0" * 64, p), "strategy file changed"),
        (lambda r, s, p: (r, s, {**p, "fast": 11}), "parameters differ"),
    ],
)
def test_tampering_or_drift_is_refused(passing, change, why) -> None:
    with pytest.raises(DeploymentRefused, match=why):
        gate(*change(*passing))


def test_another_market_is_refused(passing) -> None:
    with pytest.raises(DeploymentRefused, match="verified on kraken"):
        gate(*passing, venue="coinbase")
    with pytest.raises(DeploymentRefused, match="verified on kraken"):
        gate(*passing, tf="1h")


def test_a_failing_verdict_is_refused(dataset, tmp_path) -> None:
    result = run_verify(dataset, tmp_path, "lookahead_ma.py", "LookaheadCrossover")
    sha = hashlib.sha256((EXAMPLES / "lookahead_ma.py").read_bytes()).hexdigest()
    params = json.loads(result.report)["candidate"]["parameters"]
    with pytest.raises(DeploymentRefused, match="not pass"):
        gate(result.report, sha, params)
