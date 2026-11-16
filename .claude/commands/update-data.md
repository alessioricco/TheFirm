---
description: Download (or refresh) a market data series into the store and build a dataset version.
argument-hint: "<venue> <instrument> <provider symbol> <tf> [since YYYY-MM-DD]"
---

Update the data series `$ARGUMENTS` in the git-ignored data store.

1. This uses the network (owner-approved for public market-data endpoints
   only). Tests never do. Never write synthetic data into the real store.
2. Dry run first and show the requests:
   `uv run python scripts/download_crypto.py --venue V --instrument I
   --symbol S --tf T [--since D] [--listed-at D] --dry-run`.
   `--listed-at` is needed the first time an instrument is seen; if you
   don't know the listing date, ask instead of guessing.
3. Run it for real. Transient errors are retried; report any that remain.
   Re-runs are safe: captures are write-once and content-hashed.
4. Build the dataset version:
   `uv run firm data build-dataset --provider P --symbol S --tf T`
   (one provider per run for now; see TODO.md for multi-venue datasets).
5. Report: dataset version, bars per series, first and last close time,
   gaps, revisions (closed bars on which captures disagreed), and that the
   still-forming bar was dropped. Compare last closes across venues if
   more than one is present.
6. Check the data is still out of git: `python3 scripts/check_repo.py --all`.
   Record the new dataset version and anything odd in TODO.md.
