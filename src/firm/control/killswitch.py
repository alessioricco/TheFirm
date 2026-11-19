"""The owner's kill switch: issue it, see it, wait for the risk desk to apply it.

Per market or global (SPEC "Kill switches"). An owner command (KILL, or
RESUME, the only way to release one) goes:

1. **into the ledger first**, as an OWNER_COMMAND with where it came from
   (`cli`, `dashboard`); the paper run's risk desk replays these at start,
   so a kill survives a restart and one sent while the run was down still
   applies;
2. **onto the bus**, `control.command`, which only the control service may
   publish; the running paper run's risk desk applies it at once (durable
   consumer `risk-commands`) and records the halt (RISK_HALT) or the
   release (OWNER_COMMAND, `cmd:<id>`), which raises the owner's alert.

`wait_applied` watches the ledger for that record: "confirmed" means the
risk desk itself acted on it. A halt stops new buying on that market (or
every market); a sell that reduces a position still goes through, and
nothing already filled is undone.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firm.core.domain import LedgerEventType
from firm.core.messages.subjects import HaltScope, OwnerCommand, OwnerCommandKind
from firm.ledger.store import Ledger

Publish = Callable[[OwnerCommand], Awaitable[None]]


def new_command(kind: str, *, market: str | None, reason: str) -> OwnerCommand:
    """`market=None` means every market (global)."""
    if not reason.strip():
        raise ValueError("a reason is required (it is recorded)")
    return OwnerCommand(
        command_id=f"k-{uuid.uuid4().hex[:16]}",
        kind=OwnerCommandKind(kind),
        scope=HaltScope.GLOBAL if market is None else HaltScope.MARKET,
        market=market,
        reason=reason.strip(),
        issued_at=datetime.now(UTC),
    )


async def issue(ledger: Ledger, cmd: OwnerCommand, *, publish: Publish | None, via: str) -> bool:
    """Record the command, then publish it. True if the bus took it; False if it
    is only recorded (no bus, or the bus is down): it applies at the next start."""
    where = "all markets" if cmd.scope is HaltScope.GLOBAL else cmd.market
    ledger.append(
        LedgerEventType.OWNER_COMMAND,
        f"owner {cmd.kind.value} ({where}) via {via}: {cmd.reason}"[:300],
        cmd.model_dump(mode="json") | {"via": via, "requested": True},
        event_id=f"request:{cmd.command_id}",
    )
    if publish is None:
        return False
    try:
        await publish(cmd)
    except Exception:
        return False
    return True


def owner_commands(ledger: Ledger) -> list[OwnerCommand]:
    """Every kill and resume the owner issued, oldest first, once each."""
    seen: set[str] = set()
    out: list[OwnerCommand] = []
    for e in ledger.read():
        p = e.payload
        if e.event_type is not LedgerEventType.OWNER_COMMAND or p.get("kind") not in (
            "kill",
            "resume",
        ):
            continue
        if "command_id" not in p or p["command_id"] in seen:
            continue
        seen.add(p["command_id"])
        out.append(
            OwnerCommand.model_validate({k: p[k] for k in OwnerCommand.model_fields if k in p})
        )
    return out


def kill_state(ledger: Ledger) -> dict[str, Any]:
    """Which kill switches are on: the latest command per scope wins."""
    active: dict[str | None, dict[str, Any]] = {}
    for cmd in owner_commands(ledger):
        key = None if cmd.scope is HaltScope.GLOBAL else cmd.market
        if cmd.kind is OwnerCommandKind.KILL:
            active[key] = {
                "command_id": cmd.command_id,
                "reason": cmd.reason,
                "issued_at": cmd.issued_at.isoformat(),
            }
        else:
            active.pop(key, None)
    return {
        "global": active.get(None),
        "markets": {k: v for k, v in active.items() if k is not None},
    }


def applied(ledger: Ledger, cmd: OwnerCommand) -> bool:
    """Has the risk desk recorded acting on this command?"""
    for e in ledger.read():
        p = e.payload
        if cmd.kind is OwnerCommandKind.RESUME:
            if e.event_id == f"cmd:{cmd.command_id}":
                return True
        elif (
            e.event_type is LedgerEventType.RISK_HALT
            and p.get("reason") == f"owner kill: {cmd.reason}"
            and p.get("scope") == cmd.scope.value
            and p.get("market") == cmd.market
        ):
            return True
    return False


async def wait_applied(ledger: Ledger, cmd: OwnerCommand, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if applied(ledger, cmd):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.25)


def bus_publisher(config_root: Path, state_root: Path) -> Publish:
    """Publish as the control service on the local bus (raises if it isn't up)."""

    async def publish(cmd: OwnerCommand) -> None:
        from firm.bus.client import BusClient
        from firm.bus.nats import NatsBus
        from firm.core.config import load_bus
        from firm.core.messages import Service, Subject

        cfg = load_bus(config_root)
        transport = await NatsBus.for_service(cfg, state_root, Service.CONTROL)
        try:
            client = BusClient(transport, Service.CONTROL)
            await client.publish(Subject.CONTROL_COMMAND, cmd, message_id=cmd.command_id)
        finally:
            await transport.close()

    return publish
