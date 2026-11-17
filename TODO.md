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
- [x] (2026-11-16: strategy + instrument + timeframe, openings counted as trials) **OOS vault scope (decide):** the SPEC says the hidden period is opened
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
- [x] (2026-11-16: kept; Coinbase confirmed, Kraken approximate) Listing dates used for the instrument records are approximate
      (Coinbase BTC-USD 2015-01-26, Kraken XBTUSD 2013-09-10); confirm.
- [x] (2026-11-16) Fees: real published entry-tier fees per venue in
      `config/fees/` (Coinbase numbers still to confirm on its signed-in page).
- [ ] Verifier thresholds: kept as v1 defaults (owner, 2026-11-16);
      calibrate once there are more real verifications.
- [ ] Kraken OHLC gives only the last 720 bars: full history needs trade
      aggregation or Kraken's files (check terms). Coinbase pages fine.
- [ ] FX conversion cost not applied in backtests (P&L in quote currency).
- [ ] Strategy loader executes code: sandbox before agent code (M3).
- [x] Trial registry and OOS vault are mirrored into the ledger (M2 phase
      A); the verifier keeps its own state (rule 12: no shared databases).
- [ ] Each verdict should cite the ledger position of the trial count it
      used, so any old report can be reproduced exactly.
- [ ] Legacy captures (ari CSVs: no capture time, last row may be forming)
      can't be normalised; needed only if we backtest on them.
- [ ] mypy runs on `src/firm/core` only (CLAUDE.md); consider adding
      `data` and `verify` (needs pandas typing).
- [ ] Trades are partitioned by year; switch to daily partitions when
      tick volumes arrive.
- [ ] `periods_per_year` supports exchange calendars only for 1d/1w (M6).

## Milestone 2: paper simulator, risk desk, ledger, trade journal

Plan approved 2026-11-16 (owner). Exit check (SPEC): a verified strategy
paper-trades for 7 days unattended; every fill traces to a verdict ID.
One PR per phase, test-first:

- [x] (2026-11-16) **A. Ledger**: SQLite WAL, append-only (UPDATE/DELETE refused), hash
      chain, single writer fed by a queue; the verifier keeps its own state
      (trial registry, OOS vault) and mirrors every event into the ledger;
      one-off import of existing state; firm rule 6 test real.
- [ ] **B. Risk desk**: approve/shrink/reject `OrderProposal`s against
      `config/risk/` limits; kill switches per market and global; heartbeat.
- [ ] **C. Paper simulator**: fills against market data with venue fees,
      spread, slippage, partial fills, lifecycle, resting stops,
      reconciliation; conformance suite xfails become passes.
- [ ] **D. Live feeds**: streaming provider layer (Kraken, Coinbase, Binance
      WebSockets), reconnects, staleness, live data captured. Dependency
      `websockets` approved.
- [ ] **E. Execution and market workers**: one asyncio worker per market;
      bars -> signal -> proposal -> risk -> `build_intent` -> simulator; halts
      on stale feed or lost risk heartbeat; firm rules 1 and 2 tests real.
- [ ] **F. Bus**: NATS JetStream, per-service credentials from `PUBLISHERS`.
      Dependency `nats-py` and the `nats-server` binary approved. In-process
      first (A–E), bus after (owner decision).
- [ ] **G. Trade journal**: rebuilt from the ledger into DuckDB, exportable,
      every row traced to verdict and risk decision.
- [ ] **H. The 7-day run** on the Linux box (24/7); strategy to choose then
      (a 1h-verified strategy trades often enough to exercise the plumbing).

Owner decisions 2026-11-16:

- Paper capital and risk limits are configuration (`config/risk/`, owner
  editable, never agents). Proposed starting values (owner to adjust):
  paper capital £10,000; max position per instrument 20%; per exchange 50%;
  per market 60%; total exposure 100% (no leverage); max single order 10%;
  daily loss limit 3% (halts new orders until 00:00 UTC); max drawdown 10%
  per market and 15% total from peak (kill switches).
- Machines: Mac, DGX and a Linux box are always on. Proposal: the Linux box
  runs the trading stack 24/7 (no GPU needed), the DGX serves models and
  research, the Mac is for development; the Linux box can also back up the
  DGX's stores and act as the heartbeat watchdog.

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

## Web monitoring and real-time instruments (owner request 2026-11-16)

The CLI stays; a web interface monitors everything, live. Builds on the
"Web UI" section above (milestone 4) and the control API.

- [ ] **Live monitoring dashboard**: one page that shows the state of the
      whole firm at a glance: desks and services up/down (bus heartbeats),
      feed health and staleness per market, agents and experiments running,
      strategies by stage, paper positions and P&L, risk usage and halts,
      recent verdicts, alerts. Updates pushed over WebSocket, no refresh.
- [ ] **Real-time instruments**: watchlists of several instruments at once
      (crypto and shares, any venue), live prices, spreads and volume,
      intraday charts with our own bars, the judge's regime label later.
      Instruments can be added/removed from the UI and the CLI
      (`firm market watch BTC-USD ETH-USD NVDA.XNAS`).
- [ ] **Streaming data layer**: a streaming counterpart of the provider
      layer (`firm.data.providers`): Kraken, Coinbase and Binance WebSocket
      feeds behind one interface, normalised to the same bar/trade rows,
      reconnects with backoff, staleness detection (SPEC: stale feed → urgent
      alert, no new paper orders on that market). Live ticks are also
      captured to the store with their capture time, so live and backtest
      data are the same data.
