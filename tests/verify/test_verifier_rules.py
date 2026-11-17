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
    assert r.verdict.checks[0].name == "data_quality"  # the data itself was fine
    assert all(c.status is CheckStatus.NOT_RUN for c in r.verdict.checks[1:])


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
    trials_a = next(c for c in a["checks"] if c["name"] == "trial_count")
    trials_b = next(c for c in b["checks"] if c["name"] == "trial_count")
    assert trials_b["metrics"]["n_trials"] == trials_a["metrics"]["n_trials"] + 1


def test_implausible_bars_are_refused_and_burn_no_trial(tmp_path) -> None:
    """A corrupt bar in the window: UNVERIFIED, every check not run, no trial counted."""
    from datetime import UTC, datetime

    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars
    from firm.data.store import write_capture
    from firm.data.synthetic import kraken_ohlc_payload, seed_store, synthetic_bars
    from firm.verify.state import TrialRegistry

    root, state = tmp_path / "store", tmp_path / "state"
    seed_store(root, n=600)
    start = datetime(2019, 1, 1, tzinfo=UTC)
    bars = synthetic_bars(601, seed=7, start=start, tf="1d")
    b = bars[300]
    bars[300] = type(b)(
        open_time=b.open_time,
        open=b.open * 0.0001,
        high=b.high,
        low=b.open * 0.0001,
        close=b.close,
        volume=b.volume,
    )
    write_capture(
        root,
        kraken_ohlc_payload(bars),
        provider="kraken",
        kind="bars",
        provider_symbol="XBTUSD",
        tf="1d",
        fmt="json",
        captured_at=datetime(2021, 1, 1, tzinfo=UTC),
        request={"t": "corrupt"},
        via="test",
        licence="synthetic",
        capture_method="synthetic",
    )
    files = normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    version = build_dataset(root, files).dataset_version  # built WITHOUT quarantine
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
        state=state,
        now=NOW,
    )
    assert r.verdict.outcome is VerdictOutcome.UNVERIFIED and r.exit_code == 2
    quality = r.verdict.checks[0]
    assert quality.name == "data_quality" and quality.status is CheckStatus.NOT_RUN
    assert "implausible" in quality.detail
    assert all(c.status is CheckStatus.NOT_RUN for c in r.verdict.checks)
    assert TrialRegistry(state).count() == 0  # bad data burns no trial
