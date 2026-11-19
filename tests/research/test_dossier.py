"""A researcher's dossier: one strategy from idea to verdict (M3 plan, M1).

- The hypothesis and why the edge should exist come first: no code or
  backtest before them, and they can't be rewritten once a backtest exists
  (a new idea is a new dossier).
- Stages follow a fixed path; a failed submission closes the dossier, an
  unverified one goes back to research. Every stage change is a ledger
  event (without the hypothesis text: the ledger has many readers).
- Stored privately in the data store as an append-only log, never in git.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from firm.core.config import load_research_config
from firm.core.domain import LedgerEventType
from firm.core.events import CollectingSink
from firm.research.dossier import DossierError, DossierStore, Stage
from firm.research.store import save_strategy
from verify_helpers import EXAMPLES, REPO

T0 = datetime(2026, 11, 20, 9, tzinfo=UTC)
HYPOTHESIS = "BTC trends persist for weeks after a breakout"
RATIONALE = "slow-moving capital and momentum chasing (published momentum literature)"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def store(tmp_path, sink=None) -> DossierStore:
    return DossierStore(tmp_path, sink=sink or CollectingSink(), clock=Clock())


def opened(tmp_path, sink=None, dossier_id="d-001"):
    s = store(tmp_path, sink)
    budget = load_research_config(REPO / "config").budgets
    s.open(
        dossier_id,
        brief="momentum on BTC daily bars",
        market="crypto",
        instruments=["BTC-USD"],
        timeframes=["1d"],
        budget=budget,
    )
    return s


def stages(sink) -> list[tuple[str | None, str]]:
    return [(p["from"], p["to"]) for t, _r, p, _i in sink.events if t is LedgerEventType.DOSSIER]


def test_a_dossier_starts_as_a_draft_with_its_brief_and_budget(tmp_path) -> None:
    sink = CollectingSink()
    d = opened(tmp_path, sink).get("d-001")
    assert d.stage is Stage.DRAFT and d.brief == "momentum on BTC daily bars"
    assert d.budget.cloud_spend_gbp == 0 and d.hypothesis is None
    assert stages(sink) == [(None, "draft")]
    assert (tmp_path / "research" / "dossiers" / "d-001.jsonl").is_file()


def test_no_code_or_backtest_before_the_hypothesis(tmp_path) -> None:
    s = opened(tmp_path)
    code = save_strategy(tmp_path, "d-001", (EXAMPLES / "ma_crossover.py").read_bytes())
    with pytest.raises(DossierError, match="hypothesis"):
        s.add_code("d-001", code)
    with pytest.raises(DossierError, match="hypothesis"):
        s.add_trial("d-001", "t-0123456789abcdef")
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    s.add_code("d-001", code)
    assert s.get("d-001").code[0].sha256 == code.sha256


def test_the_hypothesis_can_be_revised_until_the_first_backtest(tmp_path) -> None:
    s = opened(tmp_path)
    s.write_hypothesis("d-001", "first draft", RATIONALE)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)  # still before any backtest
    s.add_trial("d-001", "t-0123456789abcdef")
    with pytest.raises(DossierError, match="new dossier"):
        s.write_hypothesis("d-001", "something else", RATIONALE)
    d = s.get("d-001")
    assert d.hypothesis == HYPOTHESIS and d.stage is Stage.RESEARCH


def test_the_path_to_a_pass(tmp_path) -> None:
    sink = CollectingSink()
    s = opened(tmp_path, sink)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    s.add_trial("d-001", "t-0000000000000001")
    s.add_trial("d-001", "t-0000000000000001")  # the same trial once
    s.add_trial("d-001", "t-0000000000000002")
    s.record_submission("d-001", "c-0123456789abcdef")
    s.record_verdict("d-001", "v-xxxxxxxxxxxxxxxx", "pass")
    d = s.get("d-001")
    assert d.stage is Stage.PASSED and d.trial_ids == ["t-0000000000000001", "t-0000000000000002"]
    assert d.verdicts[0].verdict_id == "v-xxxxxxxxxxxxxxxx"
    assert stages(sink) == [
        (None, "draft"),
        ("draft", "hypothesis"),
        ("hypothesis", "research"),
        ("research", "submitted"),
        ("submitted", "passed"),
    ]


def test_a_failed_submission_closes_the_dossier(tmp_path) -> None:
    s = opened(tmp_path)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    s.add_trial("d-001", "t-0000000000000001")
    s.record_submission("d-001", "c-0123456789abcdef")
    s.record_verdict("d-001", "v-xxxxxxxxxxxxxxxx", "fail")
    assert s.get("d-001").stage is Stage.FAILED
    with pytest.raises(DossierError, match="failed"):
        s.add_trial("d-001", "t-0000000000000002")  # a changed strategy is a new dossier


def test_an_unverified_submission_goes_back_to_research(tmp_path) -> None:
    s = opened(tmp_path)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    s.add_trial("d-001", "t-0000000000000001")
    s.record_submission("d-001", "c-0123456789abcdef")
    s.record_verdict("d-001", "v-xxxxxxxxxxxxxxxx", "unverified")  # bad data, sandbox
    assert s.get("d-001").stage is Stage.RESEARCH


def test_submitting_needs_a_backtest_and_verdicts_need_a_submission(tmp_path) -> None:
    s = opened(tmp_path)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    with pytest.raises(DossierError):
        s.record_submission("d-001", "c-0123456789abcdef")
    with pytest.raises(DossierError):
        s.record_verdict("d-001", "v-xxxxxxxxxxxxxxxx", "pass")


def test_closing_is_final_and_has_a_reason(tmp_path) -> None:
    sink = CollectingSink()
    s = opened(tmp_path, sink)
    s.close("d-001", "owner: not worth pursuing")
    d = s.get("d-001")
    assert d.stage is Stage.CLOSED and d.closed_reason == "owner: not worth pursuing"
    for change in (
        lambda: s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE),
        lambda: s.note("d-001", "late thought"),
        lambda: s.close("d-001", "again"),
    ):
        with pytest.raises(DossierError, match="closed"):
            change()
    assert stages(sink)[-1] == ("draft", "closed")


def test_the_ledger_never_sees_the_hypothesis(tmp_path) -> None:
    sink = CollectingSink()
    s = opened(tmp_path, sink)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    s.note("d-001", "a private note")
    text = str(sink.events)
    assert HYPOTHESIS not in text and RATIONALE not in text and "private note" not in text


def test_dossiers_persist_append_only(tmp_path) -> None:
    s = opened(tmp_path)
    s.write_hypothesis("d-001", HYPOTHESIS, RATIONALE)
    path = tmp_path / "research" / "dossiers" / "d-001.jsonl"
    before = path.read_text()
    s.note("d-001", "tried a slower filter")
    assert path.read_text().startswith(before)  # only appended
    again = store(tmp_path).get("d-001")  # a new process reads the same dossier
    assert again.hypothesis == HYPOTHESIS and again.notes[0].text == "tried a slower filter"
    assert [d.dossier_id for d in store(tmp_path).list()] == ["d-001"]


def test_ids_are_checked_and_unique(tmp_path) -> None:
    s = opened(tmp_path)
    with pytest.raises(DossierError, match="exists"):
        opened(tmp_path)
    with pytest.raises(DossierError, match="no dossier"):
        s.get("d-404")
    with pytest.raises(ValueError):
        s.open(
            "../escape",
            brief="x",
            market="crypto",
            instruments=["BTC-USD"],
            timeframes=["1d"],
            budget=load_research_config(REPO / "config").budgets,
        )
