---
description: Bring the downloaded data series up to date (only the new bars) and build their dataset versions.
argument-hint: "[provider] [provider symbol] [tf]"
---

Update the downloaded series in the git-ignored data store
(docs/COMMANDS.md, "firm data update"). Filter from the owner:
`$ARGUMENTS` (empty: every downloaded series). Use
`FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm`.

1. This uses the network: public market-data endpoints only, no keys
   (owner-approved). The sandbox lets a command reach only the hosts it
   declares: binance `data-api.binance.vision`, coinbase
   `api.exchange.coinbase.com`, kraken `api.kraken.com`, yahoo
   `query1.finance.yahoo.com`; declare only the ones in the plan.
2. Plan: `firm data update --dry-run [--provider P] [--symbol S] [--tf T]`.
   Show the owner one line per series. If nothing is behind, say so and
   stop.
3. Run it: `firm data update [same filters]`. A failed series is reported
   and the rest carry on (exit 3); rerunning is safe, so retry a failed
   one once if the error looks transient, then report.
4. Report: per series, the new last close, dataset version, bars, gaps,
   revisions (closed bars on which captures disagreed) and quarantined
   bars, as printed. Flag anything odd (revisions, new gaps, a Kraken gap).
5. Record the update, the new dataset versions and anything odd in the
   git-ignored `private/TODO.md` (never in the public TODO.md). Never commit
   data: `python3 scripts/check_repo.py --all` must stay clean.

Never pass `--root` pointing at a store other than the owner's unless they
ask, and never write synthetic data into the real store.
