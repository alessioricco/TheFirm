---
description: What the data store holds - every bar series, how recent, and what an update would fetch. Read-only, no network.
argument-hint: "[provider | provider symbol | tf to filter]"
---

Show the owner the data store (docs/COMMANDS.md, "firm data"). Filter from
the owner: `$ARGUMENTS` (empty: everything). Use
`FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm` (the same as
`uv run firm`, without uv's cache, which the sandbox blocks).

Run (read-only, no network; nothing here downloads or changes anything):

1. `firm data list [--provider P] [--symbol S] [--tf T]`
2. `firm data update --dry-run [same filters]`

Report in at most 12 lines:

- each series: provider symbol and timeframe, the firm instrument, how it
  was captured (downloaded, synthetic, imported), first bar, last closed
  bar and its age;
- what an update would do: series that are behind (and roughly how many
  bars), series already current, series skipped (synthetic or imported)
  and any Kraken series that would keep a gap (it serves only 720 bars);
- next steps: `/firm-data-update` to bring them current,
  `/firm-data-fetch` for a new series, or the dashboard's Data tab
  (`/firm-dashboard`).

Report what the commands printed, nothing else. Market data stays out of
the conversation beyond these summaries: never paste prices or bars.
