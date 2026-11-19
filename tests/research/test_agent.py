"""The researcher agent (M4): hypothesis -> code -> counted backtests -> one submission.

A fake model and a fake Certo stand in; the manager, the dossier store and
the private store are real. Checked: the order of the steps, that every
backtest is a counted trial, that model answers are data (bad JSON is noted
and retried, never run), that it gives up cleanly, and that it submits the
best parameters once.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from firm.core.config import load_research_config
from firm.core.events import CollectingSink
from firm.research.agent import ModelResearcher, _json_object, class_name, trials
from firm.research.dossier import DossierStore, Stage
from firm.research.manager import CodeResult, ResearchManager
from verify_helpers import EXAMPLES, REPO

CODE = (EXAMPLES / "ma_crossover.py").read_bytes()


class FakeModel:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, dossier_id, messages):
        self.prompts.append(messages[-1]["content"])
        return self.answers.pop(0) if self.answers else '{"params": {"fast": 10, "slow": 40}}'


class FakeCerto:
    def __init__(self, results):
        self.results, self.tasks = list(results), []

    async def __call__(self, dossier_id, task):
        self.tasks.append(task)
        return self.results.pop(0)


class Desk:
    """The verification desk: backtests score fast/slow; verify records the call."""

    def __init__(self):
        self.trials, self.submitted = 0, []

    def backtest(self, path, class_name, params, *, dossier_id, **where):
        self.trials += 1
        return {
            "trial_id": f"t-{self.trials:016x}",
            "new_trial": True,
            "metrics": {"sharpe": params["fast"] / 10.0, "bars": 100.0},
        }

    def verify(self, path, class_name, params, **where):
        self.submitted.append((class_name, dict(params), where))
        return ("v-xxxxxxxxxxxxxxxx", "c-xxxxxxxxxxxxxxxx", "pass")


def floor(tmp_path, model, certo, *, datasets=True, trials=50):
    desk = Desk()
    cfg = load_research_config(REPO / "config")
    store = DossierStore(tmp_path, sink=CollectingSink())
    ticks = iter(range(10**6))
    manager = ResearchManager(
        cfg.model_copy(
            update={"researchers": 1, "budgets": cfg.budgets.model_copy(update={"trials": trials})}
        ),
        store,
        backtest=desk.backtest,
        verify=desk.verify,
        model_usage=lambda _id: (0, 0.0),
        monotonic=lambda: float(next(ticks)),
        think=model,
        coder=certo,
        datasets=(lambda i, tf: [("ds-xxxxxxxxxxxx", "kraken", 4000)]) if datasets else None,
    )
    d = manager.start(
        brief="trend on BTC", market="crypto", instruments=["BTC-USD"], timeframes=["1d"]
    )
    return manager, store, desk, d.dossier_id


def run(manager, researcher, ticks=20):
    async def go():
        for _ in range(ticks):
            if not manager.running():
                return
            await manager.atick(researcher)

    asyncio.run(go())


HYPOTHESIS = 'Sure! ```json\n{"hypothesis": "trends persist", "rationale": "momentum"}\n```'
PARAMS = [json.dumps({"params": {"fast": f, "slow": 40}}) for f in (5, 20, 12)]


def test_from_idea_to_one_submission(tmp_path) -> None:
    model = FakeModel([HYPOTHESIS, *PARAMS])
    certo = FakeCerto([CodeResult("verified", True, CODE)])
    manager, store, desk, did = floor(tmp_path, model, certo)
    run(manager, ModelResearcher(backtests=3))
    d = store.get(did)
    assert d.hypothesis == "trends persist" and d.rationale == "momentum"
    assert "trends persist" in certo.tasks[0] and "[0, 1]" in certo.tasks[0]
    assert len(d.code) == 1 and len(d.trial_ids) == 3 == desk.trials
    # the best of the three (fast=20) is submitted, once; the verifier decided
    assert desk.submitted == [
        (
            "MovingAverageCrossover",
            {"fast": 20, "slow": 40},
            {
                "market": "crypto",
                "dataset_version": "ds-xxxxxxxxxxxx",
                "venue": "kraken",
                "instrument": "BTC-USD",
                "tf": "1d",
            },
        )
    ]
    assert d.stage is Stage.PASSED
    # the model saw the results so far before proposing the next parameters
    assert '"sharpe": 0.5' in model.prompts[2]


def test_unreadable_answers_are_noted_then_it_gives_up(tmp_path) -> None:
    model = FakeModel(["I think trends persist.", "no json here either"])
    manager, store, desk, did = floor(tmp_path, model, FakeCerto([]))
    run(manager, ModelResearcher())
    d = store.get(did)
    assert d.stage is Stage.CLOSED and "gave up" in d.closed_reason
    assert d.hypothesis is None and desk.trials == 0


def test_failed_code_is_retried_then_it_gives_up(tmp_path) -> None:
    certo = FakeCerto(
        [
            CodeResult("failed", False, None, "pytest failed"),
            CodeResult("verified", True, b"x = 1\n"),
        ]
    )
    manager, store, _desk, did = floor(tmp_path, FakeModel([HYPOTHESIS]), certo)
    run(manager, ModelResearcher(code_attempts=2))
    d = store.get(did)
    assert len(certo.tasks) == 2 and not d.code  # nothing unusable reaches the store
    assert d.stage is Stage.CLOSED and "no working strategy code" in d.closed_reason
    assert any("pytest failed" in n.text for n in d.notes)


def test_repeated_or_bad_params_end_the_search_with_a_submission(tmp_path) -> None:
    same = '{"params": {"fast": 5, "slow": 40}}'
    model = FakeModel([HYPOTHESIS, same, same, '{"params": {"fast": true}}', "{}"])
    certo = FakeCerto([CodeResult("verified", True, CODE)])
    manager, _store, desk, _did = floor(tmp_path, model, certo)
    run(manager, ModelResearcher(backtests=5))
    assert desk.trials == 1 and len(desk.submitted) == 1


def test_no_dataset_means_no_backtest(tmp_path) -> None:
    certo = FakeCerto([CodeResult("verified", True, CODE)])
    manager, store, desk, did = floor(tmp_path, FakeModel([HYPOTHESIS]), certo, datasets=False)
    run(manager, ModelResearcher())
    d = store.get(did)
    assert d.stage is Stage.CLOSED and "no dataset" in d.closed_reason and desk.trials == 0


def test_helpers() -> None:
    assert _json_object('x {"a": {"b": 1}} y') == {"a": {"b": 1}}
    assert _json_object("[1, 2]") is None
    assert class_name(CODE) == "MovingAverageCrossover"
    with pytest.raises(ValueError):
        class_name(b"class A:\n    pass\nclass B:\n    pass\n")


def test_trials_are_read_back_from_notes(tmp_path) -> None:
    manager, _store, _desk, did = floor(tmp_path, FakeModel([]), FakeCerto([]))
    tools = manager.tools(did)
    tools.note("trial " + json.dumps({"params": {"a": 1}, "metrics": {"sharpe": 1.0}}))
    tools.note("trial not json")
    assert trials(tools) == [{"params": {"a": 1}, "metrics": {"sharpe": 1.0}}]


def test_the_trial_budget_caps_the_search(tmp_path) -> None:
    model = FakeModel([HYPOTHESIS, *PARAMS])
    certo = FakeCerto([CodeResult("verified", True, CODE)])
    manager, store, desk, did = floor(tmp_path, model, certo, trials=2)
    run(manager, ModelResearcher(backtests=6))
    assert desk.trials == 2 and len(store.get(did).trial_ids) == 2
    assert desk.submitted[0][1] == {"fast": 20, "slow": 40}  # best of the two
