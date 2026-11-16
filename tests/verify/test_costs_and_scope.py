"""Per-venue costs in the verifier, and OOS openings counted as trials."""

import json

from firm.core.domain import CheckStatus
from firm.verify.state import OosVault, TrialRegistry
from verify_helpers import run_verify


def test_report_uses_the_venues_real_fee_schedule(dataset, tmp_path) -> None:
    body = json.loads(
        run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover").report
    )
    costs = body["costs"]
    assert costs["fee_schedule"] == "kraken_pro_tier1"
    assert costs["cost_bps_per_unit_traded"] == 80 + 2 / 2 + 5  # taker + half spread + slip
    assert costs["fee_as_of"] and costs["fee_source"]


def test_first_opening_is_counted_as_a_trial(dataset, tmp_path) -> None:
    before = TrialRegistry(tmp_path).count()
    r = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    assert next(c for c in r.verdict.checks if c.name == "hidden_out_of_sample").status in (
        CheckStatus.PASSED,
        CheckStatus.FAILED,
    )
    trials = TrialRegistry(tmp_path).all()
    assert len(trials) == before + 2  # the in-sample run + the opening
    assert any(t.experiment_id == "oos_opening" for t in trials)
    scopes = {e.get("scope") for e in OosVault(tmp_path).events()}
    assert scopes == {"ma_crossover|BTC-USD|1d"}


def test_replay_does_not_count_its_own_opening(dataset, tmp_path) -> None:
    a = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    b = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    assert a.report == b.report  # its own opening must not raise its own bar
