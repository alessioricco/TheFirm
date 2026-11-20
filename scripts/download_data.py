"""Download market data from any registered provider into the store. OWNER-RUN.

Prefer `firm data fetch` (same options, and it builds the dataset version)
and `firm data update` (only the new bars of every downloaded series).
This script stays for existing notes and scripts.

Never run by tests (they use fake transports). Public endpoints only, no
keys. Every response is stored exactly as received, as one capture with its
`.meta.json`; build dataset versions with `firm data build-dataset`.

    uv run python scripts/download_data.py --providers          # what's available
    uv run python scripts/download_data.py --provider binance --symbol BTCUSDT \\
        --instrument BTC-USDT --tf 1d --since 2017-08-17 --listed-at 2017-08-17
    uv run python scripts/download_data.py --provider coinbase --symbol BTC-USD \\
        --instrument BTC-USD --tf 1d --since 2015-07-20 --listed-at 2015-01-26
    uv run python scripts/download_data.py --provider yahoo --symbol NVDA \\
        --instrument NVDA.XNAS --venue xnas --asset-class equity --quote USD \\
        --tf 1d --since 2015-01-01 --listed-at 1999-01-22
    ... --dry-run   # print the requests, download nothing

Using a source here is for the lab (research, training, paper trading). It
says nothing about trading there with real money: that is decided by the
jurisdiction profile (`allowed_venues`, `venue_status`) and stays off in v1.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from firm.data import providers
from firm.data.fetch import Fetcher, plan
from firm.data.store import data_root
from firm.data.update import fetch_series


def _day(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def main(argv: list[str] | None = None, *, fetcher: Fetcher | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--providers", action="store_true", help="list providers and exit")
    ap.add_argument("--provider", help=f"one of: {', '.join(providers.available())}")
    ap.add_argument("--symbol", help="as the provider spells it: XBTUSD, BTC-USD, BTCUSDT, NVDA")
    ap.add_argument("--instrument", help="firm id: BTC-USD (crypto), NVDA.XNAS (shares)")
    ap.add_argument("--venue", help="where it trades (default: the provider; shares: e.g. xnas)")
    ap.add_argument("--asset-class", choices=["crypto", "equity"], default="crypto")
    ap.add_argument("--quote", help="quote currency for shares (e.g. USD)")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--since", help="first day (YYYY-MM-DD); default: the provider's latest page")
    ap.add_argument("--until", help="last day (YYYY-MM-DD); default: now")
    ap.add_argument("--listed-at", help="listing date, needed the first time")
    ap.add_argument("--trades-pages", type=int, default=0, help="also fetch N pages of trades")
    ap.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.providers:
        for name in providers.available():
            p = providers.get(name)
            print(
                f"{name:<9} {p.description}; kinds={sorted(p.kinds)} "
                f"tf={sorted(p.timeframes)}; {p.history_note}"
            )
        return 0
    if not (args.provider and args.symbol and args.instrument):
        ap.error("--provider, --symbol and --instrument are required")

    start = _day(args.since) if args.since else None
    end = _day(args.until) if args.until else datetime.now(UTC)
    if args.dry_run:
        for req in plan(args.provider, args.symbol, args.tf, start=start, end=end):
            print("GET", req.url)
        return 0

    root = data_root(args.root)
    fetch_series(
        fetcher or Fetcher(root),
        provider=args.provider,
        symbol=args.symbol,
        instrument=args.instrument,
        tf=args.tf,
        start=start,
        end=end,
        venue=args.venue,
        asset_class=args.asset_class,
        quote=args.quote,
        listed_at=_day(args.listed_at) if args.listed_at else None,
        trades_pages=args.trades_pages,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, providers.ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
