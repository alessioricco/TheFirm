---
description: Download a new market data series, or bring the downloaded ones up to date, and build dataset versions.
argument-hint: "[empty: update everything | <provider> <provider symbol> <instrument> <tf> [since YYYY-MM-DD]]"
---

Download or update data in the git-ignored data store. Request: `$ARGUMENTS`
(empty: update every downloaded series).

Use `FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm` (the
same as `uv run firm`, without uv's cache, which the sandbox blocks).

1. This uses the network (owner-approved for public market-data endpoints
   only). Tests never do. Never write synthetic data into the real store.
   The sandbox lets a command reach only the hosts it declares: binance
   `data-api.binance.vision`, coinbase `api.exchange.coinbase.com`, kraken
   `api.kraken.com`, yahoo `query1.finance.yahoo.com`.
2. Show what is there: `firm data list`.
3. **Update** (no arguments, or the owner names a provider/series):
   `firm data update --dry-run [--provider P --symbol S --tf T]`, show the
   plan, then run it without `--dry-run`. Failed series are reported and
   the rest carry on; rerunning is safe.
4. **New series**: dry run first and show the requests:
   `firm data fetch --provider P --symbol S --instrument I [--venue V]
   [--asset-class equity --quote USD] --tf T [--since D] [--listed-at D]
   --dry-run`. `--listed-at` is needed the first time an instrument is
   seen; if you don't know the listing date, ask instead of guessing. Then
   run it for real.
5. Report: each series' new dataset version, bars, last close, gaps,
   revisions (closed bars on which captures disagreed) and quarantined
   bars, as printed. A dataset spanning several series is
   `firm data build-dataset --bars P:S:TF [--bars ...]`.
6. Check the data is still out of git: `python3 scripts/check_repo.py --all`.
   Record the download, the new dataset versions and anything odd in the
   git-ignored `private/TODO.md` (never in the public TODO.md).
