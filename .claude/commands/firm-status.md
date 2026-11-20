---
description: One summary of the firm - paper runs, feeds, kill switches, bus, research floor, proposals waiting for the owner.
argument-hint: "[--json for raw output]"
---

Give the owner one short status of the firm (docs/CLAUDE_CODE.md). Reading
results is covered by the owner's standing opt-in (DECISIONS 2026-11-20,
option a): status, P&L, fills, verdicts and dossiers are fine; never open
agent-written strategy code or its parameters from the private store.

Run these (read-only; none of them changes anything). Use
`FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm` (the same as
`uv run firm`, without uv's cache, which the sandbox blocks):

1. `firm paper status` (accounts, runs, feeds, risk heartbeat, halts, P&L)
2. `firm bus status` (says so if the bus isn't running). Inside the
   sandbox it fails with "Operation not permitted" (local connections are
   blocked) and wrongly says the bus is down: rerun it outside the sandbox
   (it is read-only).
3. `firm research list` (dossiers, running or waiting, budget used)
4. `firm research proposals list --status proposed`
5. Background jobs this session started (paper runs, bus, dashboard): still
   running or how they ended.

Then report in at most 15 lines, in this order:

- **Needs you**: proposals waiting for approval, a halt or kill switch on,
  a stale feed, a run that stopped unexpectedly, a dossier closed by its
  budget. Say "nothing" if nothing.
- **Paper**: each run (strategy, market, running or stopped), P&L, open
  positions.
- **Floor**: dossiers by stage; who runs, who waits.
- **Infrastructure**: bus, feeds, dashboard.

Report what the commands printed, nothing else; if one failed, say so and
show its error. If the owner passed `--json`, also show `firm paper status
--json`. Change nothing: no command here starts, stops, approves or kills.
