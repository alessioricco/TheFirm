"""`firm data list | fetch | update`: what the store holds, first downloads,
and bringing every downloaded series up to date (firm.data.update).

`fetch` and `update` use the network (public market-data endpoints, no
keys; owner-run, never by tests). Every response is stored exactly as
received; by default each fetched series is then normalised into a new
dataset version, so it is ready for `firm verify`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXIT_ERROR = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _fetcher(root: Path):  # type: ignore[no-untyped-def]
    """The real fetcher (network). Tests replace this function."""
    from firm.data.fetch import Fetcher

    return Fetcher(root)


def _day(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s).replace(tzinfo=UTC) if s else None


def _fmt(ts: datetime | None, *, day: bool = False) -> str:
    if ts is None:
        return "-"
    return ts.strftime("%Y-%m-%d" if day else "%Y-%m-%d %H:%M")


def _quality(config: str):  # type: ignore[no-untyped-def]
    from firm.core.config import load_data_quality

    return load_data_quality(Path(config))


def _build(root: Path, series: Any, config: str) -> str:
    from firm.data.update import build_summary

    return build_summary(root, series, _quality(config))


def _cmd_list(args: argparse.Namespace) -> int:
    from firm.data.store import data_root
    from firm.data.update import age, series_view, stored_series

    root = data_root(args.root)
    now = _now()
    rows = stored_series(root, provider=args.provider, symbol=args.symbol, tf=args.tf)
    if args.json:
        out = [series_view(s, now) for s in rows]
        print(json.dumps(out, indent=2))
        return 0
    if not rows:
        print(f"no bar series in {root}/raw; download one with `firm data fetch`")
        return 0
    print(
        f"{'SERIES':<28} {'INSTRUMENT':<22} {'CAPTURED':<14} {'N':>4}  "
        f"{'FROM':<10}  {'LAST CLOSE (UTC)':<16}  AGE"
    )
    for s in rows:
        inst = f"{s.venue}:{s.instrument}" if s.instrument else "(no record)"
        print(
            f"{s.key:<28} {inst:<22} {'/'.join(s.methods):<14} {s.captures:>4}  "
            f"{_fmt(s.first_open, day=True):<10}  {_fmt(s.last_close):<16}  "
            f"{age(s.last_close, now)}"
        )
    print("update the api series with `firm data update` (synthetic and imported ones are skipped)")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from firm.data.fetch import plan
    from firm.data.store import data_root
    from firm.data.update import fetch_series, stored_series

    start, end = _day(args.since), _day(args.until) or _now()
    if args.dry_run:
        for req in plan(args.provider, args.symbol, args.tf, start=start, end=end):
            print("GET", req.url)
        return 0
    root = data_root(args.root)
    n = fetch_series(
        _fetcher(root),
        provider=args.provider,
        symbol=args.symbol,
        instrument=args.instrument,
        tf=args.tf,
        start=start,
        end=end,
        venue=args.venue,
        asset_class=args.asset_class,
        quote=args.quote,
        listed_at=_day(args.listed_at),
        trades_pages=args.trades_pages,
    )
    print(f"{args.provider}:{args.symbol}:{args.tf}: {n} request(s) stored")
    if not args.no_build:
        (series,) = stored_series(root, provider=args.provider, symbol=args.symbol, tf=args.tf)
        print(f"  dataset {_build(root, series, args.config)}")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    from firm.data.store import data_root
    from firm.data.update import plan_update, run_update, stored_series

    root = data_root(args.root)
    now = _now()
    todo = stored_series(root, provider=args.provider, symbol=args.symbol, tf=args.tf)
    if not todo:
        print("no matching bar series in the store; download one with `firm data fetch`")
        return 0
    if args.dry_run:
        for s in todo:
            p = plan_update(s, now)
            print(f"{s.key:<28} {p.note}")
            for req in p.requests:
                print("  GET", req.url)
        return 0
    failed = run_update(
        root,
        _fetcher(root),
        todo,
        now,
        _quality(args.config),
        build=not args.no_build,
        err=lambda m: print(m, file=sys.stderr),
    )
    if failed:
        print(
            f"{failed} series failed; rerun `firm data update` (it is safe to repeat)",
            file=sys.stderr,
        )
        return EXIT_ERROR
    return 0


def add_data_store_parsers(data: Any) -> None:
    """Add `list`, `fetch`, `update` under `firm data`."""
    from firm.data import providers

    lst = data.add_parser("list", help="the bar series in the store, and how recent each is")
    lst.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    lst.add_argument("--provider")
    lst.add_argument("--symbol", help="as the provider spells it")
    lst.add_argument("--tf")
    lst.add_argument("--json", action="store_true", help="machine-readable output")
    lst.set_defaults(func=_cmd_list)

    fe = data.add_parser("fetch", help="download a series (first time, or a backfill) - network")
    fe.add_argument("--provider", required=True, help=f"one of: {', '.join(providers.available())}")
    fe.add_argument(
        "--symbol", required=True, help="as the provider spells it: BTCUSDT, BTC-USD, NVDA"
    )
    fe.add_argument(
        "--instrument", required=True, help="firm ID: BTC-USD (crypto), NVDA.XNAS (shares)"
    )
    fe.add_argument("--tf", default="1d")
    fe.add_argument("--venue", help="where it trades (default: the provider; shares: e.g. xnas)")
    fe.add_argument("--asset-class", choices=["crypto", "equity"], default="crypto")
    fe.add_argument("--quote", help="quote currency for shares (e.g. USD)")
    fe.add_argument("--since", help="first day YYYY-MM-DD (default: the provider's latest page)")
    fe.add_argument("--until", help="last day YYYY-MM-DD (default: now)")
    fe.add_argument("--listed-at", help="listing date YYYY-MM-DD, needed the first time")
    fe.add_argument("--trades-pages", type=int, default=0, help="also fetch N pages of trades")
    fe.add_argument("--no-build", action="store_true", help="don't build a dataset version")
    fe.add_argument("--config", default="config", help="config root (data quality rule)")
    fe.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    fe.add_argument("--dry-run", action="store_true", help="print the requests, download nothing")
    fe.set_defaults(func=_cmd_fetch)

    up = data.add_parser(
        "update", help="bring every downloaded series up to date (only new bars) - network"
    )
    up.add_argument("--provider", help="only this provider")
    up.add_argument("--symbol", help="only this provider symbol")
    up.add_argument("--tf", help="only this timeframe")
    up.add_argument("--no-build", action="store_true", help="don't build dataset versions")
    up.add_argument("--config", default="config", help="config root (data quality rule)")
    up.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    up.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    up.set_defaults(func=_cmd_update)
