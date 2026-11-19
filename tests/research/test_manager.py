"""The research manager: deterministic, no model (M3 plan, M2).

- Runs up to `researchers` dossiers at once, oldest first; the rest wait.
  Paused dossiers neither run nor hold a place.
- Enforces each dossier's budget itself: a researcher only acts through the
  manager's tools, which refuse a step once a budget is spent, and the
  manager closes that dossier. Nothing the researcher can call changes a
  budget.
- Redirect: a new brief closes the dossier (its hypothesis is locked once
  research starts) and opens a linked one; a draft just gets the new brief.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from firm.core.config import load_research_config
from firm.core.events import CollectingSink
from firm.research.dossier import DossierStore, FloorBriefs, Stage
from firm.research.manager import BudgetSpent, ResearchManager, ResearchTools
from verify_helpers import EXAMPLES, NOW, REPO

CODE = (EXAMPLES / "ma_crossover.py").read_bytes()
T0 = datetime(2026, 11, 20, 9, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class FakeBacktest:
    """Stands in for the verification desk's research backtester."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, path, class_name, params, *, dossier_id, **where):
        self.calls += 1
        return {"trial_id": f"t-{self.calls:016x}", "sharpe": 1.0}


def fake_verify(path, class_name, params, **where):
    return ("v-xxxxxxxxxxxxxxxx", "c-xxxxxxxxxxxxxxxx", "pass")


class Researcher:
    """A fake researcher: hypothesis, code, `n` backtests, then submit."""

    def __init__(self, n: int = 3, dataset: str = "ds-xxxxxxxxxxxx") -> None:
        self.n, self.dataset = n, dataset

    def step(self, tools: ResearchTools) -> None:
        d = tools.dossier
        if d.hypothesis is None:
            tools.write_hypothesis("trends persist", "momentum literature")
            return
        if not d.code:
            tools.save_code(CODE, stem="ma")
            return
        if len(d.trial_ids) < self.n:
            tools.backtest(
                d.code[-1].file,
                "MovingAverageCrossover",
                {"fast": 10 + len(d.trial_ids), "slow": 40},
                dataset_version=self.dataset,
                venue="kraken",
                instrument="BTC-USD",
                tf="1d",
            )
            return
        tools.submit_candidate(
            d.code[-1].file,
            "MovingAverageCrossover",
            {"fast": 10, "slow": 40},
            dataset_version=self.dataset,
            venue="kraken",
            instrument="BTC-USD",
            tf="1d",
        )


def floor(tmp_path, *, researchers=2, trials=200, hours=24.0, tokens=2_000_000, used=(0, 0.0)):
    cfg = load_research_config(REPO / "config")
    cfg = cfg.model_copy(
        update={
            "researchers": researchers,
            "budgets": cfg.budgets.model_copy(
                update={"trials": trials, "wall_clock_hours": hours, "tokens": tokens}
            ),
        }
    )
    store = DossierStore(tmp_path, sink=CollectingSink(), clock=Clock())
    manager = ResearchManager(
        cfg,
        store,
        backtest=FakeBacktest(),
        verify=fake_verify,
        model_usage=lambda _id: used,
    )
    return manager, store


def start(manager, n: int) -> list[str]:
    return [
        manager.start(
            brief=f"idea {i}", market="crypto", instruments=["BTC-USD"], timeframes=["1d"]
        ).dossier_id
        for i in range(n)
    ]


def test_n_researchers_run_and_the_rest_wait_in_order(tmp_path) -> None:
    manager, _ = floor(tmp_path, researchers=2)
    ids = start(manager, 4)
    assert [d.dossier_id for d in manager.running()] == ids[:2]
    assert [d.dossier_id for d in manager.waiting()] == ids[2:]


def test_a_paused_dossier_gives_up_its_place(tmp_path) -> None:
    manager, store = floor(tmp_path, researchers=2)
    ids = start(manager, 3)
    store.pause(ids[0], "owner")
    assert [d.dossier_id for d in manager.running()] == ids[1:]
    store.resume(ids[0])
    assert [d.dossier_id for d in manager.running()] == ids[:2]


def test_a_fake_researcher_goes_from_idea_to_verdict(tmp_path) -> None:
    manager, store = floor(tmp_path, researchers=1)
    (dossier_id,) = start(manager, 1)
    researcher = Researcher(n=3)
    for _ in range(10):
        manager.tick(researcher)
    d = store.get(dossier_id)
    assert d.stage is Stage.PASSED and len(d.trial_ids) == 3
    assert d.verdicts[0].verdict_id == "v-xxxxxxxxxxxxxxxx"
    assert d.work_seconds > 0  # each step's time is charged to the dossier
    assert manager.running() == []  # finished: its place is free


def test_the_trial_budget_stops_the_researcher_and_closes_the_dossier(tmp_path) -> None:
    manager, store = floor(tmp_path, trials=2)
    (dossier_id,) = start(manager, 1)
    researcher = Researcher(n=5)
    for _ in range(10):
        manager.tick(researcher)
    d = store.get(dossier_id)
    assert len(d.trial_ids) == 2
    assert d.stage is Stage.CLOSED and "budget" in d.closed_reason and "trials" in d.closed_reason


