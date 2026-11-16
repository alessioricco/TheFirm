"""Deflated Sharpe, the trial registry and the OOS vault."""

from datetime import UTC, datetime

import numpy as np
import pytest

from firm.core.domain import TrialRecord
from firm.verify.state import OosVault, TrialRegistry
from firm.verify.stats import deflated_sharpe, expected_max_sharpe

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def test_expected_max_sharpe_grows_with_trials() -> None:
    values = [expected_max_sharpe(n, 0.01) for n in (1, 2, 10, 100, 1000)]
    assert values[0] == 0.0
    assert values == sorted(values) and values[-1] > values[1]


def test_dsr_falls_as_trials_rise() -> None:
    r = np.random.default_rng(0).normal(0.001, 0.01, 1500)
    dsrs = [deflated_sharpe(r, n, [])["dsr"] for n in (1, 10, 100, 10_000)]
    assert dsrs == sorted(dsrs, reverse=True)
    assert dsrs[0] > dsrs[-1]


def test_dsr_uses_trial_variance_when_available() -> None:
    r = np.random.default_rng(1).normal(0.001, 0.01, 500)
    assert deflated_sharpe(r, 5, [0.01, 0.05, 0.02])["variance_from_trials"] == 1.0
    assert deflated_sharpe(r, 5, [0.01])["variance_from_trials"] == 0.0


def _trial(i: str) -> TrialRecord:
    return TrialRecord(
        trial_id=i,
        experiment_id="e",
        strategy_id="s",
        parameters={},
        metrics={"sharpe_per_bar": 0.01},
        dataset_version="ds",
        recorded_at=NOW,
    )


def test_registry_counts_each_trial_once(tmp_path) -> None:
    reg = TrialRegistry(tmp_path)
    assert reg.record(_trial("a")) and reg.record(_trial("b"))
    assert not reg.record(_trial("a"))
    assert reg.count() == 2


def test_vault_first_opening_replay_and_refusal(tmp_path) -> None:
    v = OosVault(tmp_path)
    kw = dict(strategy_id="s", dataset_version="ds", window=["a", "b"], at=NOW)
    assert v.request(candidate_key="k1", **kw).access == "first_opening"
    v.seal(strategy_id="s", candidate_key="k1", result_sha256="h", at=NOW)
    replay = v.request(candidate_key="k1", **kw)
    assert (replay.access, replay.sealed_hash) == ("replay", "h")
    assert v.request(candidate_key="k2", **kw).access == "refused"
    assert [e["event"] for e in v.events()] == ["first_opening", "sealed", "replay", "refused"]


@pytest.mark.parametrize("other", ["s2"])
def test_vault_is_per_strategy(tmp_path, other: str) -> None:
    v = OosVault(tmp_path)
    kw = dict(dataset_version="ds", window=["a", "b"], at=NOW)
    v.request(strategy_id="s", candidate_key="k1", **kw)
    assert v.request(strategy_id=other, candidate_key="k9", **kw).access == "first_opening"
