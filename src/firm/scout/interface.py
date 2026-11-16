"""Scout interface."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import OpportunityNote


class Scout(Protocol):
    def scan(self) -> list[OpportunityNote]: ...
