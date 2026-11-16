"""Download public Kraken / Coinbase market data into the data store. OWNER-RUN.

Never run by tests. Public endpoints only (no API keys). Every response is
stored exactly as received, as one capture with its `.meta.json`; turn
captures into a dataset with `firm data build-dataset`.

Examples:

    uv run python scripts/download_crypto.py --venue coinbase --instrument BTC-USD \\
        --symbol BTC-USD --tf 1d --since 2017-01-01 --listed-at 2015-07-20
    uv run python scripts/download_crypto.py --venue kraken --instrument BTC-USD \\
        --symbol XBTUSD --tf 1d --listed-at 2013-09-10
    ... --dry-run   # print the requests, download nothing

Limits worth knowing (see DECISIONS.md "Crypto connectivity"):
- Kraken OHLC returns only the most recent 720 bars of a timeframe. Older
  history needs the trades endpoint (`--trades-pages`, slow) or Kraken's
  downloadable files (check their terms first).
- Coinbase candles: at most 300 per request; paged backwards in time.
- Coinbase has no 4h or 1w candles.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from firm.core.timeframes import tf_seconds
from firm.data.providers import (
    coinbase_candles_url,
    coinbase_trades_url,
    kraken_ohlc_url,
    kraken_trades_url,
)
from firm.data.reference import InstrumentRecord, latest_instruments, write_instrument
from firm.data.store import data_root, write_capture

USER_AGENT = "firm-data-downloader/0.1"
LICENCE = {
    "kraken": "kraken public market data: personal use; check terms before redistribution",
    "coinbase": "coinbase public market data: personal use; check terms before redistribution",
}
PAUSE = {"kraken": 1.2, "coinbase": 0.4}  # seconds between requests (public rate limits)


def fetch(url: str, attempts: int = 5) -> tuple[bytes, datetime]:
    """GET with retries on transient failures (incomplete read, connection, 429, 5xx)."""
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            return body, datetime.now(UTC)
        except urllib.error.HTTPError as exc:
            if (exc.code != 429 and exc.code < 500) or attempt == attempts:
                raise
            why = f"HTTP {exc.code}"
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise
            why = type(exc).__name__
        wait = 2**attempt
        print(f"  retry {attempt}/{attempts - 1} in {wait}s ({why})", file=sys.stderr)
        time.sleep(wait)
    raise AssertionError("unreachable")


def ensure_instrument(root: Path, args: argparse.Namespace) -> None:
    if (args.venue, args.instrument) in latest_instruments(root):
        return
    if not args.listed_at:
        sys.exit("first download of this instrument: pass --listed-at YYYY-MM-DD")
    base, quote = args.instrument.split("-")
    write_instrument(
        root,
        InstrumentRecord(
            venue=args.venue,
            instrument=args.instrument,
            asset_class="crypto",
            base=base,
            quote=quote,
            provider_symbols={args.venue: args.symbol},
            listed_at=datetime.fromisoformat(args.listed_at).replace(tzinfo=UTC),
            known_at=datetime.now(UTC),
        ),
    )


def store(root: Path, args: argparse.Namespace, kind: str, url: str, dry: bool) -> bytes | None:
    if dry:
        print("GET", url)
        return None
    body, at = fetch(url)
    cap = write_capture(
        root,
        body,
        provider=args.venue,
        kind=kind,
        provider_symbol=args.symbol,
        tf=args.tf if kind == "bars" else None,
        fmt="json",
        captured_at=at,
        request={"url": url},
        via="urllib",
        licence=LICENCE[args.venue],
    )
    print(f"{kind}: {len(body):>8} bytes -> {cap.path.relative_to(root)}")
    time.sleep(PAUSE[args.venue])
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--venue", choices=["kraken", "coinbase"], required=True)
    ap.add_argument("--instrument", required=True, help="firm id, e.g. BTC-USD")
    ap.add_argument("--symbol", required=True, help="provider symbol: XBTUSD (kraken), BTC-USD")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--since", help="coinbase: first day to fetch (YYYY-MM-DD)")
    ap.add_argument("--listed-at", help="listing date, needed the first time")
    ap.add_argument("--trades-pages", type=int, default=0, help="also fetch N pages of trades")
    ap.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = data_root(args.root)
    if not args.dry_run:
        ensure_instrument(root, args)

    if args.venue == "kraken":
        store(root, args, "bars", kraken_ohlc_url(args.symbol, args.tf), args.dry_run)
        since: int | None = None
        for _ in range(args.trades_pages):
            body = store(root, args, "trades", kraken_trades_url(args.symbol, since), args.dry_run)
            if body is None:
                break
            since = int(json.loads(body)["result"]["last"])
    else:
        step = timedelta(seconds=tf_seconds(args.tf) * 300)
        end = datetime.now(UTC)
        start = datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since else end - step
        t = start
        while t < end:
            store(
                root,
                args,
                "bars",
                coinbase_candles_url(args.symbol, args.tf, t, min(t + step, end)),
                args.dry_run,
            )
            t += step
        after: int | None = None
        for _ in range(args.trades_pages):
            body = store(
                root, args, "trades", coinbase_trades_url(args.symbol, after), args.dry_run
            )
            if body is None:
                break
            trades = json.loads(body)
            if not trades:
                break
            after = min(int(tr["trade_id"]) for tr in trades)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
