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
    assert v.request(scope="s|X|1d", candidate_key="k1", **kw).access == "first_opening"
    v.seal(strategy_id="s", scope="s|X|1d", candidate_key="k1", result_sha256="h", at=NOW)
    replay = v.request(scope="s|X|1d", candidate_key="k1", **kw)
    assert (replay.access, replay.sealed_hash) == ("replay", "h")
    assert v.request(scope="s|X|1d", candidate_key="k2", **kw).access == "refused"
    assert [e["event"] for e in v.events()] == ["first_opening", "sealed", "replay", "refused"]


@pytest.mark.parametrize("other", ["s2"])
def test_vault_is_per_strategy(tmp_path, other: str) -> None:
    v = OosVault(tmp_path)
    kw = dict(dataset_version="ds", window=["a", "b"], at=NOW)
    v.request(strategy_id="s", scope="s|X|1d", candidate_key="k1", **kw)
    assert (
        v.request(strategy_id=other, scope=f"{other}|X|1d", candidate_key="k9", **kw).access
        == "first_opening"
    )


# --- OOS scope: strategy + instrument + timeframe (owner decision 2026-11-16)


def test_vault_scope_is_strategy_instrument_timeframe(tmp_path) -> None:
    v = OosVault(tmp_path)
    kw = dict(strategy_id="s", dataset_version="ds", window=["a", "b"], at=NOW)
    assert v.request(scope="s|BTC-USD|1d", candidate_key="k1", **kw).access == "first_opening"
    assert v.request(scope="s|ETH-USD|1d", candidate_key="k1", **kw).access == "first_opening"
    assert v.request(scope="s|BTC-USD|1h", candidate_key="k1", **kw).access == "first_opening"
    assert v.request(scope="s|BTC-USD|1d", candidate_key="k2", **kw).access == "refused"


def test_legacy_openings_without_scope_still_block_the_whole_strategy(tmp_path) -> None:
    import json

    (tmp_path / "oos_openings.jsonl").write_text(
        json.dumps(
            {
                "event": "first_opening",
                "strategy_id": "s",
                "candidate_key": "old",
                "dataset_version": "ds",
                "window": ["a", "b"],
                "at": NOW.isoformat(),
            }
        )
        + "\n"
    )
    v = OosVault(tmp_path)
    kw = dict(strategy_id="s", dataset_version="ds", window=["a", "b"], at=NOW)
    assert v.request(scope="s|ETH-USD|1d", candidate_key="new", **kw).access == "refused"


def test_replay_under_a_new_verifier_config_gets_its_own_seal(tmp_path) -> None:
    """Changing costs/thresholds must not fail an unchanged strategy's replay, and
    must not open the hidden period to a changed strategy either."""
    v = OosVault(tmp_path)
    kw = dict(strategy_id="s", scope="s|X|1d", dataset_version="ds", window=["a", "b"], at=NOW)
    assert v.request(candidate_key="k1", config_key="fees-old", **kw).access == "first_opening"
    v.seal(
        strategy_id="s",
        scope="s|X|1d",
        candidate_key="k1",
        config_key="fees-old",
        result_sha256="h-old",
        at=NOW,
    )
    new = v.request(candidate_key="k1", config_key="fees-new", **kw)
    assert (new.access, new.sealed_hash) == ("replay", None)  # replay, nothing sealed yet
    old = v.request(candidate_key="k1", config_key="fees-old", **kw)
    assert (old.access, old.sealed_hash) == ("replay", "h-old")
    assert v.request(candidate_key="k2", config_key="fees-new", **kw).access == "refused"
