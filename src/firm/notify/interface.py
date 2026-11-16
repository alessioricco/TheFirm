"""Notifier interface. Implemented in milestone 4."""

from __future__ import annotations

from typing import Protocol

from firm.core.domain import Notification


class Notifier(Protocol):
    def send(self, notification: Notification) -> None:
        """Deliver to the owner. There is no way to name another recipient."""
        ...
