---
description: Write a strategy from the template, verify it on synthetic then real data, report the verdict as it is.
argument-hint: "<name> <one-line hypothesis>"
---

Add the strategy `$ARGUMENTS`.

1. Read `src/firm/verify/strategy.py` (the `Strategy` protocol) and the
   examples in `examples/strategies/`. Write `examples/strategies/<name>.py`:
   a class with `name`, `default_params` (a `ClassVar` dict) and
   `signals(bars, params)` returning a target exposure in [0, 1] per bar
   (spot, no leverage, no shorting in v1), NaN during warm-up. Use only rows
   up to the current one: no `shift(-n)`, centred windows, `bfill`, or
   statistics over the whole series. Run `/check-point-in-time` on the file.
2. Verify on synthetic data first (a separate store, never the real one):
   `FIRM_DATA_ROOT=./data-demo FIRM_STATE_ROOT=./data-demo/state`,
   `firm data synthetic`, `firm data build-dataset ...`, `firm verify ...`.
   This shows the code runs; it says nothing about the market.
3. Verify on real data: use an existing real dataset version in the store,
   or run `/update-data` first. Show the real `firm verify` output.
4. Report the verdict exactly as the verifier gives it (pass / fail /
   unverified, each check's detail, verdict ID, dataset version). Never
   call a strategy good because it passed: thresholds are placeholders.
5. **Every run is a trial.** Do not tweak parameters and re-run until it
   passes: the registry counts each variant, the deflated Sharpe raises the
   bar, and a changed version can't reopen the hidden out-of-sample period.
   If it fails, stop and report; propose a new hypothesis instead.
6. The strategy file may be committed (it's code); reports, state and data
   never are. Add a TODO.md entry for any follow-up.
