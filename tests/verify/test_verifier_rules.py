"""Verifier behaviour beyond the exit check: honesty rules and edge cases."""

from datetime import timedelta

from firm.core.domain import CheckStatus, VerdictOutcome
from firm.verify.runner import verify_strategy_file
from verify_helpers import EXAMPLES, NOW, REPO


def test_too_little_data_is_unverified_never_pass(dataset, tmp_path) -> None:
    root, version = dataset
    from firm.data.dataset import open_dataset

    start = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=NOW)["open_time"][0]
    r = verify_strategy_file(
        strategy_path=EXAMPLES / "ma_crossover.py",
        class_name="MovingAverageCrossover",
        params=None,
        data_root=root,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=tmp_path,
        now=NOW,
        as_of=start + timedelta(days=200),
    )
    assert r.verdict.outcome is VerdictOutcome.UNVERIFIED and r.exit_code == 2
    assert all(c.status is CheckStatus.NOT_RUN for c in r.verdict.checks)


def test_changed_version_cannot_reopen_oos(dataset, tmp_path) -> None:
    from verify_helpers import run_verify

    first = run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    assert first.verdict.outcome is VerdictOutcome.PASS
    tweaked = run_verify(
        dataset,
        tmp_path,
        "ma_crossover.py",
        "MovingAverageCrossover",
        params={"fast": 12, "slow": 40},
    )
    oos = next(c for c in tweaked.verdict.checks if c.name == "hidden_out_of_sample")
    assert oos.status is CheckStatus.FAILED and "already opened" in oos.detail


def test_report_changes_only_in_trial_count_when_global_trials_grow(dataset, tmp_path) -> None:
    """Reproducibility is per (dataset version, candidate, global trial count). Another
    strategy's trial in between raises the bar, by design (firm rule 5)."""
    import json

    from verify_helpers import run_verify

    a = json.loads(
        run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover").report
    )
    run_verify(dataset, tmp_path, "lookahead_ma.py", "LookaheadCrossover")  # one more trial
    b = json.loads(
        run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover").report
    )
    changed = [ca["name"] for ca, cb in zip(a["checks"], b["checks"], strict=True) if ca != cb]
    assert changed == ["trial_count"]
    assert b["checks"][3]["metrics"]["n_trials"] == a["checks"][3]["metrics"]["n_trials"] + 1
