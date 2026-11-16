# Task: extract the ari_signals Level 0 generator into the firm's feature library

Source repo (owner's own code): `~/Documents/GitHub/ari_signals`.
Target: `src/firm/data/features/ari/` in the firm repo.
When: step 0 (golden baseline) can run now and must run before the old repo
is archived; steps 1–3 after milestone 1's data plane exists.

## What to extract

The JSON generator is `timeseries_info(df, symbol, description, tf, exchange,
params, normalized)` in `src/analysis/level_0_generation.py`. It computes ~30
indicators concurrently (market structure, trends, S/R, pivots, Fibonacci,
patterns, signals, RSI/MACD/EMA/ADX context, volatility and volume regimes,
VWAP, liquidity zones, fair value gaps, weekly RSI / 200W MA / ari_20wma) and
returns a schema-validated "Level 0" document.

Copy exactly these files (and nothing from levels 2/3/5/9, simulation,
Telegram or LLM code):

| From `ari_signals/src/` | Purpose |
|---|---|
| `analysis/level_0_generation.py` | `timeseries_info`, the entry point |
| `analysis/document_cleanup.py` | `prune_document` |
| `analysis/level_0.schema.json`, `analysis/validate_level0.py` | Output schema and validator |
| `technicals/calc_*.py`, `technicals/pattern_*.py` (except `calc_load_test_data.py`) | Indicator calculations |
| `technicals/interpret.py`, `technicals/interpretations/*.py` | Interpretation layer |
| `utils/analysis_science.py` | Used by `calc_fibonacci.py` |
| `analysis/strategy_params.py` + `config/strategy_params/default.json` | `level0` parameters only |
| `llmstrategy/compact_state.py` (optional, later) | Compact feature vector, useful for d3's 4,096-token context |

Runtime dependencies: pandas, numpy, scipy, python-dateutil. Nothing else.
Keep the license header of the source repo. The old repo will be archived,
so this copy becomes the maintained version.

## Plan: copy first, freeze a baseline, then fix in the firm repo

The old repo will be decommissioned, so the firm repo becomes the only home
of this code and the fixes go into the code itself, not only the wrapper.

**Step 0 — before touching the old repo (do this first, once):**
generate golden outputs from the *unchanged* ari_signals code on recorded
fixtures (BTCUSDT 1d and one US share, e.g. AAPL 1d; 50+ dates each,
including dates near the start of history and inside an unfinished week).
Store input frames and outputs in `tests/fixtures/ari_golden/` with the
source commit hash. This baseline is the only way to prove later changes
were intended, so it must exist before the repo is archived.

**Step 1 — copy with history (preferred) or as a plain copy.**
Preferred: bring the files over with their git history (for example with
`git filter-repo --paths-from-file` on a clone, then merge into the firm repo
under `src/firm/data/features/ari/`). Otherwise a plain copy, recording the
source commit hash in DECISIONS.md.

**Step 2 — mechanical changes, output must stay identical to the baseline:**
1. Imports: remove the `sys.path.append` hack; convert `from technicals...`,
   `from utils...`, `from analysis...` to package-relative imports. Remove
   `__main__` blocks and the `calc_load_test_data` imports inside them.
2. `technicals/calc_signals.py`: replace `fillna(method="bfill")`
   (deprecated in pandas 2, removed in pandas 3) with `.bfill()`.
3. Parity test against the baseline must pass, byte for byte.

**Step 3 — fix the findings below, one commit and one DECISIONS.md entry
each.** Every fix changes the parity test in a declared way: the test lists
exactly which fields are expected to differ from the baseline and why;
anything else differing fails.

**Step 4 — archive the old repo** (GitHub "Archive", read-only) rather than
deleting it, once steps 0–3 pass.

## The wrapper (the only way the firm calls this code)

```python
def compute_ari_features(
    bars: BarsView,          # from the data plane, closed bars only, as_of applied
    market: MarketProfile,
    timeframe: str,
    params: Level0Params,
) -> FeatureDocument:        # Level 0 JSON + {feature_library_version, as_of, lookback}
```

Rules enforced by the wrapper:

- **Closed bars only.** The data plane never hands in a still-forming candle.
  `as_of` for a bar is its close time (a daily bar dated D is known at
  D+1 00:00 UTC), and the document records that `as_of`.
- **Fixed lookback, same in backtest and paper.** The wrapper always passes
  exactly `params.lookback_bars` rows (default 1,500 for 1d, enough for the
  200-week MA), never "whatever was downloaded". Fewer rows available →
  fields that need more are `None`, and the document says so.
- **One bar, one call.** Features for historical bar t are computed from bars
  up to t only; never compute once on the full series and index back.
- **Versioned output.** Every document carries `feature_library_version`
  (source commit + firm wrapper version) and the dataset version it read.

## Findings from reading the source, and how each gets fixed

| # | Finding | Effect | Fix (in code, step 3) |
|---|---|---|---|
| 1 | Simulation passes a 201-row window (`min_history=200`); the live downloader passes the full downloaded frame | Backtest and live see different features: the 200-week MA needs ~1,400 daily bars so it is `None` in simulation; S/R, Fibonacci and trends use 201 vs up to 366 rows | `timeseries_info` takes an explicit `lookback_bars` and refuses frames of another length; the internal `tail(366)` becomes a named parameter; fields that need more history than given return `None` with a reason |
| 2 | The live path does not appear to drop the still-forming last candle | Live features can change during the bar ("repaint") while backtests use closed bars | `timeseries_info` takes `as_of` and refuses any bar whose close time is after it; the data plane serves closed bars only |
| 3 | Weekly indicators resample including the current, unfinished week | Point-in-time correct, but the weekly RSI / ari_20wma move during the week | Add `weekly_mode: "completed" \| "partial"` (default `completed`) and record it in the document |
| 4 | Pivot and peak detection use centred windows (`rolling(center=True)`, `argrelextrema`, `i ± window`) | Safe only because each call sees data up to "now"; recent pivots confirm later | Keep the maths; enforce "one bar, one call" and add a `confirmed_at` bar index to each pivot so strategies can tell fresh pivots from confirmed ones |
| 5 | `generated_at` is the bar's open timestamp | A daily bar dated D looks known at D 00:00 instead of D+1 00:00 | Record both `bar_open` and `as_of` (bar close) in `state.meta` |

Schema changes from these fixes bump `state.meta.schema_version` to `1.0`
(from `0.1`) and update `level_0.schema.json` in the same commit.

## Acceptance criteria (script-checkable)

- `tests/fixtures/ari_golden/` exists with the source commit hash, created
  from the unchanged ari_signals code before any edit.
- `tests/features/test_ari_parity.py`: after step 2, output equals the
  baseline byte for byte; after step 3, only the fields declared in the
  test's expected-differences list differ, each linked to its finding.
- `tests/features/test_ari_point_in_time.py`: for 50 random bars t, features
  computed with data up to t are identical whether or not later bars exist
  in storage (truncation test).
- `tests/features/test_ari_lookback.py`: backtest mode and paper mode produce
  identical documents for the same bar.
- Every output validates against `level_0.schema.json`.
- `uv run pytest tests/features` passes with no network.

## Out of scope

Levels 2, 3, 5 and 9 (permissions, decisions, arbitration, Telegram), the
LLM trade planner and the simulation engine. The firm has its own verifier,
risk desk and notifier; ari_signals' strategies (e.g. ari_20wma) can later be
ported as firm strategies and go through the verifier like any other.
