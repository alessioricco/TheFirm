# TODO

The single place for everything that still has to be done, at any point in
the project: next actions, owner decisions, milestone work, follow-ups,
known bugs. Check it before starting work; update it before switching task,
so nothing pending lives only in a chat.

Why things were decided goes in `DECISIONS.md`; how modules work goes in
`ARCHITECTURE.md`.

**This file is public.** Anything about our real data or results (dataset
versions, verdict IDs, metrics, download history) goes in the git-ignored
`private/TODO.md` instead; `scripts/check_repo.py` refuses real IDs here.

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

## Data: incremental updates (owner request 2026-11-16)

Downloading a series again should extend what we have, not start over.

Already true: captures accumulate (write-once, content-hashed), and the
normaliser merges every capture of a series, de-duplicating overlaps and
counting revisions, so a re-download already yields a newer dataset version.

Still to build:

- [ ] Incremental fetch: start from the last stored closed bar (or trade
      cursor) per provider/symbol/tf, with a small overlap to detect
      revisions, instead of the full history (`firm data update`, wrapping
      the download script). Kraken `since`, Coinbase time windows.
- [ ] Gap detection after merge: report missing bars per series, and
      backfill them on the next update.
- [ ] Incremental normalisation: rebuild only the year partitions touched by
      new captures; untouched partitions keep their files (same bytes, same
      names).
- [ ] Dataset lineage: each new version records the version it extends;
      a `latest` pointer per series for convenience. Old versions stay
      valid and reproducible; backtests always cite an exact version.
- [ ] Revision policy: a changed closed bar is counted today; decide whether
      it should also raise an alert or block the new version.
- [ ] Scheduling (later, with the market workers): periodic updates with
      staleness alerts (SPEC "feed staleness").

## Strategies on real data (owner request 2026-11-16)

Synthetic data stays for unit tests (public repo, no network in tests), but
strategies are judged on real past data whenever we can download it.

- [ ] Verdicts say what data they rest on: the dataset manifest records each
      file's capture method (`api` / `synthetic` / `legacy_import`) and the
      report shows it. A verdict on synthetic data is labelled as such and
      can never promote a strategy to paper trading.
- [ ] `firm verify` on a missing series/window offers to fetch it first
      (via the incremental update), then builds the dataset version.
- [ ] Real-data test tier: tests that run the verifier on real datasets in
      the store, skipped as UNVERIFIED where the store is absent (like the
      ari baseline). Run them on the Mac and the DGX before a milestone closes.
- [ ] Reference real datasets per market (e.g. BTC-USD, ETH-USD 1d on
      Coinbase and Kraken), refreshed by incremental updates, so every
      strategy is checked on the same data.
- [ ] Prefer the longest honest history: Coinbase pages back to 2015, Kraken
      OHLC only 720 bars (see Milestone 1 follow-ups).

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
- [ ] Step 2: mechanical changes (imports, pandas 3 fixes) +
      `tests/features/test_ari_parity.py`: exact where possible, every
      library-caused difference declared (decision above).
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

## Technical analysis documents on demand (owner request 2026-11-16)

Generate a technical-analysis document for any instrument from a dataset
version, using the ported ari feature library. Depends on the ari
extraction (steps 1–3 and the `compute_ari_features` wrapper).

- [ ] `firm ta <instrument> --venue V --tf T [--as-of TIME] [--dataset ds-...]
      [--format json|md|html]`: computes the Level 0 document for the last
      closed bar at `as_of` (default: now), through the wrapper only (closed
      bars, fixed lookback, one bar one call).
- [ ] Every document records dataset version, feature-library version,
      `as_of` and lookback, so it can be regenerated exactly and used
      point-in-time later (e.g. by the judge or in post-mortems).
- [ ] Human-readable rendering (Markdown/HTML) from the JSON. The old repo
      has renderers under `technical_analysis/` (not part of the extraction):
      decide whether to port one or write a fresh deterministic renderer.
- [ ] Output goes to the git-ignored store/state (never git: it contains
      prices), tagged private; served later by the control API and web UI.
- [ ] Optional narrative summary by a local model (milestone 5), clearly
      marked as model text. Documents are information for the owner and
      features for research: never an input to the order path.