@pytest.mark.parametrize(
    ("kw", "what"),
    [({"tokens": 1000, "used": (1000, 0.0)}, "tokens"), ({"hours": 1e-9}, "hours")],
)
def test_token_and_time_budgets_close_the_dossier(tmp_path, kw, what) -> None:
    manager, store = floor(tmp_path, **kw)
    (dossier_id,) = start(manager, 1)
    manager.tick(Researcher())  # one step charges time; tokens come from the ledger
    manager.tick(Researcher())
    d = store.get(dossier_id)
    assert d.stage is Stage.CLOSED and what in d.closed_reason


def test_cloud_spend_over_budget_closes_the_dossier(tmp_path) -> None:
    manager, store = floor(tmp_path, used=(10, 0.01))  # the budget is £0
    (dossier_id,) = start(manager, 1)
    manager.tick(Researcher())
    assert "cloud" in store.get(dossier_id).closed_reason


def test_the_tools_refuse_once_the_budget_is_spent(tmp_path) -> None:
    manager, store = floor(tmp_path, trials=1)
    (dossier_id,) = start(manager, 1)
    tools = manager.tools(dossier_id)
    tools.write_hypothesis("h", "why")
    tools.save_code(CODE, stem="ma")
    file = store.get(dossier_id).code[-1].file
    where = {
        "dataset_version": "ds-xxxxxxxxxxxx",
        "venue": "kraken",
        "instrument": "BTC-USD",
        "tf": "1d",
    }
    tools.backtest(file, "MovingAverageCrossover", {}, **where)
    with pytest.raises(BudgetSpent, match="trials"):
        tools.backtest(file, "MovingAverageCrossover", {"fast": 5}, **where)


def test_the_tools_stay_inside_the_dossiers_brief(tmp_path) -> None:
    manager, _ = floor(tmp_path)
    (dossier_id,) = start(manager, 1)
    tools = manager.tools(dossier_id)
    tools.write_hypothesis("h", "why")
    tools.save_code(CODE, stem="ma")
    file = tools.dossier.code[-1].file
    with pytest.raises(ValueError, match="instrument"):
        tools.backtest(
            file,
            "MovingAverageCrossover",
            {},
            dataset_version="ds-xxxxxxxxxxxx",
            venue="kraken",
            instrument="ETH-USD",
            tf="1d",
        )
    with pytest.raises(ValueError, match="private store"):
        tools.backtest(
            "../../escape.py",
            "MovingAverageCrossover",
            {},
            dataset_version="ds-xxxxxxxxxxxx",
            venue="kraken",
            instrument="BTC-USD",
            tf="1d",
        )
    assert not hasattr(tools, "budget_set") and "config" not in dir(tools)


def test_redirect_closes_a_started_dossier_and_opens_a_linked_one(tmp_path) -> None:
    manager, store = floor(tmp_path)
    (first,) = start(manager, 1)
    manager.tick(Researcher())  # hypothesis written
    manager.tick(Researcher())  # code saved
    manager.tick(Researcher())  # first backtest: research has started
    new = manager.redirect(first, "carry instead of momentum")
    old = store.get(first)
    assert old.stage is Stage.CLOSED and new.dossier_id in old.closed_reason
    assert new.redirected_from == first and new.brief == "carry instead of momentum"
    assert new.stage is Stage.DRAFT


def test_redirecting_a_draft_just_replaces_its_brief(tmp_path) -> None:
    manager, store = floor(tmp_path)
    (first,) = start(manager, 1)
    same = manager.redirect(first, "a sharper brief")
    assert same.dossier_id == first and store.get(first).brief == "a sharper brief"


def test_the_floor_brief_is_what_the_owner_set_last(tmp_path) -> None:
    briefs = FloorBriefs(tmp_path, clock=Clock())
    assert briefs.current() is None
    briefs.set("stay uncorrelated with the daily MA strategy")
    briefs.set("prefer 1h and 4h")
    assert briefs.current().text == "prefer 1h and 4h"
    briefs.set("")  # cleared
    assert briefs.current() is None
    manager, _ = floor(tmp_path)
    (dossier_id,) = start(manager, 1)
    briefs.set("prefer 1h and 4h")
    assert manager.tools(dossier_id).floor_brief == "prefer 1h and 4h"


def test_end_to_end_through_the_real_verifier_and_sandbox(dataset, tmp_path) -> None:
    """The fake researcher, the real research backtester and verifier, in the
    real OS sandbox: a verified strategy, every backtest a counted trial."""
    from firm.sandbox.policy import detect
    from firm.verify.state import TrialRegistry

    if detect() is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec / bubblewrap)")
    from firm.control.research import verification_desk

    root, version = dataset
    state = tmp_path / "state"
    backtest, verify = verification_desk(
        data_root=root, config_root=REPO / "config", state=state, now=lambda: NOW
    )
    cfg = load_research_config(REPO / "config")
    store = DossierStore(root, clock=Clock())
    manager = ResearchManager(
        cfg, store, backtest=backtest, verify=verify, model_usage=lambda _i: (0, 0.0)
    )
    d = manager.start(brief="momentum", market="crypto", instruments=["BTC-USD"], timeframes=["1d"])

    for _ in range(8):
        manager.tick(Researcher(n=2, dataset=version))
    done = store.get(d.dossier_id)
    assert done.stage is Stage.PASSED, done
    research = [t for t in TrialRegistry(state).all() if t.experiment_id == d.dossier_id]
    assert len(research) == 2  # both backtests counted, under the dossier's ID