- [ ] Yahoo has no free real-time stream; real-time US shares come from the
      IBKR feed (milestone 6) or another licensed source.
- [ ] Read-only by construction for agents; the owner's actions (kill
      switch, approvals) stay behind confirmation (see "Web UI" above).

## Data providers (2026-11-16)

- [x] One provider layer (`firm.data.providers`): contract, registry,
      kraken, coinbase, binance, yahoo; generic fetch engine and
      `scripts/download_data.py` (replaces `download_crypto.py`).
- [ ] **Owner:** move `claude-staging/commands/update-data.md` into
      `.claude/commands/` (it now uses `scripts/download_data.py`).
- [ ] EODHD provider (US shares with delistings; needs a key in `.env`,
      paid plan) for milestone 6.
- [ ] Yahoo dividends: the chart response carries them; store them as
      corporate actions in `reference/` when shares are verified (M6).
- [ ] Library-backed providers (yfinance, ccxt, ...) fit the same contract
      but are new dependencies: owner approval first.
- [ ] Re-check Binance's and Yahoo's terms of use before any wider use; both
      are personal/research use here and never redistributed.

## Timeframes (owner request 2026-11-16)

- [x] Every layer already keyed by timeframe (raw `tf=`, normalized `tf=`,
      manifests, `bars(..., tf)`); "known at" follows each bar's own close.
- [x] Provider alignment declared and checked: Kraken weeks start Thursday,
      Binance/Yahoo Monday, Yahoo days follow the exchange session; the
      normaliser refuses bars off their declared grid and records the
      alignment in the manifest (normaliser version 2).
- [x] Derived timeframes on one canonical grid (days 00:00 UTC, weeks
      Monday): `firm data build-dataset ... --derive 1m:1h --derive 1d:1w`;
      complete buckets only; stored apart (`derived_from`), read with
      `bars(..., derived_from="1m")`. Verified on the real Coinbase/Kraken data.
- [x] Intraday and weekly tests end to end (`tests/data/test_timeframes.py`).
- [ ] Deriving share timeframes needs the exchange calendar (sessions,
      half days): milestone 6.
- [ ] Deep 1m history: Binance (about 530 requests per instrument-year) is
      the practical source; Kraken serves only 720 minutes, Yahoo ~7 days.
- [ ] Storage: yearly files are fine for 1m on a few instruments; switch to
      monthly partitions if many instruments or ticks arrive.
- [x] (2026-11-16: content key, aliases for legacy entries) **Owner decision: vault key vs re-processed data.** The OOS vault keys a
      candidate by code + params + *dataset version* + window. Re-normalising
      the same bars (e.g. normaliser v2) gives a new dataset version, so
      re-verifying an unchanged strategy on it is refused as "another
      version" in its scope. Proposal: key on a hash of the bars the check
      actually read (content), not the dataset version, so re-processing is a
      replay while new or changed data is still a new candidate.
- [ ] The verifier reads one series; strategies that combine timeframes
      (e.g. 1h signals filtered by 1d trend) need a multi-series view.

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
- [x] GitHub hardening (2026-11-16): secret scanning + push protection,
      Dependabot alerts, read-only workflow permissions, actions pinned to
      SHAs + Dependabot for actions; `main` requires the Linux and
      secret-scan checks (macOS informational).
- [ ] **Owner:** enable push protection for your GitHub account (Settings →
      Code security), per `docs/github-security-setup.md` step 1.
- [x] Branch protection on `main` (2026-11-16): the five CI checks are
      required, branch must be up to date, applies to admins, no force
      pushes or deletion.
- [x] PR #1 merged (2026-11-16).
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

SPEC changes for the owner:

- [x] SPEC v0.2 (2026-11-16, PR "SPEC v0.2"): bus table and order flow,
      configuration (risk profile, venue status, fees, verifier, models),
      data plane store and point-in-time rules, verifier method, firm rules
      3 and 4 wording, milestone 1 done, open questions, planned additions.
      SPEC.md is now the source of truth, changed only through owner-reviewed PRs.

Functional gaps:

- [x] (2026-11-16, `--bars`/`--trades`) `firm data build-dataset` took one provider per run, so a dataset
      covering Coinbase *and* Kraken (like the real-data one) can't be built
      from the CLI; it was built from Python. Accept several
      `--provider:symbol` pairs.
- [x] (2026-11-16, tests/data/test_trades.py) Trades: parsers existed, but `normalize_trades` and
      `Dataset.trades` had no end-to-end test.
- [ ] **Real trades: deferred to milestone 2** (owner, 2026-11-16: no trade
      data source yet). Free: Kraken and Coinbase public trade endpoints
      (no keys; `download_data.py --trades-pages N`), but ~1,000 trades
      per call, so recent trades only, not years of history. Deep tick
      history = a paid vendor (SPEC: Tardis.dev, later). Daily-bar
      strategies don't need trades; the paper simulator does (fills,
      slippage calibration), from live WebSocket feeds plus recent pages.
- [x] (2026-11-16, tests/scripts/) `scripts/download_crypto.py` had no tests (retry logic, pagination);
      test them with a fake `urlopen` (no network).
- [x] (2026-11-16, `STATE_VERSION`, `firm state show`) `state/` had no schema/versioning
      and no tooling to inspect it (`firm state show`?) until the ledger.
- [x] **Owner:** move `claude-staging/commands/update-data.md` into
      `.claude/commands/` (new `--bars/--trades` syntax; results go to
      `private/TODO.md`).
- [x] (2026-11-16) **Owner:** copy `scripts/fetch_claude_skills.py` from Certo, or drop that line
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
