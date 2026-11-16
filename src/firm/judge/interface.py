"""Judge interface. Implemented in milestone 5."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import JudgeCall


class Judge(Protocol):
    def ask(self, call: JudgeCall) -> JudgeCall:
        """Return the same call with typed `answers` filled in."""
        ...
