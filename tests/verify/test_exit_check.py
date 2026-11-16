"""Milestone 1 exit check, one test per condition (SPEC "Milestones")."""

import json

from firm.core.domain import CheckStatus, VerdictOutcome
from verify_helpers import run_verify


def _check(result, name):
    return next(c for c in result.verdict.checks if c.name == name)


def test_planted_lookahead_is_rejected_by_truncation_check(dataset, tmp_path) -> None:
    r = run_verify(dataset, tmp_path, "lookahead_ma.py", "LookaheadCrossover")
    assert _check(r, "lookahead_truncation").status is CheckStatus.FAILED
    assert r.verdict.outcome is VerdictOutcome.FAIL and r.exit_code == 1
    # its out-of-sample period was never opened
    assert _check(r, "hidden_out_of_sample").status is CheckStatus.NOT_RUN


def test_clean_moving_average_crossover_passes(dataset, tmp_path) -> None:
    r = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    assert {c.name: c.status for c in r.verdict.checks} == {
        "lookahead_truncation": CheckStatus.PASSED,
        "cost_realism": CheckStatus.PASSED,
        "parameter_sensitivity": CheckStatus.PASSED,
        "trial_count": CheckStatus.PASSED,
        "hidden_out_of_sample": CheckStatus.PASSED,
    }
    assert r.verdict.outcome is VerdictOutcome.PASS and r.exit_code == 0


def test_same_dataset_and_strategy_give_byte_identical_reports(dataset, tmp_path) -> None:
    a = run_verify(dataset, tmp_path / "s1", "ma_crossover.py", "MovingAverageCrossover")
    b = run_verify(dataset, tmp_path / "s1", "ma_crossover.py", "MovingAverageCrossover")  # replay
    c = run_verify(dataset, tmp_path / "s2", "ma_crossover.py", "MovingAverageCrossover")  # fresh
    assert a.report == b.report == c.report
    body = json.loads(a.report)
    assert body["verdict_id"] == a.verdict.verdict_id
    assert "issued_at" not in a.report.decode() and "2026-11-16" not in a.report.decode()


def test_report_identical_after_rebuilding_dataset_from_captures(tmp_path) -> None:
    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars
    from firm.data.synthetic import seed_store

    reports = []
    for i in (1, 2):
        root = tmp_path / f"store{i}"
        seed_store(root, n=2000, seed=7)
        version = build_dataset(
            root, normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
        ).dataset_version
        reports.append(
            run_verify(
                (root, version), tmp_path / f"state{i}", "ma_crossover.py", "MovingAverageCrossover"
            ).report
        )
    assert reports[0] == reports[1]
