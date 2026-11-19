"""The research manager: runs researchers within their budgets (M3 plan, M2).

Deterministic; no model here. A researcher (a fake in tests, the agent loop
from M4) acts on its dossier only through `ResearchTools`, which the
manager hands it one step at a time:

- **Scheduling**: up to `researchers` dossiers run at once, oldest first;
  the rest wait. Paused dossiers neither run nor hold a place. Finished
  (passed, failed, closed) dossiers free theirs.
- **Budgets**: each dossier keeps the budget it was opened with. The tools
  refuse a backtest past the trial budget; before and after every step the
  manager checks hours (time charged by its steps), tokens and cloud spend
  (from the ledger's MODEL_CALL events) and closes a dossier that has spent
  any of them. Nothing a researcher can call changes a budget or reads the
  config.
- **The researcher's world**: the dossier, the owner's floor brief, its own
  code in the private store, backtests through the verification desk
  (counted trials, research view only), and one submission to the verifier.
  Instruments and timeframes must be the dossier's.
- **Owner direction**: start, pause, resume, close, redirect (a draft gets
  the new brief; a started dossier is closed and a linked one opened, since
  its hypothesis is locked).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from firm.core.config import ResearchConfig
from firm.research.dossier import FINAL, Dossier, DossierError, DossierStore, FloorBriefs, Stage
from firm.research.store import SavedStrategy, save_strategy, strategies_dir

# backtest(path, class_name, params, *, dossier_id, market, dataset_version, venue,
#          instrument, tf) -> {"trial_id": ..., ...}: the verification desk's research backtest
Backtest = Callable[..., Mapping[str, Any]]
# verify(path, class_name, params, *, market, dataset_version, venue, instrument, tf)
#        -> (verdict_id, candidate_id, outcome)
Verify = Callable[..., tuple[str, str, str]]
# model_usage(dossier_id) -> (tokens, cloud GBP), from the ledger
ModelUsage = Callable[[str], tuple[int, float]]


class BudgetSpent(RuntimeError):
    """A dossier has spent one of its budgets: the researcher stops."""


class Researcher(Protocol):
    def step(self, tools: ResearchTools) -> None: ...


@dataclass(frozen=True)
class Usage:
    trials: int
    hours: float
    tokens: int
    cloud_gbp: float


class ResearchManager:
    def __init__(
        self,
        config: ResearchConfig,
        store: DossierStore,
        *,
        backtest: Backtest,
        verify: Verify,
        model_usage: ModelUsage,
        floor_briefs: FloorBriefs | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config, self.store = config, store
        self._backtest, self._verify, self._model_usage = backtest, verify, model_usage
        self.floor_briefs = floor_briefs or FloorBriefs(store.dir.parent.parent)
        self._monotonic = monotonic

    # ------------------------------------------------------------ the floor
    def start(
        self,
        *,
        brief: str,
        market: str,
        instruments: list[str],
        timeframes: list[str],
        dossier_id: str | None = None,
        redirected_from: str | None = None,
    ) -> Dossier:
        if not brief.strip():
            raise ValueError("a brief is required")
        dossier_id = dossier_id or (
            f"d-{self.store.clock().strftime('%Y%m%d')}-{secrets.token_hex(3)}"
        )
        return self.store.open(
            dossier_id,
            brief=brief,
            market=market,
            instruments=instruments,
            timeframes=timeframes,
            budget=self.config.budgets,
            redirected_from=redirected_from,
        )

    def queue(self) -> list[Dossier]:
        """Every dossier that may run, in the order they get a place."""
        active = [d for d in self.store.list() if d.stage not in FINAL and not d.paused]
        return sorted(active, key=lambda d: (d.opened_at, d.dossier_id))

    def running(self) -> list[Dossier]:
        return self.queue()[: self.config.researchers]

    def waiting(self) -> list[Dossier]:
        return self.queue()[self.config.researchers :]

    def redirect(self, dossier_id: str, brief: str) -> Dossier:
        d = self.store.get(dossier_id)
        if d.stage is Stage.DRAFT:
            self.store.rebrief(dossier_id, brief)
            return self.store.get(dossier_id)
        new = self.start(
            brief=brief,
            market=d.market,
            instruments=d.instruments,
            timeframes=d.timeframes,
            redirected_from=dossier_id,
        )
        self.store.close(dossier_id, f"redirected to {new.dossier_id}")
        return new

    # -------------------------------------------------------------- budgets
    def usage(self, d: Dossier) -> Usage:
        tokens, cloud = self._model_usage(d.dossier_id)
        return Usage(len(d.trial_ids), d.work_seconds / 3600, tokens, cloud)

    def over_budget(self, d: Dossier) -> str | None:
        """Why a dossier must stop (hours, tokens, cloud spend), or None. Trials
        are refused one by one by the tools, so a researcher can still submit."""
        u, b = self.usage(d), d.budget
        if u.hours >= b.wall_clock_hours:
            return f"hours {u.hours:.3g}/{b.wall_clock_hours:g}"
        if u.tokens >= b.tokens:
            return f"tokens {u.tokens}/{b.tokens}"
        if u.cloud_gbp > b.cloud_spend_gbp:
            return f"cloud spend £{u.cloud_gbp:.2f}/£{b.cloud_spend_gbp:.2f}"
        return None

    def enforce(self) -> list[str]:
        """Close every open dossier that has spent a budget; their IDs."""
        closed = []
        for d in self.store.list():
            if d.stage in FINAL:
                continue
            why = self.over_budget(d)
            if why:
                self.store.close(d.dossier_id, f"budget spent: {why}")
                closed.append(d.dossier_id)
        return closed

    # ---------------------------------------------------------------- steps
    def tools(self, dossier_id: str) -> ResearchTools:
        return ResearchTools(self, dossier_id)

    def tick(self, researcher: Researcher) -> None:
        """One step for each running researcher, in order (one model at a time)."""
        self.enforce()
        for d in self.running():
            started = self._monotonic()
            try:
                researcher.step(self.tools(d.dossier_id))
            except BudgetSpent as exc:
                self.store.close(d.dossier_id, f"budget spent: {exc}")
            finally:
                self.store.log_work(d.dossier_id, self._monotonic() - started)
        self.enforce()


class ResearchTools:
    """Everything a researcher may do, for one dossier. Read the dossier and the
    floor brief; write the hypothesis, code and notes; backtest; submit once."""

    def __init__(self, manager: ResearchManager, dossier_id: str) -> None:
        self._m, self._id = manager, dossier_id

    @property
    def dossier(self) -> Dossier:
        return self._m.store.get(self._id)

    @property
    def floor_brief(self) -> str | None:
        b = self._m.floor_briefs.current()
        return b.text if b else None

    def write_hypothesis(self, hypothesis: str, rationale: str) -> None:
        self._check()
        self._m.store.write_hypothesis(self._id, hypothesis, rationale)

    def save_code(self, code: bytes, *, stem: str = "strategy") -> SavedStrategy:
        if self._check().hypothesis is None:  # before anything is written to the store
            raise DossierError("write the hypothesis and its rationale first")
        saved = save_strategy(self._root(), self._id, code, stem=stem)
        self._m.store.add_code(self._id, saved)
        return saved

    def backtest(
        self,
        file: str,
        class_name: str,
        params: Mapping[str, Any],
        *,
        dataset_version: str,
        venue: str,
        instrument: str,
        tf: str,
    ) -> Mapping[str, Any]:
        d = self._check()
        if len(d.trial_ids) >= d.budget.trials:
            raise BudgetSpent(f"trials {len(d.trial_ids)}/{d.budget.trials}")
        path = self._where(d, file, instrument, tf)
        result = self._m._backtest(
            path,
            class_name,
            dict(params),
            dossier_id=self._id,
            market=d.market,
            dataset_version=dataset_version,
            venue=venue,
            instrument=instrument,
            tf=tf,
        )
        self._m.store.add_trial(self._id, result["trial_id"])
        return result

    def submit_candidate(
        self,
        file: str,
        class_name: str,
        params: Mapping[str, Any],
        *,
        dataset_version: str,
        venue: str,
        instrument: str,
        tf: str,
    ) -> str:
        """Submit the candidate to the verifier; the dossier records the verdict.
        Returns the outcome. (Not `submit`: that name is for orders, and firm
        rule 1's invariant test checks every `.submit(` call in the code.)"""
        d = self._check()
        path = self._where(d, file, instrument, tf)
        verdict_id, candidate_id, outcome = self._m._verify(
            path,
            class_name,
            dict(params),
            market=d.market,
            dataset_version=dataset_version,
            venue=venue,
            instrument=instrument,
            tf=tf,
        )
        self._m.store.record_submission(self._id, candidate_id)
        self._m.store.record_verdict(self._id, verdict_id, outcome)
        return outcome

    def note(self, text: str) -> None:
        self._m.store.note(self._id, text)

    # ------------------------------------------------------------- plumbing
    def _root(self):  # the data store
        return self._m.store.dir.parent.parent

    def _check(self) -> Dossier:
        d = self.dossier
        why = self._m.over_budget(d)
        if why:
            raise BudgetSpent(why)
        return d

    def _where(self, d: Dossier, file: str, instrument: str, tf: str):
        if instrument not in d.instruments:
            raise ValueError(f"instrument {instrument} is not in the dossier's brief")
        if tf not in d.timeframes:
            raise ValueError(f"timeframe {tf} is not in the dossier's brief")
        folder = strategies_dir(self._root(), self._id)
        path = folder / file
        if "/" in file or "\\" in file or not path.is_file():
            raise ValueError(f"{file!r} is not this dossier's code in the private store")
        return path
