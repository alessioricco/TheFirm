"""State schema versioning and `firm state show`."""

import json
from datetime import UTC, datetime

import pytest

from firm.control.cli import main
from firm.core.domain import TrialRecord
from firm.verify.state import (
    STATE_SCHEMA_VERSION,
    OosVault,
    StateVersionError,
    TrialRegistry,
    state_summary,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def _trial(i: str, exp: str = "manual") -> TrialRecord:
    return TrialRecord(
        trial_id=i,
        experiment_id=exp,
        strategy_id="s",
        parameters={},
        metrics={},
        dataset_version="ds",
        recorded_at=NOW,
    )


def test_first_write_stamps_the_schema_version(tmp_path) -> None:
    TrialRegistry(tmp_path).record(_trial("a"))
    assert (tmp_path / "STATE_VERSION").read_text().strip() == str(STATE_SCHEMA_VERSION)


def test_incompatible_state_is_refused_not_misread(tmp_path) -> None:
    TrialRegistry(tmp_path).record(_trial("a"))
    (tmp_path / "STATE_VERSION").write_text("999\n")
    with pytest.raises(StateVersionError):
        TrialRegistry(tmp_path).count()
    with pytest.raises(StateVersionError):
        TrialRegistry(tmp_path).record(_trial("b"))


def test_summary_counts_trials_vault_and_reports(tmp_path) -> None:
    reg = TrialRegistry(tmp_path)
    for i, exp in [("a", "manual"), ("b", "manual"), ("c", "search")]:
        reg.record(_trial(i, exp))
    vault = OosVault(tmp_path)
    kw = dict(dataset_version="ds", window=["x", "y"], at=NOW)
    vault.request(strategy_id="s", scope="s|X|1d", candidate_key="k1", **kw)
    vault.request(strategy_id="s", scope="s|X|1d", candidate_key="k2", **kw)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "v-1.json").write_text(
        json.dumps(
            {
                "verdict_id": "v-1",
                "outcome": "pass",
                "candidate": {"strategy_id": "s"},
                "data": {"dataset_version": "ds"},
            }
        )
    )
    s = state_summary(tmp_path)
    assert s["trials_total"] == 3
    assert s["trials_by_experiment"] == {"manual": 2, "search": 1}
    assert s["oos_vault"] == {"s": {"first_opening": 1, "refused": 1}}
    assert s["reports"] == [
        {"verdict_id": "v-1", "outcome": "pass", "strategy_id": "s", "dataset_version": "ds"}
    ]


def test_state_show_cli(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    TrialRegistry(tmp_path).record(_trial("a"))
    assert main(["state", "show", "--state-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "trials: 1" in out and "schema 1" in out
    assert main(["state", "show", "--state-root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["trials_total"] == 1
    assert main(["state", "show", "--state-root", str(tmp_path / "none")]) == 0
