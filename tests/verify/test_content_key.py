"""The vault and trial registry key candidates by the CONTENT of the bars they read.

Re-processing the same bars (a new dataset version) is a replay, not a new
attempt; new or changed bars are still a new candidate. Openings recorded
under the old key (dataset version + window) are linked by an alias event.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from firm.core.domain import CheckStatus
from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars, normalize_trades
from firm.data.synthetic import seed_store, seed_trades
from firm.verify.state import OosVault, TrialRegistry
from firm.verify.verifier import bars_fingerprint
from verify_helpers import NOW, run_verify

FAR = datetime(2100, 1, 1, tzinfo=UTC)


def _oos(result):
    return next(c for c in result.verdict.checks if c.name == "hidden_out_of_sample")


def _same_bars_new_version(root: Path) -> tuple[Path, str]:
    """A second dataset version over the SAME bars (plus an unrelated trades series)."""
    seed_trades(root, n=300)
    files = normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    files += normalize_trades(root, provider="kraken", provider_symbol="XBTUSD")
    return root, build_dataset(root, files).dataset_version


def test_fingerprint_is_deterministic_and_content_sensitive(dataset) -> None:
    root, version = dataset
    bars = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=FAR)
    assert bars_fingerprint(bars) == bars_fingerprint(bars.copy())
    changed = bars.copy()
    changed.loc[100, "close"] = changed.loc[100, "close"] + 0.01
    assert bars_fingerprint(changed) != bars_fingerprint(bars)
    assert bars_fingerprint(bars.iloc[:-1]) != bars_fingerprint(bars)


def test_reprocessed_data_is_a_replay_not_a_new_attempt(tmp_path) -> None:
    root = tmp_path / "store"
    seed_store(root, n=2000, seed=7)
    v1 = build_dataset(
        root, normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    ).dataset_version
    _, v2 = _same_bars_new_version(root)
    assert v1 != v2
    state = tmp_path / "state"
    a = run_verify((root, v1), state, "ma_crossover.py", "MovingAverageCrossover")
    trials_after_a = TrialRegistry(state).count()
    b = run_verify((root, v2), state, "ma_crossover.py", "MovingAverageCrossover")
    assert _oos(a).status is CheckStatus.PASSED
    assert _oos(b).status is CheckStatus.PASSED  # replay, not "already opened"
    assert TrialRegistry(state).count() == trials_after_a  # same bars: not a new trial
    assert [e["event"] for e in OosVault(state).events()] == ["first_opening", "sealed", "replay"]


def test_changed_bars_are_still_a_new_candidate(tmp_path) -> None:
    state = tmp_path / "state"
    roots = []
    for i, n in enumerate((2000, 2010)):  # 10 more bars: different content
        root = tmp_path / f"s{i}"
        seed_store(root, n=n, seed=7)
        roots.append(
            (
                root,
                build_dataset(
                    root, normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
                ).dataset_version,
            )
        )
    run_verify(roots[0], state, "ma_crossover.py", "MovingAverageCrossover")
    b = run_verify(roots[1], state, "ma_crossover.py", "MovingAverageCrossover")
    assert _oos(b).status is CheckStatus.FAILED and "already opened" in _oos(b).detail


def test_legacy_opening_is_aliased_then_replayed(tmp_path, dataset) -> None:
    """An opening recorded under the old key (dataset version + window) is linked to
    the content key on the first verification that reproduces the old key."""
    from firm.verify.verifier import legacy_candidate_key

    root, version = dataset
    bars = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=FAR)
    window = [bars["open_time"].iloc[0].isoformat(), bars["close_time"].iloc[-1].isoformat()]
    code = (
        Path(__file__).resolve().parents[2] / "examples" / "strategies" / "ma_crossover.py"
    ).read_bytes()
    import hashlib

    old_key = legacy_candidate_key(
        hashlib.sha256(code).hexdigest(), {"fast": 10, "slow": 40}, version, window
    )
    state = tmp_path / "state"
    state.mkdir()
    (state / "STATE_VERSION").write_text("1\n")
    (state / "oos_openings.jsonl").write_text(
        json.dumps(
            {
                "event": "first_opening",
                "strategy_id": "ma_crossover",
                "candidate_key": old_key,
                "dataset_version": version,
                "window": window,
                "at": NOW.isoformat(),
            }
        )
        + "\n"
    )
    r = run_verify(dataset, state, "ma_crossover.py", "MovingAverageCrossover")
    assert _oos(r).status is CheckStatus.PASSED
    events = [e["event"] for e in OosVault(state).events()]
    assert events[:3] == ["first_opening", "alias", "replay"]
    r2 = run_verify(dataset, state, "ma_crossover.py", "MovingAverageCrossover")
    assert _oos(r2).status is CheckStatus.PASSED
    assert [e["event"] for e in OosVault(state).events()].count("alias") == 1


def test_fingerprint_is_in_the_report(dataset, tmp_path) -> None:
    root, version = dataset
    body = json.loads(
        run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover").report
    )
    bars = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=FAR)
    assert body["data"]["bars_sha256"] == bars_fingerprint(bars)


@pytest.mark.parametrize("col", ["open_time", "close"])
def test_fingerprint_needs_the_canonical_columns(col) -> None:
    df = pd.DataFrame(
        {
            "open_time": [],
            "close_time": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
    ).drop(columns=[col])
    with pytest.raises(KeyError):
        bars_fingerprint(df)


def test_legacy_trial_is_aliased_not_counted_twice(tmp_path) -> None:
    """A trial recorded under the old ID scheme (dataset version + window) is linked to
    its content ID, so the same bars on a new dataset version don't add a trial."""
    from firm.core.domain import TrialRecord
    from firm.verify.runner import _h
    from verify_helpers import EXAMPLES

    root = tmp_path / "store"
    seed_store(root, n=2000, seed=7)
    v1 = build_dataset(
        root, normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    ).dataset_version
    bars = open_dataset(root, v1).bars("kraken", "BTC-USD", "1d", as_of=FAR)
    window = [bars["open_time"].iloc[0].isoformat(), bars["close_time"].iloc[-1].isoformat()]
    import hashlib

    code = hashlib.sha256((EXAMPLES / "ma_crossover.py").read_bytes()).hexdigest()
    p = {"fast": 10, "slow": 40}
    legacy = "t-" + _h(["ma_crossover", code, p, v1, window])[:16]
    state = tmp_path / "state"
    TrialRegistry(state).record(
        TrialRecord(
            trial_id=legacy,
            experiment_id="manual",
            strategy_id="ma_crossover",
            parameters=p,
            metrics={},
            dataset_version=v1,
            recorded_at=NOW,
        )
    )
    run_verify((root, v1), state, "ma_crossover.py", "MovingAverageCrossover")
    _, v2 = _same_bars_new_version(root)
    run_verify((root, v2), state, "ma_crossover.py", "MovingAverageCrossover")
    reg = TrialRegistry(state)
    manual = [t for t in reg.all() if t.experiment_id == "manual"]
    assert len(manual) == 1  # the legacy record only; no second in-sample trial
    assert reg.count() == 2  # that trial (aliased) + the OOS opening
