"""Append-only verifier state: the global trial registry and the OOS vault.

Both are JSON-lines files under `$FIRM_STATE_ROOT` (default `./state`,
git-ignored). Nothing is ever rewritten; they move into the ledger in
milestone 2.

- Trial registry (firm rule 5): every trial, across all experiments, is
  counted once (idempotent on `trial_id`). The verifier corrects for the
  global count, so more searching raises the bar.
- OOS vault (firm rule 4): the final out-of-sample period is opened once per
  strategy, instrument and timeframe (the *scope*), by the verifier only, and
  every attempt is logged. Re-verifying the identical candidate is a *replay*
  (nothing new is learnt) and must reproduce the sealed result; any other
  version in the same scope is refused. Results are sealed per verifier
  configuration (`config_key`: costs + thresholds), so changing fees or
  thresholds gives an unchanged strategy a new seal instead of a false
  "does not reproduce". Each first opening is also recorded
  as a trial, so trying a strategy on many instruments raises the bar.
  Events written before scopes existed have no `scope` and keep blocking the
  whole strategy (the strict reading).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from firm.core.domain import LedgerEventType, TrialRecord
from firm.core.events import NULL_SINK, EventSink

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
    """Global trial count. Trial IDs recorded under an old scheme can be linked to
    their new ID by an append-only alias (`trial_aliases.jsonl`); every trial is
    counted once under its canonical ID."""

    def __init__(self, root: Path, *, sink: EventSink = NULL_SINK) -> None:
        self.path = root / "trials.jsonl"
        self.alias_path = root / "trial_aliases.jsonl"
        self.sink = sink

    def _aliases(self) -> dict[str, str]:
        return {a["from"]: a["to"] for a in _read(self.alias_path)}

    def alias(self, old_id: str, new_id: str) -> None:
        """Link a legacy trial ID to its canonical ID (idempotent)."""
        if self._aliases().get(old_id) != new_id:
            record = {"from": old_id, "to": new_id}
            _append(self.alias_path, record)
            emit_trial_alias(self.sink, record)

    def record(self, trial: TrialRecord) -> bool:
        """Count a trial. Returns False if it was already counted."""
        if trial.trial_id in self.ids():
            return False
        record = trial.model_dump(mode="json")
        _append(self.path, record)
        emit_trial(self.sink, record)
        return True

    def all(self) -> list[TrialRecord]:
        return [TrialRecord.model_validate(r) for r in _read(self.path)]

    def raw_ids(self) -> set[str]:
        return {r["trial_id"] for r in _read(self.path)}

    def ids(self) -> set[str]:
        aliases = self._aliases()
        return {aliases.get(i, i) for i in self.raw_ids()}

    def count(self) -> int:
        return len(self.ids())

    def sharpes(self) -> list[float]:
        return [t.metrics["sharpe_per_bar"] for t in self.all() if "sharpe_per_bar" in t.metrics]


Access = Literal["first_opening", "replay", "refused"]


def oos_scope(strategy_id: str, instruments: tuple[str, ...] | list[str], timeframe: str) -> str:
    """The unit the hidden period is opened for: strategy | instrument(s) | timeframe."""
    return f"{strategy_id}|{'+'.join(instruments)}|{timeframe}"


def opening_trial_id(scope: str, candidate_key: str) -> str:
    """Deterministic trial ID of an OOS first opening (idempotent in the registry)."""
    return "oos-" + hashlib.sha256(f"{scope}|{candidate_key}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Opening:
    access: Access
    sealed_hash: str | None  # for replays: the result the first opening produced


class OosVault:
    def __init__(self, root: Path, *, sink: EventSink = NULL_SINK) -> None:
        self.path = root / "oos_openings.jsonl"
        self.sink = sink

    def _log(self, record: dict[str, Any]) -> None:
        _append(self.path, record)
        emit_vault_event(self.sink, record)

    def events(self) -> list[dict[str, Any]]:
        return _read(self.path)

    def request(
        self,
        *,
        strategy_id: str,
        scope: str,
        candidate_key: str,
        dataset_version: str,
        window: list[str],
        at: datetime,
        config_key: str = "",
        legacy_keys: tuple[str, ...] = (),
    ) -> Opening:
        """Log an opening attempt and say whether the OOS data may be used.

        Candidate keys are compared after resolving `alias` events, and a
        first opening recorded under one of `legacy_keys` (the pre-content
        key of the same candidate) is linked to `candidate_key` by a new
        `alias` event: append-only, nothing is rewritten."""
        events = self.events()
        aliases = {e["from"]: e["to"] for e in events if e["event"] == "alias"}

        def canon(key: str) -> str:
            return aliases.get(key, key)

        firsts = [
            e
            for e in events
            if e["event"] == "first_opening"
            and e["strategy_id"] == strategy_id
            and e.get("scope", scope) == scope  # no scope = legacy: whole strategy
        ]
        sealed: str | None = None
        if not firsts:
            access: Access = "first_opening"
        else:
            opened = firsts[0]["candidate_key"]
            if canon(opened) != candidate_key and opened in legacy_keys:
                self._log(
                    {
                        "event": "alias",
                        "strategy_id": strategy_id,
                        "scope": scope,
                        "from": opened,
                        "to": candidate_key,
                        "at": at.isoformat(),
                    }
                )
                aliases[opened] = candidate_key
            if canon(opened) == candidate_key:
                access = "replay"
                seals = [
                    e
                    for e in events
                    if e["event"] == "sealed"
                    and canon(e["candidate_key"]) == candidate_key
                    and e.get("scope", scope) == scope
                    and e.get("config_key", "") == config_key
                ]
                sealed = seals[0]["result_sha256"] if seals else None
            else:
                access = "refused"
        self._log(
            {
                "event": access,
                "strategy_id": strategy_id,
                "scope": scope,
                "candidate_key": candidate_key,
                "config_key": config_key,
                "dataset_version": dataset_version,
                "window": window,
                "at": at.isoformat(),
            },
        )
        return Opening(access, sealed)

    def seal(
        self,
        *,
        strategy_id: str,
        scope: str,
        candidate_key: str,
        result_sha256: str,
        at: datetime,
        config_key: str = "",
    ) -> None:
        self._log(
            {
                "event": "sealed",
                "strategy_id": strategy_id,
                "scope": scope,
                "candidate_key": candidate_key,
                "config_key": config_key,
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


# --------------------------------------------------------------------------- ledger mirror
# Deterministic event IDs: emitting the same record twice (replay, import) is a
# no-op in the ledger.


def _digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:32]


def emit_trial(sink: EventSink, record: dict[str, Any]) -> None:
    sink.emit(
        LedgerEventType.TRIAL,
        f"trial {record['trial_id']} counted",
        record,
        event_id=f"trial:{record['trial_id']}",
    )


def emit_trial_alias(sink: EventSink, record: dict[str, Any]) -> None:
    sink.emit(
        LedgerEventType.TRIAL_ALIAS,
        f"trial {record['from']} is {record['to']}",
        record,
        event_id=f"trial_alias:{record['from']}",
    )


def emit_vault_event(sink: EventSink, record: dict[str, Any]) -> None:
    sink.emit(
        LedgerEventType.OOS_VAULT,
        f"oos {record['event']} for {record.get('scope', record['strategy_id'])}",
        record,
        event_id=f"oos:{_digest(record)}",
    )
