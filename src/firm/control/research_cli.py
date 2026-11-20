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
from pathlib import Path
from typing import Any


def _floor(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from firm.control.research import open_floor
    from firm.data.store import data_root
    from firm.verify.state import state_root

    return open_floor(Path(args.config), data_root(args.root), state_root(args.state_root))


def _owner(ledger, command: str, payload: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    from firm.control.research import owner_command

    owner_command(ledger, command, payload, via="cli")


def _brief_text(args: argparse.Namespace) -> str:
    if getattr(args, "brief_file", None):
        return Path(args.brief_file).read_text().strip()
    return (args.brief or "").strip()


def _place(manager, dossier_id: str) -> str:  # type: ignore[no-untyped-def]
    from firm.control.research import place

    return place(manager, dossier_id)


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
    _add_proposals_parser(rs, common)


def _add_proposals_parser(rs, common) -> None:  # type: ignore[no-untyped-def]
    pr = rs.add_parser(
        "proposals", help="strategy ideas from the senior quant: import, review, approve"
    ).add_subparsers(dest="proposals_cmd")

    im = pr.add_parser("import", help="check and record proposal files (YAML)")
    im.add_argument("paths", nargs="+", help="proposal files or folders of *.yaml")
    im.set_defaults(func=cmd_proposals_import)

    li = pr.add_parser("list", help="every proposal and its status")
    li.add_argument("--status", choices=["proposed", "approved", "rejected"])
    li.set_defaults(func=cmd_proposals_list)

    sh = pr.add_parser("show", help="one proposal in full, as the brief it would become")
    sh.add_argument("proposal", help="proposal ID")
    sh.set_defaults(func=cmd_proposals_show)

    ap = pr.add_parser("approve", help="open a dossier with the proposal as its brief")
    ap.add_argument("proposal", help="proposal ID")
    ap.add_argument("--id", help="dossier ID (default d-<date>-<random>)")
    ap.set_defaults(func=cmd_proposals_approve)

    rj = pr.add_parser("reject", help="turn a proposal down, with a reason")
    rj.add_argument("proposal", help="proposal ID")
    rj.add_argument("--reason", required=True, help="why (recorded; the senior quant sees it)")
    rj.set_defaults(func=cmd_proposals_reject)

    cx = pr.add_parser(
        "context", help="what the senior quant may know: markets, costs, data, past proposals"
    )
    cx.add_argument(
        "--outcomes",
        action="store_true",
        help="add each proposal's stage, verdict outcomes and failed check names "
        "(private data to a cloud model: logged)",
    )
    cx.set_defaults(func=cmd_proposals_context)

    for cmd in (im, li, sh, ap, rj, cx):
        common(cmd)


# ---------------------------------------------------------------- proposals
def _proposals(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from firm.data.store import data_root
    from firm.research.proposals import ProposalStore

    return ProposalStore(data_root(args.root))


def _markets(config: Path) -> set[str]:
    return {p.stem for p in (config / "markets").glob("*.yaml")}


def cmd_proposals_import(args: argparse.Namespace) -> int:
    from firm.research.proposals import ProposalError, parse_proposal

    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        files += sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")) if p.is_dir() else [p]
    if not files:
        print("no proposal files (*.yaml) found", file=sys.stderr)
        return 3
    store, markets = _proposals(args), _markets(Path(args.config))
    _, ledger = _floor(args)
    bad = 0
    for f in files:
        try:
            proposal = parse_proposal(f.read_text(), markets=markets)
        except (ProposalError, OSError) as e:
            print(f"{f}: refused: {e}", file=sys.stderr)
            bad += 1
            continue
        p, new = store.add(proposal)
        if new:
            _owner(ledger, "proposals import", {"proposal_id": p.proposal_id})
        print(f"{p.proposal_id}  {'new' if new else p.status + ' (already imported)'}  {f.name}")
    return 2 if bad else 0


def cmd_proposals_list(args: argparse.Namespace) -> int:
    proposals = _proposals(args).list()
    if args.status:
        proposals = [p for p in proposals if p.status == args.status]
    if not proposals:
        print("no proposals: ask the senior quant (`/senior-quant` in Claude Code)")
        return 0
    for p in proposals:
        extra = f" -> {p.dossier_id}" if p.dossier_id else ""
        print(
            f"{p.proposal_id}  {p.status:<9} {p.proposal.method_family:<18} "
            f"{p.proposal.market} {','.join(p.proposal.timeframes)}  {p.proposal.title}{extra}"
        )
    return 0


def cmd_proposals_show(args: argparse.Namespace) -> int:
    p = _proposals(args).get(args.proposal)
    print(f"{p.proposal_id}  {p.status}  proposed {p.proposed_at:%Y-%m-%d %H:%M} UTC")
    print(
        f"market {p.proposal.market}  instruments {', '.join(p.proposal.instruments)}  "
        f"timeframes {', '.join(p.proposal.timeframes)}"
    )
    if p.dossier_id:
        print(f"dossier: {p.dossier_id}")
    if p.rejected_reason:
        print(f"rejected: {p.rejected_reason}")
    print()
    print(p.proposal.as_brief())
    return 0


def cmd_proposals_approve(args: argparse.Namespace) -> int:
    store = _proposals(args)
    p = store.get(args.proposal)
    if p.status != "proposed":
        print(f"{p.proposal_id} is already {p.status}", file=sys.stderr)
        return 3
    manager, ledger = _floor(args)
    d = manager.start(
        brief=p.proposal.as_brief(),
        market=p.proposal.market,
        instruments=p.proposal.instruments,
        timeframes=p.proposal.timeframes,
        dossier_id=args.id,
    )
    store.approve(p.proposal_id, d.dossier_id)
    _owner(ledger, "proposals approve", {"proposal_id": p.proposal_id, "dossier_id": d.dossier_id})
    print(f"{p.proposal_id} approved: {d.dossier_id}  draft  {_place(manager, d.dossier_id)}")
    return 0


def cmd_proposals_reject(args: argparse.Namespace) -> int:
    store = _proposals(args)
    _, ledger = _floor(args)
    store.reject(args.proposal, args.reason)
    _owner(ledger, "proposals reject", {"proposal_id": args.proposal, "reason": args.reason})
    print(f"{args.proposal} rejected")
    return 0


def cmd_proposals_context(args: argparse.Namespace) -> int:
    from firm.research.proposals import context_pack

    manager, ledger = _floor(args)
    from firm.data.store import data_root
    from firm.verify.state import state_root

    text, n_outcomes = context_pack(
        config_root=Path(args.config),
        data_root=data_root(args.root),
        store=_proposals(args),
        dossiers=manager.store,
        reports=state_root(args.state_root) / "reports",
        with_outcomes=args.outcomes,
    )
    if args.outcomes:
        # Owner's standing opt-in (DECISIONS 2026-11-20): outcomes only, to a cloud model.
        _owner(
            ledger,
            "proposals context",
            {
                "egress": "cloud",
                "tag": "private",
                "fields": "stage, verdict outcome, failed check names",
                "proposals_with_outcomes": n_outcomes,
            },
        )
    print(text)
    return 0
