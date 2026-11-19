"""`firm research ...`: the owner's commands for the research floor (M3, M2).

They manage dossiers (start, pause, resume, redirect, close), the floor
brief, and show the floor (who runs, who waits, budgets used). Every change
is an OWNER_COMMAND ledger event; stage changes are DOSSIER events too.
Running researchers comes with the researcher agent (M4): until then no
command here starts a model or a backtest.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _floor(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from firm.control.research import ledger_model_usage
    from firm.core.config import load_research_config
    from firm.data.store import data_root
    from firm.ledger.sink import LedgerSink, ledger_path
    from firm.ledger.store import Ledger
    from firm.research.dossier import DossierStore, FloorBriefs
    from firm.research.manager import ResearchManager
    from firm.verify.state import state_root

    def not_yet(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("researchers run with the researcher agent (milestone 3, M4)")

    ledger = Ledger(ledger_path(state_root(args.state_root)))
    root = data_root(args.root)
    store = DossierStore(root, sink=LedgerSink(ledger))
    manager = ResearchManager(
        load_research_config(Path(args.config)),
        store,
        backtest=not_yet,
        verify=not_yet,
        model_usage=ledger_model_usage(ledger),
        floor_briefs=FloorBriefs(root),
    )
    return manager, ledger


def _owner(ledger, command: str, payload: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    from firm.core.domain import LedgerEventType

    ledger.append(
        LedgerEventType.OWNER_COMMAND,
        f"owner: research {command}"[:300],
        {"command": f"research {command}", "at": datetime.now(UTC).isoformat()} | payload,
    )


def _brief_text(args: argparse.Namespace) -> str:
    if getattr(args, "brief_file", None):
        return Path(args.brief_file).read_text().strip()
    return (args.brief or "").strip()


def _place(manager, dossier_id: str) -> str:  # type: ignore[no-untyped-def]
    running = [d.dossier_id for d in manager.running()]
    waiting = [d.dossier_id for d in manager.waiting()]
    if dossier_id in running:
        return "running"
    if dossier_id in waiting:
        return f"waiting ({waiting.index(dossier_id) + 1} in the queue)"
    return "-"


def _usage_line(manager, d) -> str:  # type: ignore[no-untyped-def]
    u, b = manager.usage(d), d.budget
    return (
        f"trials {u.trials}/{b.trials}  hours {u.hours:.2f}/{b.wall_clock_hours:g}  "
        f"tokens {u.tokens}/{b.tokens}  cloud £{u.cloud_gbp:.2f}/£{b.cloud_spend_gbp:.2f}"
    )


def cmd_start(args: argparse.Namespace) -> int:
    brief = _brief_text(args)
    if not brief:
        print("a brief is required: --brief TEXT or --brief-file PATH", file=sys.stderr)
        return 3
    manager, ledger = _floor(args)
    timeframes = [t for spec in args.tf for t in spec.split(",") if t]
    d = manager.start(
        brief=brief,
        market=args.market,
        instruments=args.instrument,
        timeframes=timeframes,
        dossier_id=args.id,
    )
    _owner(ledger, "start", {"dossier_id": d.dossier_id})
    print(f"{d.dossier_id}  draft  {_place(manager, d.dossier_id)}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    manager, _ = _floor(args)
    dossiers = sorted(manager.store.list(), key=lambda d: (d.opened_at, d.dossier_id))
    if not dossiers:
        print("no dossiers yet: `firm research start --brief ...`")
        return 0
    cfg = manager.config
    print(f"researchers at once: {cfg.researchers}")
    for d in dossiers:
        place = "paused" if d.paused else _place(manager, d.dossier_id)
        print(f"{d.dossier_id}  {d.stage.value:<10} {place}")
        print(f"    {_usage_line(manager, d)}")
        print(f"    brief: {d.brief[:100]}")
    brief = manager.floor_briefs.current()
    if brief:
        print(f"floor brief: {brief.text[:200]}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    manager, _ = _floor(args)
    d = manager.store.get(args.dossier)
    place = "paused" if d.paused else _place(manager, d.dossier_id)
    lines = [
        f"{d.dossier_id}  {d.stage.value}  {place}",
        f"opened {d.opened_at:%Y-%m-%d %H:%M} UTC"
        + (f", redirected from {d.redirected_from}" if d.redirected_from else ""),
        f"market {d.market}  instruments {', '.join(d.instruments)}  "
        f"timeframes {', '.join(d.timeframes)}",
        f"brief: {d.brief}",
        f"budget used: {_usage_line(manager, d)}",
        f"hypothesis: {d.hypothesis or '(not written yet)'}",
    ]
    if d.rationale:
        lines.append(f"why: {d.rationale}")
    lines += [f"code: {c.file} ({c.sha256[:12]})" for c in d.code]
    lines.append(f"trials: {len(d.trial_ids)}")
    lines += [f"verdict: {v.verdict_id} {v.outcome} ({v.candidate_id})" for v in d.verdicts]
    lines += [f"note {n.at:%Y-%m-%d %H:%M}: {n.text}" for n in d.notes]
    if d.closed_reason:
        lines.append(f"closed: {d.closed_reason}")
    print("\n".join(lines))
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    manager, ledger = _floor(args)
    manager.store.pause(args.dossier, args.reason or "owner")
    _owner(ledger, "pause", {"dossier_id": args.dossier, "reason": args.reason})
    print(f"{args.dossier} paused: it won't run and its place goes to the next in the queue")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    manager, ledger = _floor(args)
    manager.store.resume(args.dossier)
    _owner(ledger, "resume", {"dossier_id": args.dossier})
    print(f"{args.dossier} resumed: {_place(manager, args.dossier)}")
    return 0


def cmd_redirect(args: argparse.Namespace) -> int:
    brief = _brief_text(args)
    if not brief:
        print("a new brief is required: --brief TEXT or --brief-file PATH", file=sys.stderr)
        return 3
    manager, ledger = _floor(args)
    new = manager.redirect(args.dossier, brief)
    _owner(ledger, "redirect", {"dossier_id": args.dossier, "new_dossier_id": new.dossier_id})
    if new.dossier_id == args.dossier:
        print(f"{args.dossier}: new brief (it hadn't started)")
    else:
        print(f"{args.dossier} closed; {new.dossier_id} opened with the new brief")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    manager, ledger = _floor(args)
    manager.store.close(args.dossier, f"owner: {args.reason}")
    _owner(ledger, "close", {"dossier_id": args.dossier, "reason": args.reason})
    print(f"{args.dossier} closed")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    manager, ledger = _floor(args)
    briefs = manager.floor_briefs
    if args.clear or args.text is not None or args.file:
        text = "" if args.clear else (Path(args.file).read_text() if args.file else args.text)
        briefs.set(text)
        _owner(ledger, "brief", {"cleared": not text.strip()})
    current = briefs.current()
    print(f"floor brief: {current.text}" if current else "no floor brief")
    return 0


def add_research_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    rs = sub.add_parser(
        "research", help="the research floor: dossiers, briefs, budgets"
    ).add_subparsers(dest="research_cmd")

    def common(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--config", default="config", help="config root (research.yaml)")
        cmd.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
        cmd.add_argument("--state-root", help="ledger (default $FIRM_STATE_ROOT or ./state)")

    st = rs.add_parser("start", help="open a dossier with a brief; it joins the queue")
    st.add_argument("--brief", help="the brief, as text")
    st.add_argument("--brief-file", help="the brief, from a file (keep it out of the repo)")
    st.add_argument("--market", required=True, help="market profile, e.g. crypto")
    st.add_argument(
        "--instrument", action="append", required=True, help="firm ID, e.g. BTC-USD; repeat"
    )
    st.add_argument("--tf", action="append", required=True, help="e.g. 1d, or 1h,1d; repeat")
    st.add_argument("--id", help="dossier ID (default d-<date>-<random>)")
    st.set_defaults(func=cmd_start)

    ls = rs.add_parser("list", help="every dossier: stage, running or waiting, budget used")
    ls.set_defaults(func=cmd_list)

    sh = rs.add_parser("show", help="one dossier in full")
    sh.add_argument("dossier", help="dossier ID")
    sh.set_defaults(func=cmd_show)

    pa = rs.add_parser("pause", help="stop a dossier from running; it gives up its place")
    pa.add_argument("dossier", help="dossier ID")
    pa.add_argument("--reason", help="why (recorded)")
    pa.set_defaults(func=cmd_pause)

    re_ = rs.add_parser("resume", help="let a paused dossier run again")
    re_.add_argument("dossier", help="dossier ID")
    re_.set_defaults(func=cmd_resume)

    rd = rs.add_parser(
        "redirect", help="a new brief: a started dossier is closed, a new one opened"
    )
    rd.add_argument("dossier", help="dossier ID")
    rd.add_argument("--brief", help="the new brief, as text")
    rd.add_argument("--brief-file", help="the new brief, from a file")
    rd.set_defaults(func=cmd_redirect)

    cl = rs.add_parser("close", help="close a dossier for good")
    cl.add_argument("dossier", help="dossier ID")
    cl.add_argument("--reason", required=True, help="why (recorded)")
    cl.set_defaults(func=cmd_close)

    br = rs.add_parser("brief", help="show or set the floor brief (for every researcher)")
    group = br.add_mutually_exclusive_group()
    group.add_argument("--text", help="the new floor brief")
    group.add_argument("--file", help="the new floor brief, from a file")
    group.add_argument("--clear", action="store_true", help="remove the floor brief")
    br.set_defaults(func=cmd_brief)

    for cmd in (st, ls, sh, pa, re_, rd, cl, br):
        common(cmd)