- [ ] Batch mode: documents for a watchlist after each market's close,
      linked from the daily digest (milestone 4).

## Ask the firm: an LLM over our data and reports (owner request 2026-11-16)

A model that answers questions ("how did ma_crossover do out of sample?",
"what does the TA say for BTC-USD today?", "why was v-0ef9... rejected?")
from our data, reports and ledger.

- [ ] **Decide the approach (spike).** Proposal, to confirm:
      - Most of our knowledge is *structured* (Parquet via DuckDB, verdict
        reports, dataset manifests, TA documents, later the ledger and
        journal). For that, **tool calling** beats a vector RAG: the model
        calls typed, read-only functions (`list_verdicts`, `get_report`,
        `bars(as_of)`, `ta_document`, `dataset_info`, maybe read-only SQL on
        DuckDB) and the numbers come from the tools, not from the model.
      - **RAG only for unstructured text**: SPEC, DECISIONS, notes, papers,
        later news/scout notes. Small corpus: start with plain full-text
        search (DuckDB FTS or ripgrep); add embeddings only if that falls short.
      - Delivery: the **read-only MCP endpoint** the SPEC already plans on
        the control API (milestone 4), so any MCP client (local agent, Claude
        Code while building) uses the same tools; plus `firm ask "..."`.
- [x] Model per role, Q&A approach and egress policy are in
      `config/models.yaml` (hosts/keys in `.env`), validated by the loader.
- [ ] Runs on **local models** (Ollama, the `ask` role in `config/models.yaml`):
      reports, results and the journal are private (firm rule 8). A cloud
      model only with the owner's per-run opt-in, logged with cost.
- [ ] Read-only by construction: no tool can change state, place orders,
      touch risk or config (firm rules 1, 3). Tool outputs are data, never
      instructions (golden rule 11).
- [ ] Answers cite their sources (verdict IDs, dataset versions, report
      paths, `as_of`), so every number can be checked.
