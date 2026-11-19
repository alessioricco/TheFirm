"""Every backtest a researcher runs is a counted trial (firm rule 5, M3 plan R4).

Researchers don't run backtests themselves: their code runs only in the
sandbox, which can't read the data store. They ask the verification desk,
which owns the trial count and the hidden period:

- the backtest reads only the research view: bars before the hidden period
  the verifier will later open;
- the trial (experiment ID, bars per year, per-bar Sharpe) is recorded and
  written to the ledger before any result is returned: no result without a
  counted trial;
- the same backtest again is the same trial (code, parameters, bars);
- a later verification's deflated Sharpe sees every research trial.
"""

from __future__ import annotations

import pytest

from firm.core.domain import LedgerEventType
from firm.core.events import CollectingSink
from firm.data.dataset import open_dataset
from firm.verify.research import ResearchBacktester
from firm.verify.state import TrialRegistry
from verify_helpers import EXAMPLES, NOW, REPO, run_verify

DAY = 365.0


def bench(dataset, state, *, experiment="e-001", sink=None, **kw) -> ResearchBacktester:
    root, version = dataset
    return ResearchBacktester(
        strategy_path=kw.pop("path", EXAMPLES / "ma_crossover.py"),
        class_name="MovingAverageCrossover",
        experiment_id=experiment,
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
        sink=sink or CollectingSink(),
        **kw,
    )


def test_a_research_backtest_is_counted_before_its_result_is_returned(dataset, tmp_path):
    sink = CollectingSink()
    with bench(dataset, tmp_path, sink=sink) as b:
        r = b.run({"fast": 10, "slow": 40})
    (trial,) = TrialRegistry(tmp_path).all()
    assert trial.trial_id == r.trial_id and r.new_trial
    assert trial.experiment_id == "e-001"
    assert trial.metrics["periods_per_year"] == DAY
    assert trial.metrics["sharpe_per_bar"] == pytest.approx(r.metrics["sharpe_per_bar"])
    assert [t for t, *_ in sink.events] == [LedgerEventType.TRIAL]  # in the ledger too


def test_no_result_without_a_counted_trial(dataset, tmp_path, monkeypatch):
    def broken(self, trial):
        raise OSError("disk full")

    monkeypatch.setattr(TrialRegistry, "record", broken)
    with bench(dataset, tmp_path) as b, pytest.raises(OSError):
        b.run({"fast": 10, "slow": 40})


def test_research_never_reads_the_hidden_period(dataset, tmp_path):
    root, version = dataset
    all_bars = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=NOW)
    with bench(dataset, tmp_path) as b:
        r = b.run({"fast": 10, "slow": 40})
    hidden = all_bars["open_time"].iloc[len(all_bars) - round(len(all_bars) * 0.25)]
    assert r.hidden_from == hidden.to_pydatetime()
    assert r.last_bar_close <= r.hidden_from  # nothing from the hidden period
    assert r.bars < len(all_bars) * 0.76


def test_the_same_backtest_again_is_the_same_trial(dataset, tmp_path):
    with bench(dataset, tmp_path) as b:
        first = b.run({"fast": 10, "slow": 40})
        again = b.run({"fast": 10, "slow": 40})
        other = b.run({"fast": 12, "slow": 40})
    assert again.trial_id == first.trial_id and not again.new_trial
    assert other.trial_id != first.trial_id
    assert TrialRegistry(tmp_path).count() == 2


def test_a_later_verification_is_deflated_by_the_research_trials(dataset, tmp_path):
    alone = run_verify(dataset, tmp_path / "alone", "ma_crossover.py", "MovingAverageCrossover")
    with bench(dataset, tmp_path / "searched") as b:  # a parameter search: 30 trials
        for fast in range(5, 20):
            for slow in (30, 50):
                b.run({"fast": fast, "slow": slow})
    searched = run_verify(
        dataset, tmp_path / "searched", "ma_crossover.py", "MovingAverageCrossover"
    )

    def trial_check(result):
        return next(c for c in result.verdict.checks if c.name == "trial_count")

    a, s = trial_check(alone).metrics, trial_check(searched).metrics
    assert a["n_trials"] == 1 and s["n_trials"] >= 31  # 30 research trials + the manual one
    assert s["expected_max_sharpe"] > a["expected_max_sharpe"]  # the bar is higher
    assert s["dsr"] <= a["dsr"]


def test_a_private_strategy_is_backtested_only_in_the_sandbox(dataset, tmp_path, monkeypatch):
    import firm.sandbox.client as client
    from firm.research.store import save_strategy
    from firm.sandbox.client import SandboxError

    monkeypatch.setattr(client, "detect", lambda: None)  # no OS sandbox here
    root, _ = dataset
    saved = save_strategy(root, "d-r4", (EXAMPLES / "ma_crossover.py").read_bytes())
    with pytest.raises(SandboxError), bench(dataset, tmp_path, path=saved.path):
        pass
    assert TrialRegistry(tmp_path).count() == 0


def test_bad_data_is_never_a_trial(dataset, tmp_path, monkeypatch):
    import firm.verify.research as research

    monkeypatch.setattr(research, "implausible_bars", lambda *a, **k: ["a bar at ~0"])
    with bench(dataset, tmp_path) as b, pytest.raises(ValueError, match="implausible"):
        b.run({"fast": 10, "slow": 40})
    assert TrialRegistry(tmp_path).count() == 0


def test_a_parameter_search_on_a_private_strategy_runs_in_the_real_sandbox(dataset, tmp_path):
    import os

    from firm.research.store import save_strategy
    from firm.sandbox.policy import detect

    if detect() is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec / bubblewrap)")
    root, _ = dataset
    saved = save_strategy(root, "d-r4-box", (EXAMPLES / "ma_crossover.py").read_bytes())
    with bench(dataset, tmp_path / "box", path=saved.path) as b:
        boxed = [b.run({"fast": f, "slow": 40}) for f in (5, 10, 20)]
    with bench(dataset, tmp_path / "local") as b:
        local = [b.run({"fast": f, "slow": 40}) for f in (5, 10, 20)]
    assert [r.metrics for r in boxed] == [r.metrics for r in local]  # same numbers
    assert TrialRegistry(tmp_path / "box").count() == 3
