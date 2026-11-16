"""Research desk interfaces. Implemented in milestone 3."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import StrategyCandidate, TrialRecord


class TrialCounter(Protocol):
    """Global trial count across all experiments (firm rule 5)."""

    def record(self, trial: TrialRecord) -> int: ...

    def total(self) -> int: ...


class ExperimentManager(Protocol):
    def submit(self, experiment: object) -> str: ...

    def candidates(self, experiment_id: str) -> list[StrategyCandidate]: ...
