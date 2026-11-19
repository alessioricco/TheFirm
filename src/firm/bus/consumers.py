"""Who reads what on the bus: every durable consumer, declared once.

The admin creates these at `firm bus up` (`NatsBus.ensure_consumers`); a
service can bind only to its own declared consumers and can't create any, so
no desk can squat another's consumer or take its deliveries. Each consumer
delivers to `_INBOX.<service>.c.<name>`, under its service's inbox prefix,
and only that service may acknowledge its messages.

`start` is where a consumer begins the first time it is created: `all` (the
whole stream) or `new` (only messages published from then on). After that,
a durable consumer always resumes after its last acknowledged message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from firm.core.messages import Service, Subject


@dataclass(frozen=True)
class Consumer:
    name: str  # also the JetStream durable name: one token, no dots
    service: Service
    filter: str  # a subject or a wildcard pattern
    start: Literal["all", "new"] = "all"

    def __post_init__(self) -> None:
        if not self.name or any(c in self.name for c in ". *>"):
            raise ValueError(f"consumer name {self.name!r} must be one token")

    @property
    def deliver_subject(self) -> str:
        return f"_INBOX.{self.service.value}.c.{self.name}"


CONSUMERS: dict[str, Consumer] = {
    c.name: c
    for c in (
        # risk answers every order proposal with a decision
        Consumer("risk-proposals", Service.RISK, Subject.ORDER_PROPOSED.value, "new"),
        # execution waits for the decision on its proposal...
        Consumer("execution-decisions", Service.EXECUTION, Subject.RISK_DECISION.value, "new"),
        # ...and stops ordering when risk's heartbeat stops (golden rule 12)
        Consumer("execution-heartbeat", Service.EXECUTION, Subject.RISK_HEARTBEAT.value, "new"),
        # the notifier delivers every alert to the owner
        Consumer("notifier-alerts", Service.NOTIFIER, "alert.>", "new"),
    )
}
"""Every durable consumer on the firm's stream."""


def consumers_of(service: Service) -> list[Consumer]:
    return [c for c in CONSUMERS.values() if c.service is service]
