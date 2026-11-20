---
description: Ask the senior quant (web-reading subagent) for strategy proposals; save and import them for the owner to approve.
argument-hint: "[focus, e.g. '3 ideas for crypto 1h-4h']"
---

Ask the senior quant for brief proposals. Focus from the owner: $ARGUMENTS
(if empty: "2-3 ideas for the markets and data the firm has").

1. Get the context pack, with outcomes (owner's standing opt-in, DECISIONS
   2026-11-20; the command logs the egress):
   `FIRM_DOTENV=0 uv run firm research proposals context --outcomes`
   (use `.venv/bin/python -m firm.control.cli ...` if uv's cache is blocked).
2. Launch the `senior-quant` subagent. Its prompt is the focus plus the
   context pack, verbatim, and today's date. Nothing else from this session:
   never paste private/TODO.md, dossiers, metrics, parameters, code or
   dataset versions into it.
3. Save each ```yaml block of its answer, unchanged, to
   `private/briefs/proposals/<YYYYMMDD>-<short-slug>.yaml` (git-ignored).
   Don't edit them; if one is refused, show the owner the reason and ask
   whether to send it back to the senior quant for a fix.
4. Import them:
   `FIRM_DOTENV=0 uv run firm research proposals import private/briefs/proposals/`
5. Report to the owner: `firm research proposals list --status proposed`,
   one line per idea (family, timeframe, why the edge, expected trades a
   month), the senior quant's closing note, and the commands to decide:
   `uv run firm research proposals show ID`, `... approve ID`,
   `... reject ID --reason "..."`. Never approve on the owner's behalf.
