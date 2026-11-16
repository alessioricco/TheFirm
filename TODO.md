# TODO

The single place for everything that still has to be done, at any point in
the project: next actions, owner decisions, milestone work, follow-ups,
known bugs. Check it before starting work; update it before switching task,
so nothing pending lives only in a chat.

Why things were decided goes in `DECISIONS.md`; how modules work goes in
`ARCHITECTURE.md`.

## Status

- [x] Step 0: repository skeleton (2026-11-16)
- [ ] Milestone 1: data plane and verifier (crypto)
- [ ] Milestone 2: paper simulator, risk desk, ledger, trade journal
- [ ] Milestone 3: research agents on Certo
- [ ] Milestone 4: control and notifications
- [ ] Milestone 5: judge
- [ ] Milestone 6: US shares

## Now: waiting on the owner

- [ ] **Commit step 0.** Everything is uncommitted on `main`. Suggested:
      one commit for the skeleton (package, config, tests, docs) and one for
      the `.gitignore` anchoring fix.
- [ ] **Approve milestone 1 dependencies.** Listed in CLAUDE.md, will add:
      pyarrow (Parquet), duckdb, exchange_calendars. *Not* listed, need
      approval: pandas, numpy (backtest engine and the ari feature library,
      which also needs scipy and python-dateutil).
- [ ] **Go-ahead for milestone 1.** Per `/next-milestone`: show a plan
      first, then build test-first, stop at the exit check.

- [ ] **Back up the data store** (`data/`). It holds the only copy of the
      ari golden baseline outside the old repo, and git will never have it.
      Suggest a backup target before archiving ari_signals.
- [ ] **Archive ari_signals?** Not yet: the task says archive only after
      steps 0–3 pass. Its working tree has 2 modified + 2 untracked files
      outside the extracted code (`src/technical_analysis/`, `src/prompts/`);
      commit or discard them there before archiving.

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

- [ ] Implement the data store layout (ARCHITECTURE.md "Data store"):
      `firm.data` store root resolution, capture writer with `.meta.json`,
      normalizer to Parquet, dataset manifests, instrument master.
- [ ] Synthetic bar generator for tests (no provider data in the repo).

- [ ] Evaluate ccxt vs native Kraken/Coinbase APIs; DECISIONS.md entry.
- [ ] Data plane: ingest Kraken and Coinbase OHLCV bars and trades into
      versioned Parquet with checksums; every read takes `as_of`;
      delisted pairs kept. Recorded fixtures for tests; real downloads only
      in a separate script the owner runs.
- [ ] Backtest engine: event-driven, bar by bar, strategy sees only visible
      data; costs from the market profile.
- [ ] Fee schedules: `costs.commission` names a schedule; numbers TBD.
- [ ] Verifier checks, each a tested module with a typed result:
      lookahead truncation, hidden out-of-sample (opened once, logged),
      parameter sensitivity, cost realism, trial count (pick and document a
      method, e.g. deflated Sharpe ratio).
- [ ] Verifier combines checks into one `Verdict` with ID, report, exit code.
- [ ] Turn invariant placeholders for rules 4 and 5 into real tests.
- [x] ari_signals golden baseline (task step 0), 2026-11-16, see below.
- Exit check (all must hold):
  - [ ] a strategy with planted lookahead is rejected by the truncation check;
  - [ ] a clean moving-average crossover passes on the fixture data;
  - [ ] same dataset version + strategy → byte-identical reports;
  - [ ] `uv run pytest` green with no network and no model.

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
- [ ] Needs approval: pandas, numpy, scipy, python-dateutil as firm deps.
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
