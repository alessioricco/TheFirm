"""Trial Sharpes on one scale (owner-approved fix, 2026-11-17).

The deflated-Sharpe benchmark takes the variance of Sharpe ratios across all
trials. They are per bar, so a daily trial's and an hourly trial's are not
comparable: every trial is rescaled to the candidate's bar frequency first
(per-bar Sharpe grows with the square root of the bar length). Trials record
their bars per year; older trials get it from their dataset version, and a
trial whose scale can't be known is left out of the variance (still counted
in the number of trials) and reported."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from firm.core.domain import TrialRecord
from firm.verify.state import TrialRegistry
from firm.verify.stats import rescale_sharpe
from firm.verify.trial_scale import periods_from_dataset
from verify_helpers import run_verify

NOW = datetime(2026, 11, 19, tzinfo=UTC)
DAY, FOUR_H, HOUR = 365.0, 2190.0, 8760.0


def trial(i: str, metrics: dict[str, float], ds: str = "ds-x") -> TrialRecord:
    return TrialRecord(
        trial_id=i,
        experiment_id="e",
        strategy_id="s",
        parameters={},
        metrics=metrics,
        dataset_version=ds,
        recorded_at=NOW,
    )


def test_rescaling_follows_the_square_root_of_bar_length() -> None:
    assert rescale_sharpe(0.1, DAY, HOUR) == pytest.approx(0.1 / math.sqrt(24))
    assert rescale_sharpe(0.02, HOUR, DAY) == pytest.approx(0.02 * math.sqrt(24))
    assert rescale_sharpe(0.05, DAY, DAY) == 0.05
    # annualised, both are the same strategy quality
    assert rescale_sharpe(0.1, DAY, HOUR) * math.sqrt(HOUR) == pytest.approx(0.1 * math.sqrt(DAY))


def test_registry_puts_every_trial_on_the_candidates_scale(tmp_path) -> None:
    reg = TrialRegistry(tmp_path)
    reg.record(trial("daily", {"sharpe_per_bar": 0.1, "periods_per_year": DAY}))
    reg.record(trial("hourly", {"sharpe_per_bar": 0.02, "periods_per_year": HOUR}))
    reg.record(trial("old-4h", {"sharpe_per_bar": 0.04}, ds="ds-4h"))  # before the fix
    reg.record(trial("old-unknown", {"sharpe_per_bar": 0.3}, ds="ds-mixed"))
    reg.record(trial("opening", {}))  # an OOS opening: counted, no Sharpe
    legacy = {"ds-4h": FOUR_H}.get

    sharpes, unscaled = reg.scaled_sharpes(HOUR, lambda t: legacy(t.dataset_version))
    assert sharpes == pytest.approx([0.1 / math.sqrt(24), 0.02, 0.04 / math.sqrt(4)])
    assert unscaled == 1  # old-unknown: left out of the variance, reported
    assert reg.count() == 5  # every trial still counts towards n


def test_old_trials_get_their_scale_from_their_dataset(dataset) -> None:
    root, version = dataset  # synthetic Kraken BTC-USD, daily bars, 24x7
    assert periods_from_dataset(root, version) == DAY
    assert periods_from_dataset(root, "ds-nonexistent") is None


def test_a_verification_records_its_scale_and_reports_unscaled_trials(dataset, tmp_path) -> None:
    r = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    (rec,) = [t for t in TrialRegistry(tmp_path).all() if t.experiment_id == "manual"]
    assert rec.metrics["periods_per_year"] == DAY
    (check,) = [c for c in r.verdict.checks if c.name == "trial_count"]
    assert check.metrics["trial_sharpes_unscaled"] == 0.0
    assert check.metrics["scale_periods_per_year"] == DAY
