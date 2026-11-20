---
description: Download a new market data series (or backfill one) into the store and build its dataset version.
argument-hint: "<provider> <provider symbol> <firm instrument> [tf] [since YYYY-MM-DD]"
---

Download a series into the git-ignored data store (docs/COMMANDS.md,
"firm data fetch"). Request from the owner: `$ARGUMENTS`. Use
`FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm`.

1. This uses the network: public market-data endpoints only, no keys
   (owner-approved). Declare only the provider's host to the sandbox:
   binance `data-api.binance.vision`, coinbase `api.exchange.coinbase.com`,
   kraken `api.kraken.com`, yahoo `query1.finance.yahoo.com`.
2. Work out the arguments; ask rather than guess:
   - `--symbol` is how the **provider** spells it (`XBTUSD` on Kraken,
     `BTC-USD` on Coinbase, `BTCUSDT` on Binance, `NVDA` on Yahoo);
     `--instrument` is the **firm's** ID (`BTC-USD`, `ETH-USDT`,
     `NVDA.XNAS`). Shares need `--venue xnas|xnys --asset-class equity
     --quote USD`.
   - `--listed-at` is needed the first time an instrument is recorded
     (`firm data list` shows which exist). If you don't know the listing
     date, ask the owner; never guess.
   - `--tf` must be one the provider serves
     (`.venv/bin/python scripts/download_data.py --providers` lists them).
     Kraken keeps only its last 720 bars: say so for a long history.
3. Dry run, and show the owner the requests:
   `firm data fetch --provider P --symbol S --instrument I [--tf T]
   [--since D] [--listed-at D] [...] --dry-run`.
   A long backfill is many requests; say roughly how long it will take.
4. Run it without `--dry-run`. Report the dataset version, bars, gaps,
   revisions and quarantined bars, as printed.
5. Record the download and the dataset version in the git-ignored
   `private/TODO.md` (never in the public TODO.md). Never commit data:
   `python3 scripts/check_repo.py --all` must stay clean.

Using a source for research or paper trading says nothing about trading
there with real money (the jurisdiction profile decides that; off in v1).