- [ ] Evaluation: a small set of questions with known answers from our own
      reports, run against candidate local models (Certo's benchmark idea).

## Web UI for managing everything (milestone 4; owner request 2026-11-16)

One control API serves the web UI, the CLI and the read-only MCP endpoint,
so anything clickable is also scriptable and both always show the same
state (SPEC "Control and notifications").

- [ ] **Decide the stack** (SPEC open question): Certo's server mode plus a
      light frontend, or something else. Constraints: runs on any host (not
      tied to the DGX), works on the Mac browser, no build step if possible,
      no data or secrets in the repo.
- [ ] Pages (SPEC table, plus what we've added since):
      - **Agents**: start/pause/stop, assign research tasks, budgets and
        model ladders (from `config/models.yaml`), live runs and verdicts.
      - **Strategies**: stage (idea → verified → paper), verification
        reports (the five checks, verdict ID, dataset version), approve
        promotions.
      - **Data**: the store (captures, series, gaps, revisions), dataset
        versions and lineage, incremental updates, real vs synthetic.
      - **Markets**: watchlists, prices and charts, market hours, feed
        health, judge regime labels.
      - **Technical analysis**: on-demand TA documents (render JSON/MD).
      - **Ask the firm**: question box over our data and reports, with cited sources.
      - **Paper portfolio**: positions, fills, P&L per strategy and total in
        GBP, live-vs-backtest drift.
      - **Ledger and journal**: search any decision or order and replay why;
        journal filters by strategy, market, date; exports.
      - **System**: risk limits (read-only view), model server load, GPU
        memory, kill switches (per market and global, with confirmation).
- [ ] Owner-only actions with confirmation: promotions, kill switch, and
      the manual approval that firm rule 7 requires to enable live trading.
      Agent-facing endpoints can't do any of them (firm rules 3, 7).
- [ ] Access: private network only (e.g. Tailscale), authenticated, never on
      the public internet; WebSocket for live updates.
- [ ] Tests: API contract tests shared by UI and CLI; browser tests for the
      main flows (the `webapp-testing` skill in SKILLS.md, milestone 4).

## Gaps found in the 2026-11-16 project review

Public repo (do before making it public):

- [x] **README** for a public audience (2026-11-16): what the firm is, paper trading
      only, **not financial advice and no signals for others**, how to
      install and run (`firm data synthetic` → `build-dataset` → `verify`),
      the data policy (no data in git), licence. Today it is two lines.
- [x] **Pre-commit guard** (2026-11-16, `.githooks/pre-commit` +
      `scripts/check_repo.py`; enable per clone with
      `git config core.hooksPath .githooks`): run the
      no-data and secrets checks before every commit. The pytest guard only
      helps if tests run, and CI runs only after the push (too late for a
      public repo).
- [x] **CI** (2026-11-16, `.github/workflows/ci.yml`): verified on PR #1,
      all five checks green (Linux and macOS arm64, Python 3.12 and 3.14,
      gitleaks over the full history). macOS arm64 runners can queue for a
      long time under GitHub capacity limits; a job GitHub cancels with "not
      acquired by Runner" is capacity, not code: re-run it.
- [x] Secret scanning: pattern checks in the hook and CI, gitleaks over the
      full history in CI.
- [ ] **Owner, when the repo goes public:** follow
      `docs/github-security-setup.md` (secret scanning, push protection,
      required CI checks on `main`, actions pinned to SHAs, Dependabot).
- [x] Branch protection on `main` (2026-11-16): the five CI checks are
      required, branch must be up to date, applies to admins, no force
      pushes or deletion.
- [ ] **Owner:** merge PR #1.
- [x] **Owner: move `settings.json` (repo root) to `.claude/settings.json`**
      and commit it: denies `.env`/secrets and force pushes, asks before
      edits to risk/jurisdiction/verifier config, CI, the guard, and pushes
      (see `docs/github-security-setup.md` §6).
- [x] **Owner: move the staged commands into `.claude/commands/`** and commit:
      `mv claude-staging/commands/*.md .claude/commands/ && rmdir -p claude-staging/commands`.
      Fixes `add-market-profile`, `add-venue-adapter` (synthetic fixtures),
      `certo-task` (`docs/tasks/`, no private data), `next-milestone` (guard
      + checks before stopping); adds `add-strategy`, `update-data`,
      `pre-push`, `review-docs` (listed in SKILLS.md).
- [ ] Licence notice: when the ari code (Apache-2.0) is copied in, add its
      LICENSE/NOTICE and say so in the README (the firm is MIT).

SPEC changes for the owner (the SPEC is owner-only; these decisions differ):

- [ ] Bus table: execution publishes `order.proposed` and risk subscribes to
      it (not `order.intent`); ledger publishes `digest.daily`; alerts are
      `alert.<level>.<service>`; `risk.heartbeat`, `control.command` exist.
- [ ] Configuration: the per-exchange exposure cap lives in
      `config/risk/`, not the market profile; `venue_status` in the
      jurisdiction profile; `config/models.yaml`, `config/fees/`,
      `config/verify/` exist.
- [ ] Open questions: name (`firm`) and repo placement (own repo using
      Certo) look settled; verifier thresholds have placeholders and a
      method (DECISIONS.md); the indicator item is in progress.

Functional gaps:

- [ ] `firm data build-dataset` takes one provider per run, so a dataset
      covering Coinbase *and* Kraken (like the real-data one) can't be built
      from the CLI; it was built from Python. Accept several
      `--provider:symbol` pairs.
- [ ] Trades: parsers exist and are unit-tested, but `normalize_trades` and
      `Dataset.trades` have no end-to-end test, and no trades have been
      downloaded yet (SPEC milestone 1 says "bars and trades").
- [ ] `scripts/download_crypto.py` has no tests (retry logic, pagination);
      test them with a fake `urlopen` (no network).
- [ ] `state/` (trials, OOS openings, reports) has no schema/versioning
      and no tooling to inspect it (`firm state show`?) until the ledger.
- [ ] Copy `scripts/fetch_claude_skills.py` from Certo, or drop that line
      from SKILLS.md.

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

- Conformance `test_rejects_unsupported_order_type` is skipped for the
  simulator (it supports every type); it will run for real venues.
- No `.python-version` pin on purpose (host-agnostic); verified on 3.12 and 3.14.
- uv's default cache (`~/.cache/uv`) is not writable from the Claude Code
  sandbox; sessions use `UV_CACHE_DIR=$TMPDIR/uv-cache`. Not a repo issue.
