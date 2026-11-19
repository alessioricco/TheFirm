"""`firm kill` and `firm resume`: the owner's kill switch from a terminal.

The same as the dashboard's Kill switch: recorded in the ledger, sent to the
running paper run's risk desk over the bus, and confirmed when the desk
records the halt (or the release). `--confirm` is required: a kill is never
one stray keystroke away, and neither is undoing one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

EXIT_OK, EXIT_NOT_CONFIRMED, EXIT_ERROR = 0, 1, 3


def _run(args: argparse.Namespace, kind: str) -> int:
    from firm.control.killswitch import bus_publisher, issue, kill_state, new_command, wait_applied
    from firm.ledger.sink import ledger_path
    from firm.ledger.store import Ledger
    from firm.verify.state import state_root

    where = "every market" if args.all else args.market
    verb = "kill" if kind == "kill" else "resume"
    if not args.confirm:
        print(
            f"not done: add --confirm to {verb} {where}"
            + (" (no new buying until `firm resume`)" if kind == "kill" else ""),
            file=sys.stderr,
        )
        return EXIT_ERROR
    state = state_root(args.state_root)
    ledger = Ledger(ledger_path(state))
    current = kill_state(ledger)
    on = current["global"] if args.all else current["markets"].get(args.market)
    if kind == "resume" and on is None:
        print(f"no kill switch is on for {where}: nothing to resume", file=sys.stderr)
        return EXIT_ERROR
    cmd = new_command(kind, market=None if args.all else args.market, reason=args.reason)
    publish = None if args.no_bus else bus_publisher(Path(args.config), state)

    async def go() -> tuple[bool, bool]:
        sent = await issue(ledger, cmd, publish=publish, via="cli")
        done = await wait_applied(ledger, cmd, args.wait) if sent else False
        return sent, done

    sent, done = asyncio.run(go())
    past = "killed" if kind == "kill" else "resumed"
    print(f"{verb} {where}: recorded in the ledger ({cmd.command_id})")
    if done:
        print(f"confirmed: the risk desk has {past} {where}")
        return EXIT_OK
    if sent:
        print(
            f"sent on the bus, but the risk desk hasn't confirmed within {args.wait:g}s: "
            "is the paper run running? It applies it as soon as it reads the bus, and at its "
            "next start in any case."
        )
    else:
        print(
            "not sent: the bus isn't running (or --no-bus). It applies when the paper run "
            "next starts. To stop a running run now: Ctrl-C in its terminal, or "
            "`systemctl --user stop firm-paper@<name>`."
        )
    return EXIT_NOT_CONFIRMED


def add_kill_parsers(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    for name, kind, text in (
        ("kill", "kill", "kill switch: no new buying on a market (or every market)"),
        ("resume", "resume", "release a kill switch"),
    ):
        p = sub.add_parser(name, help=text)
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument("--market", help="market profile, e.g. crypto")
        target.add_argument("--all", action="store_true", help="every market (global)")
        p.add_argument("--reason", required=True, help="why (recorded in the ledger)")
        p.add_argument("--confirm", action="store_true", help="required: do it")
        p.add_argument(
            "--wait",
            type=float,
            default=10.0,
            help="seconds to wait for the risk desk to confirm (default 10)",
        )
        p.add_argument(
            "--no-bus",
            action="store_true",
            help="only record it (applies at the paper run's next start)",
        )
        p.add_argument("--config", default="config", help="config root (bus.yaml)")
        p.add_argument(
            "--state-root",
            help="the paper run's state and ledger (default $FIRM_STATE_ROOT or ./state)",
        )
        p.set_defaults(func=lambda a, k=kind: _run(a, k))
