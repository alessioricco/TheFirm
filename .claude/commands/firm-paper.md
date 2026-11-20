---
description: Paper-trade a strategy that has a passing verdict - preflight checks, then the exact commands for your terminal, or a run in this session.
argument-hint: "VERDICT [HOURS] [here]"
---

Start a paper run of the strategy behind verdict `$ARGUMENTS`
(docs/CLAUDE_CODE.md, section 4). Arguments: a verdict ID (`v-...`),
optionally hours, optionally `here` to run it inside this session.

Use `FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli` for `firm`.

**Preflight** (read-only; stop at the first failure and tell the owner why):

1. The report `state/reports/<VERDICT>.json` exists and `outcome` is
   `pass`. Anything else: stop. The deployment gate would refuse it anyway;
   never look for a way around it.
2. From the report: `data.venue`, `data.instruments`, `data.timeframe`,
   `candidate.parameters`, `candidate.code_sha256`. The market is the
   profile in `config/markets/` whose `data.history` lists the venue; if
   more than one does, ask.
3. Find the strategy file whose sha256 equals `code_sha256`: under
   `examples/strategies/` (`shasum -a 256`), or in the private store, where
   the name already carries the hash
   (`<data root>/research/strategies/<dossier>/<stem>-<sha256[:12]>.py`).
   Its class: for an example, read it; for a private-store file **don't
   open it** (agent-written code, option a excludes it): take only the
   class line, `grep -m1 -oE '^class [A-Za-z_][A-Za-z0-9_]*'`, as the
   researcher does. Not found: stop and say so. A private-store file always
   runs in the sandbox (`firm paper run` enforces it).
4. `firm bus status`. If the bus isn't running, it has to start first.
   Inside the sandbox it fails with "Operation not permitted" and wrongly
   says the bus is down: rerun it outside the sandbox (read-only). Starting
   the bus or a run `here` also needs the sandbox off (local ports, the
   venue's network).

**Then show the owner** what will run (strategy, class, verdict, market,
venue, instrument, timeframe, hours or "until stopped") and launch it one of
two ways:

- **Default: the owner's terminal.** Give the exact commands, ready to
  paste: `caffeinate -is uv run firm bus up` in one terminal if the bus is
  down, then `caffeinate -is uv run firm paper run --strategy PATH:CLASS
  --verdict V --market M --venue X --instrument I --tf T [--hours H]`
  (drop `caffeinate -is` on Linux, or use the systemd unit, USER_GUIDE
  section 12). This way the run survives this session and Telegram alerts
  work (the run reads `.env` itself; Claude Code never does). Use the
  `.venv/bin/python -m firm.control.cli` form only if `uv run` fails.
- **`here`: a background run in this session.** Only for a short run the
  owner is watching. Start the bus in the background if it is down, then
  the paper run in the background with `--no-notify` (this session doesn't
  load `.env`, so no Telegram): say clearly that alerts are off and that
  the run stops when the session ends.

Either way, give the `tail -f <state>/logs/paper-<market>-<venue>.log`
command, and when a background run ends, report how and why from its log
and `firm paper status`. Never add `--params` that differ from the
verified ones, and never pass anything the report doesn't support.
