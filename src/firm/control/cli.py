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


def _series(args: argparse.Namespace) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Parse `--bars P:S:TF`, `--trades P:S` and the legacy `--provider/--symbol/--tf`."""
    bars: list[tuple[str, str, str]] = []
    trades: list[tuple[str, str]] = []
    for spec in args.bars or []:
        parts = spec.split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"--bars expects PROVIDER:SYMBOL:TF, got {spec!r}")
        bars.append((parts[0], parts[1], parts[2]))
    for spec in args.trades or []:
        parts = spec.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"--trades expects PROVIDER:SYMBOL, got {spec!r}")
        trades.append((parts[0], parts[1]))
    if args.symbol or args.provider or args.tf:
        if not (args.symbol and args.provider and args.tf):
            raise ValueError("--provider, --symbol and --tf go together")
        bars += [(args.provider, s, args.tf) for s in args.symbol]
    if not bars and not trades:
        raise ValueError("nothing to build: give --bars and/or --trades")
    return bars, trades


def _cmd_build_dataset(args: argparse.Namespace) -> int:
    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars, normalize_trades
    from firm.data.store import data_root

    root = data_root(args.root)
    bars, trades = _series(args)
    files = []
    for provider, symbol, tf in bars:
        files += normalize_bars(root, provider=provider, provider_symbol=symbol, tf=tf)
    for provider, symbol in trades:
        files += normalize_trades(root, provider=provider, provider_symbol=symbol)
    manifest = build_dataset(root, files)
    rows = sum(f.rows for f in manifest.files)
    print(f"{manifest.dataset_version}  ({len(manifest.files)} files, {rows} rows)")
    for f in manifest.files:
        print(
            f"  {f.kind:<6} {f.venue}:{f.instrument} {f.tf or '':<3} {f.rows:>8} rows"
            f"{f'  revisions={f.revisions}' if f.revisions else ''}"
        )
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


def _cmd_state_show(args: argparse.Namespace) -> int:
    from firm.verify.state import state_root, state_summary

    root = state_root(args.state_root)
    if not root.exists():
        print(f"no verifier state at {root}")
        return 0
    s = state_summary(root)
    if args.json:
        print(json.dumps(s, indent=1, sort_keys=True))
        return 0
    print(f"state: {s['root']}  (schema {s['schema_version'] or 'unversioned'})")
    print(f"trials: {s['trials_total']} (global count used by the verifier)")
    for exp, n in sorted(s["trials_by_experiment"].items()):
        print(f"  {exp}: {n}")
    print("out-of-sample vault:")
    for strategy, counts in sorted(s["oos_vault"].items()):
        print(f"  {strategy}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"reports: {len(s['reports'])}")
    for r in s["reports"]:
        print(
            f"  {r['verdict_id']}  {str(r['outcome']).upper():<10} "
            f"{r['strategy_id']} on {r['dataset_version']}"
        )
    return 0


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
    bld.add_argument(
        "--bars",
        action="append",
        metavar="PROVIDER:SYMBOL:TF",
        help="bar series to include; repeat for several venues/symbols",
    )
    bld.add_argument(
        "--trades",
        action="append",
        metavar="PROVIDER:SYMBOL",
        help="trade series to include; repeat",
    )
    bld.add_argument("--provider", help="legacy: one provider for all --symbol")
    bld.add_argument("--symbol", action="append", help="legacy: provider symbol; repeat")
    bld.add_argument("--tf", help="legacy: timeframe for --symbol")
    bld.set_defaults(func=_cmd_build_dataset)

    state = sub.add_parser("state", help="verifier state (read-only)").add_subparsers(
        dest="state_cmd"
    )
    show = state.add_parser("show", help="trials, out-of-sample openings, reports")
    show.add_argument("--state-root", help="default $FIRM_STATE_ROOT or ./state")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_state_show)

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
        if args.command in ("data", "state"):
            parser.parse_args([args.command, "--help"])
        print(f"firm {__version__}")
        return 0
    try:
        return int(func(args))
    except (LookupError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
