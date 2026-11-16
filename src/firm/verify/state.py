"""Append-only verifier state: the global trial registry and the OOS vault.

Both are JSON-lines files under `$FIRM_STATE_ROOT` (default `./state`,
git-ignored). Nothing is ever rewritten; they move into the ledger in
milestone 2.

- Trial registry (firm rule 5): every trial, across all experiments, is
  counted once (idempotent on `trial_id`). The verifier corrects for the
  global count, so more searching raises the bar.
- OOS vault (firm rule 4): the final out-of-sample period is opened once per
  strategy, by the verifier only, and every attempt is logged. Re-verifying
  the identical candidate is a *replay* (nothing new is learnt) and must
  reproduce the sealed result; any other version of the same strategy is
  refused.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from firm.core.domain import TrialRecord

STATE_ROOT_ENV = "FIRM_STATE_ROOT"


def state_root(explicit: Path | str | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(STATE_ROOT_ENV)
    return Path(env) if env else Path.cwd() / "state"


STATE_SCHEMA_VERSION = 1
"""Version of the state files' layout. Bump on any incompatible change; old
state is then refused instead of misread (migrate it, or start a new root)."""


class StateVersionError(RuntimeError):
    """The state directory was written by an incompatible version."""


def check_state_version(root: Path, *, create: bool) -> None:
    marker = root / "STATE_VERSION"
    if marker.is_file():
        found = marker.read_text().strip()
        if found != str(STATE_SCHEMA_VERSION):
            raise StateVersionError(
                f"{root}: state schema {found}, this code expects {STATE_SCHEMA_VERSION}"
            )
    elif create:
        root.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{STATE_SCHEMA_VERSION}\n")


def _append(path: Path, record: dict[str, Any]) -> None:
    check_state_version(path.parent, create=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    check_state_version(path.parent, create=False)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TrialRegistry:
    def __init__(self, root: Path) -> None:
        self.path = root / "trials.jsonl"

    def record(self, trial: TrialRecord) -> bool:
        """Count a trial. Returns False if it was already counted."""
        if trial.trial_id in self.ids():
            return False
        _append(self.path, trial.model_dump(mode="json"))
        return True

    def all(self) -> list[TrialRecord]:
        return [TrialRecord.model_validate(r) for r in _read(self.path)]

    def ids(self) -> set[str]:
        return {r["trial_id"] for r in _read(self.path)}

    def count(self) -> int:
        return len(self.ids())

    def sharpes(self) -> list[float]:
        return [t.metrics["sharpe_per_bar"] for t in self.all() if "sharpe_per_bar" in t.metrics]


Access = Literal["first_opening", "replay", "refused"]


@dataclass(frozen=True)
class Opening:
    access: Access
    sealed_hash: str | None  # for replays: the result the first opening produced


class OosVault:
    def __init__(self, root: Path) -> None:
        self.path = root / "oos_openings.jsonl"

    def events(self) -> list[dict[str, Any]]:
        return _read(self.path)

    def request(
        self,
        *,
        strategy_id: str,
        candidate_key: str,
        dataset_version: str,
        window: list[str],
        at: datetime,
    ) -> Opening:
        """Log an opening attempt and say whether the OOS data may be used."""
        firsts = [
            e
            for e in self.events()
            if e["event"] == "first_opening" and e["strategy_id"] == strategy_id
        ]
        if not firsts:
            access: Access = "first_opening"
            sealed = None
        elif firsts[0]["candidate_key"] == candidate_key:
            access = "replay"
            seals = [
                e
                for e in self.events()
                if e["event"] == "sealed" and e["candidate_key"] == candidate_key
            ]
            sealed = seals[0]["result_sha256"] if seals else None
        else:
            access, sealed = "refused", None
        _append(
            self.path,
            {
                "event": access,
                "strategy_id": strategy_id,
                "candidate_key": candidate_key,
                "dataset_version": dataset_version,
                "window": window,
                "at": at.isoformat(),
            },
        )
        return Opening(access, sealed)

    def seal(
        self, *, strategy_id: str, candidate_key: str, result_sha256: str, at: datetime
    ) -> None:
        _append(
            self.path,
            {
                "event": "sealed",
                "strategy_id": strategy_id,
                "candidate_key": candidate_key,
                "result_sha256": result_sha256,
                "at": at.isoformat(),
            },
        )


def state_summary(root: Path) -> dict[str, Any]:
    """Read-only overview of the verifier state (for `firm state show`)."""
    check_state_version(root, create=False)
    trials = TrialRegistry(root).all()
    by_experiment: dict[str, int] = {}
    for t in trials:
        by_experiment[t.experiment_id] = by_experiment.get(t.experiment_id, 0) + 1
    vault: dict[str, dict[str, int]] = {}
    for e in OosVault(root).events():
        counts = vault.setdefault(e["strategy_id"], {})
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    reports = []
    for path in sorted((root / "reports").glob("v-*.json")):
        body = json.loads(path.read_text())
        reports.append(
            {
                "verdict_id": body.get("verdict_id", path.stem),
                "outcome": body.get("outcome"),
                "strategy_id": body.get("candidate", {}).get("strategy_id"),
                "dataset_version": body.get("data", {}).get("dataset_version"),
            }
        )
    marker = root / "STATE_VERSION"
    return {
        "root": str(root),
        "schema_version": marker.read_text().strip() if marker.is_file() else None,
        "trials_total": len(trials),
        "trials_by_experiment": by_experiment,
        "oos_vault": vault,
        "reports": reports,
    }
