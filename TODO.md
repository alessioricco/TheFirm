# TODO

The single place for everything that still has to be done, at any point in
the project: next actions, owner decisions, milestone work, follow-ups,
known bugs. Check it before starting work; update it before switching task,
so nothing pending lives only in a chat.

Why things were decided goes in `DECISIONS.md`; how modules work goes in
`ARCHITECTURE.md`.

## Status

- [x] Step 0: repository skeleton (2026-11-16)
- [x] Milestone 1: data plane and verifier (crypto), exit check met 2026-11-16
- [ ] Milestone 2: paper simulator, risk desk, ledger, trade journal
- [ ] Milestone 3: research agents on Certo
- [ ] Milestone 4: control and notifications
- [ ] Milestone 5: judge
- [ ] Milestone 6: US shares

## Now

- [ ] **Review milestone 1** on branch `milestone-1` (built on
      `step-0-skeleton`; neither pushed). Merge to `main` when reviewed.
- [x] Data store backup: `data/` (and `state/`) are covered by the owner's
      Time Machine backup of the Mac (2026-11-16). The DGX or any other host
      running the firm will need its own backup.
- [ ] **OOS vault scope (decide):** the SPEC says the hidden period is opened
      "once per strategy". The vault keys by strategy ID only, so after
      `ma_crossover` opened its OOS on Coinbase BTC-USD, verifying it on
      another venue or instrument would be refused. Per strategy (strict,
      current) or per strategy × instrument?
- [ ] **Archive ari_signals?** Not yet: only after the extraction steps 0–3
      pass. Its working tree has 2 modified + 2 untracked files outside the
      extracted code; commit or discard them there before archiving.

## Step 0: done (2026-11-16)

Skeleton built: `src/firm/` packages with interfaces, domain objects, bus
envelope with per-service publish permissions, strict config loader
(jurisdiction, market, risk profiles), `VenueAdapter` + conformance suite,
one xfail placeholder per firm rule, CLI version command. 136 passed,
1 skipped, 17 xfailed, on Python 3.12 and 3.14.

Owner questions answered the same day (details in DECISIONS.md):
Q1 order flow via `OrderProposal` → `RiskDecision` → `build_intent`;
Q2 per-service alert subjects, digest built by the ledger;
Q3 risk limits in `config/risk/`; Q4 crypto in USD;
Q5 `venue_status` schema; Q6 no host assumed.

## Milestone 1: data plane and verifier (crypto)

Built 2026-11-16 on branch `milestone-1` (plan approved with the go-ahead):

- [x] core: timeframes; config: `spread_bps`, fee schedules, verifier thresholds.
- [x] data: store, Kraken/Coinbase parsers (bars + trades, closed bars only),
      synthetic data, normalizer, dataset versions, instrument master
      (delisted kept), `as_of` reader, capped research view.
- [x] Owner-run download script (native REST; ccxt decision in DECISIONS.md).
- [x] verify: strategy protocol, cost model, backtest, 5 checks, verifier,
      deterministic report, exit codes, `firm verify` / `firm data` CLI.
- [x] Invariant tests for rules 4 and 5 are real.
- Exit check (see the milestone report):
  - [x] planted lookahead rejected by the truncation check;
  - [x] clean moving-average crossover passes on the fixture data;
  - [x] same dataset version + strategy → byte-identical reports;
  - [x] `uv run pytest` green with no network and no model.

Follow-ups from milestone 1:

- [x] Real data downloaded 2026-11-16 (Coinbase and Kraken BTC-USD 1d) and
      `ma_crossover` verified on it. Dataset version, verdicts and results
      are private: `private/TODO.md` (git-ignored).
- [ ] Listing dates used for the instrument records are approximate
      (Coinbase BTC-USD 2015-01-26, Kraken XBTUSD 2013-09-10); confirm.
- [ ] Calibrate verifier thresholds (`config/verify/default.yaml`) and fee
      schedules (`config/fees/`): all placeholders (SPEC open question).
- [ ] Kraken OHLC gives only the last 720 bars: full history needs trade
      aggregation or Kraken's files (check terms). Coinbase pages fine.
- [ ] FX conversion cost not applied in backtests (P&L in quote currency).
- [ ] Strategy loader executes code: sandbox before agent code (M3).
- [ ] Trial registry and OOS vault move into the ledger (M2); each verdict
      should cite the ledger position of the trial count it used, so any
      old report can be reproduced exactly.
