"""The model gateway: the data-egress gate in code (firm rule 8).

Rules, in order, before any provider is called:

1. The provider must have an adapter.
2. Work tagged `private` (strategy code, parameters, results, ledger) runs
   on a local provider (`egress.local_providers`). A cloud provider needs
   the owner's opt-in for that run (`OwnerOptIn`), and is refused outright
   when the policy is `never`.
3. A cloud call needs budget left: this calendar month's cloud spend (from
   the ledger, plus calls made by this gateway) must be below
   `egress.cloud_spend_budget_gbp` (default 0, so no cloud call at all).
   The cost of a call is known only afterwards, so the last call of a month
   can overshoot; the next one is refused.

Every call is one MODEL_CALL ledger event: outcome (`ok`, `refused`,
`error`), role, provider, model, local or not, tag, opt-in run, tokens,
cost, duration, and a SHA-256 of the messages. Never the messages: they
may be private, and the ledger is read by more than the model.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from firm.core.config import ModelsConfig
from firm.core.domain import DataTag, LedgerEventType
from firm.core.events import NULL_SINK, EventSink

Message = Mapping[str, str]


class EgressRefused(PermissionError):
    """The egress gate refused the call; nothing was sent."""


@dataclass(frozen=True)
class Target:
    """A model on a provider: a role's model from config, or an explicit one
    (e.g. a step on a model ladder)."""

    provider: str
    model: str


@dataclass(frozen=True)
class OwnerOptIn:
    """The owner's permission for one run to send private work to a cloud model.
    Created from an owner command, never by an agent; logged with every call."""

    run_id: str
    reason: str


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    cost_gbp: float  # 0 for local models


class Provider(Protocol):
    local: bool

    def chat(self, model: str, messages: Sequence[Message], *, timeout: float) -> Completion: ...


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class ModelGateway:
    def __init__(
        self,
        config: ModelsConfig,
        providers: Mapping[str, Provider],
        *,
        sink: EventSink = NULL_SINK,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        spent_this_month: Callable[[datetime], float],
        timeout: float = 600.0,
    ) -> None:
        """`spent_this_month(now)`: cloud spend (GBP) already recorded in the
        ledger for `now`'s calendar month, before this gateway started."""
        self.config, self.providers = config, dict(providers)
        self.sink, self.clock, self.timeout = sink, clock, timeout
        self._spent_before = spent_this_month
        self._spent_here: dict[datetime, float] = {}

    def chat(
        self,
        *,
        role: str,
        messages: Sequence[Message],
        tag: DataTag,
        target: Target | None = None,
        opt_in: OwnerOptIn | None = None,
        dossier_id: str | None = None,
    ) -> Completion:
        """`dossier_id`: the research dossier the call works for; its tokens and cost
        count against that dossier's budget (read back from the ledger)."""
        if not isinstance(tag, DataTag):
            raise TypeError("tag must be a DataTag (public or private)")
        if target is None:
            ref = self.config.roles[role]  # type: ignore[index]
            target = Target(ref.provider, ref.model)
        now = self.clock()
        local = target.provider in self.config.egress.local_providers
        record: dict[str, Any] = {
            "role": role,
            "provider": target.provider,
            "model": target.model,
            "local": local,
            "tag": tag.value,
            "opt_in_run": opt_in.run_id if opt_in else None,
            "opt_in_reason": opt_in.reason if opt_in else None,
            "dossier_id": dossier_id,
            "messages_sha256": hashlib.sha256(
                json.dumps([dict(m) for m in messages], sort_keys=True).encode()
            ).hexdigest(),
            "at": now.isoformat(),
        }
        refusal = self._refusal(target, local, tag, opt_in, now)
        if refusal:
            self._log("refused", record | {"refusal": refusal}, f"model call refused: {refusal}")
            raise EgressRefused(refusal)
        started = time.monotonic()
        try:
            out = self.providers[target.provider].chat(target.model, messages, timeout=self.timeout)
        except Exception as exc:
            took = round(time.monotonic() - started, 3)
            self._log(
                "error",
                record | {"error": f"{type(exc).__name__}: {exc}"[:300], "seconds": took},
                f"model call failed: {target.provider}/{target.model}",
            )
            raise
        if not local:
            key = month_start(now)
            self._spent_here[key] = self._spent_here.get(key, 0.0) + out.cost_gbp
        self._log(
            "ok",
            record
            | {
                "input_tokens": out.input_tokens,
                "output_tokens": out.output_tokens,
                "cost_gbp": out.cost_gbp,
                "seconds": round(time.monotonic() - started, 3),
            },
            f"model call: {role} on {target.provider}/{target.model} ({tag.value})",
        )
        return out

    def spent_this_month(self, now: datetime) -> float:
        return self._spent_before(now) + self._spent_here.get(month_start(now), 0.0)

    def _refusal(
        self,
        target: Target,
        local: bool,
        tag: DataTag,
        opt_in: OwnerOptIn | None,
        now: datetime,
    ) -> str | None:
        egress = self.config.egress
        if target.provider not in self.providers:
            return f"no adapter for provider {target.provider!r}"
        if local:
            return None
        if tag is DataTag.PRIVATE:
            if egress.private_to_cloud == "never":
                return "private work never goes to a cloud model (policy: never)"
            if opt_in is None:
                return "private work goes to a cloud model only with the owner's per-run opt-in"
        spent, budget = self.spent_this_month(now), egress.cloud_spend_budget_gbp
        if spent >= budget:
            return f"cloud budget reached: £{spent:.2f} of £{budget:.2f} this month"
        return None

    def _log(self, outcome: str, payload: dict[str, Any], reason: str) -> None:
        self.sink.emit(LedgerEventType.MODEL_CALL, reason, {"outcome": outcome} | payload)
