"""Verifier interface. Implemented in milestone 1."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import CheckResult, StrategyCandidate, Verdict


class Check(Protocol):
    """One verifier check. Deterministic: same inputs, same result."""

    name: str

    def run(self, candidate: StrategyCandidate, dataset_version: str) -> CheckResult: ...


class Verifier(Protocol):
    def verify(self, candidate: StrategyCandidate, dataset_version: str) -> Verdict: ...
