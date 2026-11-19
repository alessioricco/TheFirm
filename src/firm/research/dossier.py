"""Research dossiers: one researcher, one strategy, from idea to verdict (M3, M1).

A dossier holds the owner's brief, the hypothesis and why the edge should
exist (written before any backtest), the strategy code it produced (by
hash, in the private store), every trial, the verdicts, notes, and the
budget it was given. Stages:

    draft -> hypothesis -> research -> submitted -> passed
                                          |   \\-> failed  (closed: a changed
                                          |                strategy is a new dossier)
                                          \\-> research    (unverified: bad data, sandbox)
    any open stage -> closed (owner or manager, with a reason)

Rules enforced here:

- no code and no trial before the hypothesis; the hypothesis may be revised
  until the first trial, never after (a new idea is a new dossier);
- `failed`, `closed` (and `passed`, until promotion exists) take no changes;
- every stage change is a DOSSIER ledger event, with the dossier ID, the
  stages and a reason, never the hypothesis, rationale or notes (private
  research; the ledger has many readers).

Storage: `<data root>/research/dossiers/<id>.jsonl`, an append-only log of
events; a dossier is that log folded. Private: the data store is git-ignored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, Field

from firm.core.base import FirmModel
from firm.core.config import DossierBudget
from firm.core.domain import LedgerEventType
from firm.core.events import NULL_SINK, EventSink
from firm.research.store import SavedStrategy

ZONE = Path("research") / "dossiers"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class DossierError(ValueError):
    """A change the dossier's stage or rules don't allow."""


class Stage(StrEnum):
    DRAFT = "draft"
    HYPOTHESIS = "hypothesis"
    RESEARCH = "research"
    SUBMITTED = "submitted"
    PASSED = "passed"
    FAILED = "failed"
    CLOSED = "closed"


FINAL = {Stage.PASSED, Stage.FAILED, Stage.CLOSED}


class CodeRef(FirmModel):
    sha256: str
    file: str  # name in the private store (research/strategies/<dossier>/)
    at: AwareDatetime


class VerdictRef(FirmModel):
    verdict_id: str
    candidate_id: str
    outcome: str  # pass | fail | unverified
    at: AwareDatetime


class Note(FirmModel):
    at: AwareDatetime
    text: str


class Dossier(FirmModel):
    dossier_id: str
    brief: str
    market: str
    instruments: list[str]
    timeframes: list[str]
    budget: DossierBudget
    opened_at: AwareDatetime
    stage: Stage = Stage.DRAFT
    hypothesis: str | None = None
    rationale: str | None = None
    hypothesis_at: AwareDatetime | None = None
    code: list[CodeRef] = Field(default_factory=list)
    trial_ids: list[str] = Field(default_factory=list)
    submitted_candidate: str | None = None
    verdicts: list[VerdictRef] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    closed_reason: str | None = None
    paused: bool = False  # by the owner; a paused dossier doesn't run or hold a place
    work_seconds: float = 0.0  # researcher time charged to it (the hours budget)
    redirected_from: str | None = None  # the dossier a redirect closed to open this one


def _check_id(dossier_id: str) -> str:
    if not _ID.match(dossier_id):
        raise ValueError(f"invalid dossier ID {dossier_id!r}: letters, digits, '-' and '_'")
    return dossier_id


class DossierStore:
    def __init__(
        self,
        root: Path,
        *,
        sink: EventSink = NULL_SINK,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """`root`: the data store (`$FIRM_DATA_ROOT`)."""
        self.dir = Path(root) / ZONE
        self.sink, self.clock = sink, clock

    # ---------------------------------------------------------------- reads
    def path(self, dossier_id: str) -> Path:
        return self.dir / f"{_check_id(dossier_id)}.jsonl"

    def get(self, dossier_id: str) -> Dossier:
        path = self.path(dossier_id)
        if not path.is_file():
            raise DossierError(f"no dossier {dossier_id}")
        events = [json.loads(line) for line in path.read_text().splitlines() if line]
        return _fold(events)

    def list(self) -> list[Dossier]:
        if not self.dir.is_dir():
            return []
        return [self.get(p.stem) for p in sorted(self.dir.glob("*.jsonl"))]

    # -------------------------------------------------------------- changes
    def open(
        self,
        dossier_id: str,
        *,
        brief: str,
        market: str,
        instruments: list[str],
        timeframes: list[str],
        budget: DossierBudget,
        redirected_from: str | None = None,
    ) -> Dossier:
        path = self.path(dossier_id)
        if path.exists():
            raise DossierError(f"dossier {dossier_id} exists")
        self._append(
            dossier_id,
            {
                "event": "opened",
                "dossier_id": dossier_id,
                "brief": brief,
                "market": market,
                "instruments": list(instruments),
                "timeframes": list(timeframes),
                "budget": budget.model_dump(mode="json"),
                "redirected_from": redirected_from,
            },
        )
        self._stage(dossier_id, None, Stage.DRAFT, "opened")
        return self.get(dossier_id)

    def write_hypothesis(self, dossier_id: str, hypothesis: str, rationale: str) -> None:
        d = self._open_dossier(dossier_id)
        if d.trial_ids or d.stage not in (Stage.DRAFT, Stage.HYPOTHESIS):
            raise DossierError(
                "the hypothesis can't change once a backtest exists: a new idea is a new dossier"
            )
        if not hypothesis.strip() or not rationale.strip():
            raise DossierError("a hypothesis and its rationale are both required")
        self._append(dossier_id, {"event": "hypothesis", "text": hypothesis, "why": rationale})
        if d.stage is Stage.DRAFT:
            self._stage(dossier_id, Stage.DRAFT, Stage.HYPOTHESIS, "hypothesis written")

    def add_code(self, dossier_id: str, saved: SavedStrategy) -> None:
        self._needs_hypothesis(self._open_dossier(dossier_id))
        self._append(dossier_id, {"event": "code", "sha256": saved.sha256, "file": saved.path.name})

    def add_trial(self, dossier_id: str, trial_id: str) -> None:
        d = self._open_dossier(dossier_id)
        self._needs_hypothesis(d)
        if d.stage is Stage.SUBMITTED:
            raise DossierError("submitted: wait for the verdict")
        if trial_id in d.trial_ids:
            return
        self._append(dossier_id, {"event": "trial", "trial_id": trial_id})
        if d.stage is Stage.HYPOTHESIS:
            self._stage(dossier_id, Stage.HYPOTHESIS, Stage.RESEARCH, "first backtest")

    def record_submission(self, dossier_id: str, candidate_id: str) -> None:
        d = self._open_dossier(dossier_id)
        if d.stage is not Stage.RESEARCH:
            raise DossierError(f"only a dossier in research can be submitted (it is {d.stage})")
        self._append(dossier_id, {"event": "submitted", "candidate_id": candidate_id})
        self._stage(dossier_id, Stage.RESEARCH, Stage.SUBMITTED, f"submitted {candidate_id}")

    def record_verdict(self, dossier_id: str, verdict_id: str, outcome: str) -> None:
        d = self._open_dossier(dossier_id)
        if d.stage is not Stage.SUBMITTED:
            raise DossierError(f"no submission waiting for a verdict (stage {d.stage})")
        to = {"pass": Stage.PASSED, "fail": Stage.FAILED, "unverified": Stage.RESEARCH}.get(outcome)
        if to is None:
            raise DossierError(f"unknown verdict outcome {outcome!r}")
        self._append(
            dossier_id,
            {
                "event": "verdict",
                "verdict_id": verdict_id,
                "candidate_id": d.submitted_candidate,
                "outcome": outcome,
            },
        )
        self._stage(dossier_id, Stage.SUBMITTED, to, f"verdict {verdict_id}: {outcome}")

    def rebrief(self, dossier_id: str, brief: str) -> None:
        """A new brief for a dossier that hasn't started (stage draft)."""
        d = self._open_dossier(dossier_id)
        if d.stage is not Stage.DRAFT:
            raise DossierError("only a draft can take a new brief: redirect opens a new dossier")
        self._append(dossier_id, {"event": "brief", "brief": brief})

    def pause(self, dossier_id: str, reason: str) -> None:
        if self._open_dossier(dossier_id).paused:
            raise DossierError(f"dossier {dossier_id} is already paused")
        self._append(dossier_id, {"event": "paused", "reason": reason})

    def resume(self, dossier_id: str) -> None:
        if not self._open_dossier(dossier_id).paused:
            raise DossierError(f"dossier {dossier_id} is not paused")
        self._append(dossier_id, {"event": "resumed"})

    def log_work(self, dossier_id: str, seconds: float) -> None:
        """Charge researcher time to the dossier (allowed after it closes: the step
        that closed it still took time)."""
        self._append(dossier_id, {"event": "work", "seconds": round(seconds, 3)})

    def note(self, dossier_id: str, text: str) -> None:
        self._open_dossier(dossier_id)
        self._append(dossier_id, {"event": "note", "text": text})

    def close(self, dossier_id: str, reason: str) -> None:
        d = self._open_dossier(dossier_id)
        self._append(dossier_id, {"event": "closed", "reason": reason})
        self._stage(dossier_id, d.stage, Stage.CLOSED, reason)

    # ------------------------------------------------------------- plumbing
    def _open_dossier(self, dossier_id: str) -> Dossier:
        d = self.get(dossier_id)
        if d.stage in FINAL:
            raise DossierError(f"dossier {dossier_id} is {d.stage.value}: no more changes")
        return d

    @staticmethod
    def _needs_hypothesis(d: Dossier) -> None:
        if d.hypothesis is None:
            raise DossierError("write the hypothesis and its rationale first")

    def _append(self, dossier_id: str, event: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"at": self.clock().isoformat()} | event
        with self.path(dossier_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def _stage(self, dossier_id: str, old: Stage | None, new: Stage, reason: str) -> None:
        self._append(dossier_id, {"event": "stage", "to": new.value})
        self.sink.emit(
            LedgerEventType.DOSSIER,
            f"dossier {dossier_id}: {old.value if old else 'new'} -> {new.value} ({reason})"[:300],
            {
                "dossier_id": dossier_id,
                "from": old.value if old else None,
                "to": new.value,
                "reason": reason[:300],
            },
        )


def _fold(events: list[dict[str, Any]]) -> Dossier:
    first = events[0]
    d = Dossier(
        dossier_id=first["dossier_id"],
        brief=first["brief"],
        market=first["market"],
        instruments=first["instruments"],
        timeframes=first["timeframes"],
        budget=DossierBudget.model_validate(first["budget"]),
        opened_at=first["at"],
        redirected_from=first.get("redirected_from"),
    )
    data = d.model_dump()
    for e in events[1:]:
        kind, at = e["event"], e["at"]
        if kind == "stage":
            data["stage"] = Stage(e["to"])
        elif kind == "hypothesis":
            data.update(hypothesis=e["text"], rationale=e["why"], hypothesis_at=at)
        elif kind == "code":
            data["code"].append({"sha256": e["sha256"], "file": e["file"], "at": at})
        elif kind == "trial":
            data["trial_ids"].append(e["trial_id"])
        elif kind == "submitted":
            data["submitted_candidate"] = e["candidate_id"]
        elif kind == "verdict":
            data["verdicts"].append(
                {
                    "verdict_id": e["verdict_id"],
                    "candidate_id": e["candidate_id"],
                    "outcome": e["outcome"],
                    "at": at,
                }
            )
        elif kind == "note":
            data["notes"].append({"at": at, "text": e["text"]})
        elif kind == "closed":
            data["closed_reason"] = e["reason"]
        elif kind == "brief":
            data["brief"] = e["brief"]
        elif kind == "paused":
            data["paused"] = True
        elif kind == "resumed":
            data["paused"] = False
        elif kind == "work":
            data["work_seconds"] += e["seconds"]
    return Dossier.model_validate(data)


class Brief(FirmModel):
    at: AwareDatetime
    text: str


class FloorBriefs:
    """The owner's standing brief for the whole floor (e.g. "stay uncorrelated
    with X"): an append-only log, the last one counts, an empty one clears it."""

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.path = Path(root) / "research" / "floor_briefs.jsonl"
        self.clock = clock

    def set(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": self.clock().isoformat(), "text": text.strip()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def current(self) -> Brief | None:
        if not self.path.is_file():
            return None
        lines = [json.loads(x) for x in self.path.read_text().splitlines() if x]
        last = lines[-1] if lines else None
        return Brief.model_validate(last) if last and last["text"] else None