- [ ] Legacy captures (ari CSVs: no capture time, last row may be forming)
      can't be normalised; needed only if we backtest on them.
- [ ] mypy runs on `src/firm/core` only (CLAUDE.md); consider adding
      `data` and `verify` (needs pandas typing).
- [ ] Trades are partitioned by year; switch to daily partitions when
      tick volumes arrive.
- [ ] `periods_per_year` supports exchange calendars only for 1d/1w (M6).

## ari_signals feature extraction (docs/tasks/extract-ari-signals-features.md)

- [x] Step 0: golden baseline (2026-11-16). Data in the store
      (`data/golden/ari_signals-03a8b8d/`, raw CSVs under `data/raw/`);
      only `tests/fixtures/ari_golden/MANIFEST.json` is in git.
      Source commit `03a8b8d`, 246 cases (BTCUSDT + NVDA, `sim` and `live`
      modes), deterministic across two runs, every output valid against
      `level_0.schema.json`. Integrity test: `tests/features/test_ari_golden_fixture.py`.
- [ ] Step 1 (after milestone 1's data plane): copy with git history
      (`git filter-repo`) or plain copy; record the source commit.
      Source license is **Apache-2.0** (firm is MIT): keep headers, add the
      license/NOTICE under `src/firm/data/features/ari/`.
- [x] Deps approved 2026-11-16. pandas/numpy added (M1); scipy added with
      step 1 when the ari code arrives (python-dateutil comes with pandas).
- [ ] **Library versions (owner decision 2026-11-16):** the firm uses current
      libraries (pandas 3.1.1 today); the ported ari code is adapted to them,
      never the reverse, and nothing depends on the old repo or its env. The
      golden baseline (pandas 2.3.0) stays a reference: parity is exact where
      possible, and every difference caused by a library upgrade is declared
      in the parity test with its cause and tolerance.
- [ ] Step 2: mechanical changes + `tests/features/test_ari_parity.py` byte for byte.
      Byte parity is only meaningful with the baseline's library versions
      (Python 3.11.3, pandas 2.3.0, numpy 2.3.1, scipy 1.16.0). The firm
      env differs (Python 3.12+); decide: pin those versions for the parity
      run, or declare float-level differences from library upgrades.
- [ ] Step 2 also: `technicals/calc_v2_market_regime.py` has the same
      `fillna(method="bfill")` as `calc_signals.py` (the task lists only the latter).
- [ ] Step 3: the 5 findings in the task, one commit + DECISIONS entry each.
- [ ] Step 3, new findings from the baseline:
      6. In live mode with < 200 rows the generator raises
         `ValueError: DataFrame too short for market structure` instead of
         returning `None` fields (recorded as errors in the baseline).
      7. The old repo's `DocumentValidator` fails ~80% of documents that pass
         the JSON schema (`euphoria_risk` / `fake_breakout_risk` missing
         `confidence`, `liquidity_risk` missing `drivers`). Generator and
         validator disagree; pick one contract in step 3.
- [ ] Wrapper `compute_ari_features` + point-in-time and lookback tests.
- [ ] Step 4: archive the old repo once steps 0–3 pass.

## Later milestones

- [ ] M2: generate NATS account permissions from `PUBLISHERS`.
- [ ] M2: invariant test that `OrderIntent` is built only via `build_intent`.
- [ ] M2: who raises "strategy demoted" alerts (drift detection)? Likely
      `alert.urgent.verification`; decide with the journal.
- [ ] M4: digest flags any `venue_status.review_by` date that has passed
      (the loader stays clock-free on purpose).
- [ ] M4: scout "urgent" notes. SPEC says they may arrive as alerts; agents
      publish no alerts now, notes reach the owner via the digest. Would
      need a deterministic relay.

## Known gaps / bugs

- `firm.data.interface.DataPlane` returns `object`: frame types are fixed
  in milestone 1.
- Conformance `test_rejects_unsupported_order_type` is skipped for the
  simulator (it supports every type); it will run for real venues.
- No `.python-version` pin on purpose (host-agnostic); verified on 3.12 and 3.14.
- uv's default cache (`~/.cache/uv`) is not writable from the Claude Code
  sandbox; sessions use `UV_CACHE_DIR=$TMPDIR/uv-cache`. Not a repo issue.
