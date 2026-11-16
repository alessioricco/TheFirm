"""The `firm` command line. Grows into a client of the control API (milestone 4).

Exit codes of `firm verify`: 0 pass, 1 fail, 2 unverified, 3 error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from firm import __version__

EXIT_ERROR = 3


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"firm {__version__}")
    return 0


def _cmd_synthetic(args: argparse.Namespace) -> int:
    from firm.data.store import data_root, iter_captures
    from firm.data.synthetic import seed_store

    root = data_root(args.root)
    real = [
        c
        for c in iter_captures(root, provider="kraken", provider_symbol=args.symbol)
        if c.meta.capture_method != "synthetic"
    ]
    if real:
        print(
            f"refusing: {root} holds real captures for kraken:{args.symbol}; "
            "use a separate --root for synthetic data",
            file=sys.stderr,
        )
        return EXIT_ERROR
    seed_store(
        root,
        venue="kraken",
        instrument=args.instrument,
        provider_symbol=args.symbol,
        n=args.bars,
        seed=args.seed,
    )
    print(f"synthetic captures for kraken:{args.symbol} written under {root}")
    return 0


def _cmd_build_dataset(args: argparse.Namespace) -> int:
    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars
    from firm.data.store import data_root

    root = data_root(args.root)
    files = []
    for spec in args.symbol:
        files += normalize_bars(root, provider=args.provider, provider_symbol=spec, tf=args.tf)
    manifest = build_dataset(root, files)
    rows = sum(f.rows for f in manifest.files)
    print(f"{manifest.dataset_version}  ({len(manifest.files)} files, {rows} rows)")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from firm.data.store import data_root
    from firm.verify.runner import verify_strategy_file
    from firm.verify.state import state_root

    path_str, _, class_name = args.strategy.partition(":")
    if not class_name:
        print("--strategy must be PATH:CLASS", file=sys.stderr)
        return EXIT_ERROR
    state = state_root(args.state_root)
    result = verify_strategy_file(
        strategy_path=Path(path_str),
        class_name=class_name,
        params=json.loads(args.params) if args.params else None,
        data_root=data_root(args.root),
        dataset_version=args.dataset,
        venue=args.venue,
        instrument=args.instrument,
        tf=args.tf,
        config_root=Path(args.config),
        jurisdiction=args.jurisdiction,
        market=args.market,
        state=state,
        now=datetime.now(UTC),
        verifier_name=args.verifier,
    )
    out = (
        Path(args.report)
        if args.report
        else state / "reports" / f"{result.verdict.verdict_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.report)
    v = result.verdict
    print(f"{v.verdict_id}  {v.outcome.value.upper()}  ({v.strategy_id} on {v.dataset_version})")
    for c in v.checks:
        print(f"  {c.status.value:<8} {c.name}: {c.detail}")
    print(f"report: {out}")
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="firm", description="The firm: paper trading, v1.")
    parser.add_argument("--version", action="version", version=f"firm {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the version").set_defaults(func=_cmd_version)

    data = sub.add_parser("data", help="data store commands").add_subparsers(dest="data_cmd")
    syn = data.add_parser("synthetic", help="write synthetic captures (no network, no real data)")
    syn.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    syn.add_argument("--instrument", default="BTC-USD")
    syn.add_argument("--symbol", default="XBTUSD")
    syn.add_argument("--bars", type=int, default=2000)
    syn.add_argument("--seed", type=int, default=7)
    syn.set_defaults(func=_cmd_synthetic)
    bld = data.add_parser("build-dataset", help="normalize captures and freeze a dataset version")
    bld.add_argument("--root")
    bld.add_argument("--provider", required=True)
    bld.add_argument("--symbol", required=True, action="append", help="provider symbol; repeat")
    bld.add_argument("--tf", required=True)
    bld.set_defaults(func=_cmd_build_dataset)

    ver = sub.add_parser("verify", help="verify a strategy against a dataset version")
    ver.add_argument("--strategy", required=True, help="PATH:CLASS")
    ver.add_argument("--params", help="JSON object overriding default_params")
    ver.add_argument("--dataset", required=True, help="dataset version (ds-...)")
    ver.add_argument("--venue", required=True)
    ver.add_argument("--instrument", required=True)
    ver.add_argument("--tf", required=True)
    ver.add_argument("--market", required=True, help="market profile name")
    ver.add_argument("--jurisdiction", default="uk")
    ver.add_argument("--config", default="config")
    ver.add_argument("--verifier", default="default")
    ver.add_argument("--root", help="data store")
    ver.add_argument("--state-root", help="verifier state (default $FIRM_STATE_ROOT or ./state)")
    ver.add_argument("--report", help="report path (default <state>/reports/<verdict>.json)")
    ver.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        if args.command == "data":
            parser.parse_args(["data", "--help"])
        print(f"firm {__version__}")
        return 0
    try:
        return int(func(args))
    except (LookupError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
