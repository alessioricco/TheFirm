---
description: Audit code for lookahead and point-in-time violations. Changes no file.
argument-hint: "<path or module>"
---

Audit `$ARGUMENTS` for any way it can see data from after its `as_of` time.
Change no file; report only.

Look for, at minimum:

- Centred or forward-looking windows, `shift(-n)`, `bfill`, interpolation
  across future points.
- Normalisation, scaling, ranking or z-scores computed over the whole series
  instead of an expanding or rolling past window.
- Resampling or bar aggregation that labels a bar with data from its close
  before the close time.
- Joins on dates that can pull in values published later (fundamentals,
  index membership, corporate actions, news).
- Universe selection from today's listings (survivorship).
- Warm-up periods filled with zeros or future values instead of marked
  missing.
- Feature or model code that differs between backtest and paper trading.

For each finding: file and line, what it can see, a minimal example of the
leak, and the fix. Then say which truncation test in `tests/` would catch
it, or propose one if none would.
