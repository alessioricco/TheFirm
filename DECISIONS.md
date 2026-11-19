# Decisions

Why past choices were made. One dated entry each; newest last. Read the
relevant entry before changing a settled behaviour.

## 2026-11-16 — pydantic v2 and PyYAML as the first runtime dependencies

Approved by the owner. pydantic gives frozen, `extra="forbid"` models for
domain objects, bus payloads and config with one mechanism, plus JSON schema
export for the bus contract test. PyYAML (`safe_load` only) reads profiles.
Build backend is hatchling (build-time only).

## 2026-11-16 — Every typed object is frozen and refuses unknown fields

`firm.core.base.FirmModel`: `frozen=True, extra="forbid"`. An object crossing
a boundary is exactly its schema and can't be mutated. IDs and reasons are
non-empty strings (`Id`, `Reason`) so a missing value can't hide as `""`.
Money and quantities are `Decimal`; timestamps must be timezone-aware.

## 2026-11-16 — Verdict outcome is pass / fail / unverified, derived from checks

Golden rule 2 says anything unchecked is *unverified*, never passing. Each
`CheckResult` is `passed | failed | not_run`; `Verdict` refuses an `outcome`
inconsistent with its checks (any failed → fail, else any not_run →
unverified, else pass). The verifier can't mislabel a verdict even by bug.

## 2026-11-16 — RiskDecision quantity must match its action

Reject → no quantity; approve/resize → positive quantity. Enforced by schema.

## 2026-11-16 — Bus subject names

The SPEC gives `order.*` and `fill.*` as families. Concrete subjects:
`order.intent`, `order.update` (lifecycle state in the payload), and
`fill.executed`, all owned by execution. Added `risk.heartbeat` (execution
halts when it stops, golden rule 12) and `control.command` (owner commands,
kill switch only). Alerts and digest: see "Alert publishers" below. A snapshot test pins the whole permission table, so any change is a
deliberate, reviewed edit. Schema version starts at 1 for every subject.

## 2026-11-16 — Config loader is stricter than the SPEC example requires

Refuses: unknown keys at any depth, `live_trading: true` (v1 constant
`V1_LIVE_TRADING_ALLOWED = False`), `leverage_max > 1.0` ("no leverage in
v1"), any recipient other than `owner`, `include_delisted: false`, a market
file whose `market:` differs from its file name, and market `broker.live`
venues not in the jurisdiction's `allowed_venues`. Crypto (`calendar: 24x7`)
must have `funding_rates` and no `corporate_actions`; exchange-calendar
markets the reverse. (Exposure caps moved to the risk profile, see below.) Refusing more is
the conservative side for anything near risk. `data.*` and `broker.live`
accept a name or a list (crypto has two venues).

## 2026-11-16 — VenueAdapter is an ABC with async methods; the mode gate is in its constructor

Market workers are asyncio, so adapter I/O is async. `VenueAdapter.__init__`
calls `ensure_mode_allowed(mode, live_trading=...)` and checks the mode is in
the adapter's declared capabilities, so no subclass can skip the gate.
`submit` takes an `OrderIntent` only, which can't exist without both IDs.
The paper simulator supports `paper` mode only.

## 2026-11-16 — Placeholder tests are `xfail(strict=True)`

Invariant placeholders (one per SPEC firm rule) and not-yet-built
conformance tests are strict xfails, so an unexpected pass fails the run and
forces the marker off once the behaviour exists. Conformance xfails also set
`raises=NotImplementedError`, so any *other* failure is a real failure.
Schema-level partial enforcement of rules 1, 2, 7 and 10 is tested now in
`tests/core` and `tests/config`.

## 2026-11-16 — Desks import only `firm.core`

`tests/core/test_import_boundaries.py` parses every module and fails if a
desk imports another desk (golden rule 12). `firm.core` imports nothing else.

## 2026-11-16 — CLI uses argparse

No new dependency for a version command. Revisit when the control API lands.

## 2026-11-16 — `.gitignore` entries anchored to the repo root

`data/`, `ledger/`, `models/` were unanchored and would have ignored
`src/firm/data/` and `src/firm/ledger/`. Now `/data/`, `/ledger/`,
`/models/`. Parquet under `tests/fixtures/` is re-included for recorded fixtures.

## 2026-11-16 — Order flow: OrderProposal, then RiskDecision, then OrderIntent

Owner approved (TODO Q1). The SPEC had risk subscribing to `order.intent`,
but an `OrderIntent` must already carry a `risk_decision_id`. Now:
execution publishes an `OrderProposal` (sized, has `verdict_id`, *no* risk
ID) on `order.proposed`; risk answers with a `RiskDecision` that names the
`proposal_id`; execution then calls `firm.core.domain.build_intent`, the
only sanctioned constructor, and publishes the `OrderIntent` on
`order.intent` before submitting it. `build_intent` refuses a decision for
another proposal or verdict, a rejection, an `approve` that changes the
quantity, and a `resize` that doesn't shrink it. Everything else is copied
from the proposal, so risk approves exactly what gets sent.

## 2026-11-16 — Alert publishers: per-service alert subjects, digest from the ledger

Owner question Q2. The SPEC named the notifier as the *receiver* of
`alert.*`/`digest.*` (it fans out to connectors: Telegram first) but no
sender. Each deterministic desk now raises its own alerts on a subject
namespaced by its name, keeping one owner per subject: `alert.urgent.risk`
(kill switch, drawdown, halts), `alert.urgent.execution` (feed stale, venue
disconnected), `alert.action.execution` (orders and fills). The ledger
service, which sees everything, builds `digest.daily`. The notifier
subscribes to `alert.>` and `digest.>`. Agents publish no alerts. A test
checks that every `alert.*` subject ends in its owner's name.

## 2026-11-16 — Risk limits live in their own profile

Owner approved (TODO Q3). `config/risk/<name>.yaml` (`RiskProfile`) holds
limits; `exposure_cap_per_exchange_pct` moved there from the crypto market
profile, which the loader now refuses. Market profiles stay readable by
research agents (costs, calendars); risk and jurisdiction profiles are
neither readable nor writable by agents (firm rule 3). `load_config` takes a
risk profile name (default `default`) and requires limits for every loaded
market, plus an exposure cap for every 24x7 market.

## 2026-11-16 — Crypto is traded in USD-quoted pairs

Owner decision (TODO Q4). `currency: USD`; conversion to GBP costs
`fx_conversion_bps`.

## 2026-11-16 — Venue regulatory status in the jurisdiction profile

Owner approved (TODO Q5). `venue_status: {venue: {regulator, status, as_of,
review_by?, note?}}`, status one of `authorised | registered | pending |
not_permitted` (generic, not UK-specific). Every allowed venue needs an
entry; a `not_permitted` venue can't be allowed. Values for the UK copy the
SPEC's statements as of 2026-11-15, with `review_by: 2027-10-01` for crypto
venues (UK regime change). The loader doesn't compare dates with today, so
loading stays deterministic; flagging an expired review is a digest job.

## 2026-11-16 — Host-agnostic: no machine is assumed

Owner decision (TODO Q6). Services may move off the DGX Spark one day, so
code assumes no particular host, OS (beyond macOS/Linux), or GPU. Hosts are
configuration (`.env`: `OLLAMA_HOST`, `NATS_URL` with neutral example
names). `requires-python >=3.12` with no version pin; the suite was run on
3.12 and 3.14. CLAUDE.md and the SPEC still describe the DGX as the v1
deployment target; that's deployment, not a code assumption.

## 2026-11-16 — ari_signals golden baseline: how it was frozen

Step 0 of docs/tasks/extract-ari-signals-features.md. Produced by
`scripts/ari_golden/generate_baseline.py`, run with the old repo's own
`.venv` (Python 3.11.3, pandas 2.3.0, numpy 2.3.1, scipy 1.16.0) so the
code ran exactly as it did there; the old repo was only read. The script
refuses to run if any extracted file differs from HEAD (`03a8b8d`).

- **Data:** the old repo's recorded CSVs (`data/simulation/`, not in its
  git), copied byte for byte. NVDA stands in for "e.g. AAPL": it was the US
  share available locally, so no network download was needed.
- **Two modes**, mirroring both old call paths, because finding #1 is
  exactly their difference: `sim` (201-row window, dates floored, as
  `run_simulation_fast`) and `live` (all rows up to the bar, as
  `OHLCVDownloaderService`).
- **Cases:** fixed edges (bars 200/201, 365–367, 400, last, the first bar
  with 200 weekly bars ±1, mid-week bars) + 40 seeded random bars; live
  also bars 30/60/120/199. Failures are recorded as part of the baseline.
- **Params:** whole `config/strategy_params/default.json` for both symbols
  (the simulation would have used a BTCUSDT override if present; the task
  says default).
- **Serialisation:** canonical JSON (`sort_keys`, compact separators, NaN
  allowed, Timestamp → isoformat, numpy scalar → `.item()`), gzip with
  `mtime=0`; the checksum is of the uncompressed bytes. Key order is
  deliberately not part of the contract.
- **Determinism:** two full runs gave identical bytes for all 246 cases.

## 2026-11-16 — Market-data store layout

Owner approved. Git-ignored `$FIRM_DATA_ROOT` (default `<repo>/data/`) with
immutable zones `raw/` (as received + `.meta.json` per capture),
`normalized/` (Parquet, firm schema), `datasets/` (versioned manifests that
backtests cite), `reference/` (instrument master), plus `golden/` for frozen
test baselines (added for the ari baseline, which is derived data and not a
provider capture). Hive-style `key=value` paths; raw keyed by provider and
provider symbol, normalized by venue and firm instrument ID (`BASE-QUOTE`,
`TICKER.MIC`). Unadjusted prices only, adjusted at query time. Full rules
in ARCHITECTURE.md "Data store". Why: raw-as-received makes every number
traceable and everything else rebuildable; content-hashed dataset manifests
give byte-reproducible backtests; key=value paths are queryable as-is.

## 2026-11-16 — The repo is public: no data in git, ever

Owner rule. No market data, derived prices, datasets or models in git.
Enforced three ways: `.gitignore` (data store + common data formats),
`tests/repo/test_no_data_in_repo.py` (walks the working tree for data
suffixes and files > 200 KB), and test fixtures are synthetic, generated in
code. Consequence: the ari golden baseline moved to the store; git keeps
only `tests/fixtures/ari_golden/MANIFEST.json` (checksums, dates, no
prices). On a machine without the store those tests skip as UNVERIFIED.
The store needs its own backup: it now holds the only copy of the baseline
outside the old repo.

## 2026-11-16 — ari baseline relocated into the store

The 2026-11-16 run first wrote to `tests/fixtures/ari_golden/`. Before
any commit, the two CSVs were imported as legacy captures
(`raw/provider=binance/kind=bars/symbol=BTC-USDT/tf=1d/`,
`raw/provider=yahoo/kind=bars/symbol=NVDA/tf=1d/`, `captured_at: null`,
original file mtime recorded) and the outputs moved to
`golden/ari_signals-03a8b8d/outputs/`. All checksums verified after the
move; the manifest records store paths. The generator now refuses an
`--out` inside the repo tree but outside the store.

## 2026-11-16 — Crypto connectivity: native public REST, not ccxt

Milestone 1 evaluation. Data ingestion uses the venues' public REST APIs
directly, through the standard library (`scripts/download_crypto.py`,
parsers in `firm.data.providers`). Why: the store keeps responses exactly
as received, and ccxt hands back its own normalised structures, not the
venue's bytes; the parsers are ~100 lines; no new dependency; and ccxt does
not lift venue limits (Kraken OHLC serves only the last 720 bars either
way). Revisit ccxt for the order-path adapters (sandbox/live), where its
value is larger. Known limits: Kraken OHLC 720 bars per timeframe; Coinbase
300 candles per call, no 4h/1w; Coinbase trade `side` is the maker's side
(stored as the taker's).

## 2026-11-16 — Point-in-time policy for market data

A bar is known at its close; `as_of` filters on `close_time <= as_of`. The
still-forming bar a venue returns is dropped at parse time using the
capture's `captured_at`, so it never enters the store. A capture with an
unknown capture time (legacy import) can't be normalised. `captured_at` is
recorded on every capture but not used to filter: closed exchange bars do
not change, and if two captures disagree on a closed bar the later one wins
and the disagreement is counted (`revisions` in the dataset manifest).
Revisable data (corporate actions, delistings) will need knowledge-time
filtering when it arrives. Instrument status is evaluated as of the query,
so a later delisting never leaks backwards.

## 2026-11-16 — Normalised storage and dataset versions

Parquet (zstd) with int64 UTC-nanosecond timestamps (no timezone handling in
DuckDB or pandas metadata to drift), partitioned by year, file name =
first 8 hex of the file's sha256. A dataset version `ds-<sha12>` is the hash
of its manifest content (normaliser version + every file and reference
record with checksums); no timestamps inside, so rebuilding from the same
captures gives the same version. Opening a dataset verifies every checksum.

## 2026-11-16 — Backtest timing and cost model

Event-driven over bars: the target exposure decided at bar i's close fills
at bar i+1's open (no same-bar fills); the gap close→open is held by the
previous position. Cost per unit of exposure traded = taker fee + half the
spread + slippage, all in bps from the market profile and its fee schedule
(`config/fees/`). Targets must be in [0, 1]: spot, no leverage, no shorting
in v1. `costs.spread_bps` was added to market profiles because bar data has
no quotes. FX conversion cost is not applied (P&L stays in the quote
currency); TODO when portfolio P&L in GBP arrives (milestone 2).

## 2026-11-16 — Verifier checks, order and thresholds

Five checks, each its own module returning a `CheckResult`:
lookahead truncation (re-run with data cut at up to 250 decision times;
signals up to the cut must be identical within 1e-12), cost realism (net
Sharpe with profile costs, and with costs ×2), parameter sensitivity (every
numeric parameter ±10% and ±20%; each perturbed Sharpe ≥ half the base; a
perturbation the strategy rejects counts as a collapse), trial count
(deflated Sharpe ≥ 0.95), hidden out-of-sample (last 25% of the window).
The four in-sample checks run first; the hidden period is opened only if
all pass, so a strategy can't spend its one opening on a rejected version.
Too little data → every check `not_run` → verdict UNVERIFIED (exit 2),
never PASS. All thresholds are placeholders in `config/verify/default.yaml`
(SPEC open question); the report records the config's sha256.

## 2026-11-16 — Trial-count correction: deflated Sharpe ratio

Bailey & López de Prado (2014). SR0 = expected maximum Sharpe of N trials
under the null; DSR = probability the observed per-bar Sharpe beats SR0
given sample length, skewness and kurtosis. N = the **global** trial count
from the registry (all experiments), so more searching raises the bar. The
variance of Sharpe ratios comes from recorded trials when at least two have
one, otherwise from the null approximation 1/(T−1). The registry is
idempotent on `trial_id`; a hand-written strategy's in-sample run is one
trial whose ID hashes code + params + dataset + window, so re-verifying the
same thing is not a new trial. A candidate citing unrecorded trials fails.

## 2026-11-16 — OOS vault: first opening, replay, refusal

Firm rule 4 says the final OOS is opened once per strategy. The vault keys
openings by strategy ID and a candidate key (code sha + params + dataset +
window). The first request opens and seals the result hash. The identical
candidate may be re-verified as a *replay* (it learns nothing new) and must
reproduce the sealed hash; this is what makes reports reproducible. Any
other version of the same strategy is refused and fails the check. Every
attempt is logged. Vault and trial registry are JSON lines under
`$FIRM_STATE_ROOT` (default `./state`, git-ignored) until the ledger exists
(milestone 2).

## 2026-11-16 — Reports are deterministic; verdict ID is their hash

The report has no wall-clock time; keys are sorted; floats are rounded to
10 significant digits; non-finite numbers become null. `verdict_id =
"v-" + sha256(report body)[:16]`. `issued_at` exists only on the `Verdict`
object (for the ledger). Same dataset version + candidate + global trial
count → same bytes, including after rebuilding the dataset from the same
captures. The trial count is a real input: when any other trial is recorded
in between, the trial-count check (and so the verdict ID) changes, by design
(firm rule 5). Seen in the milestone 1 CLI run; pinned by
`test_report_changes_only_in_trial_count_when_global_trials_grow`. To
reproduce an old report exactly, the registry state at that time is needed:
the ledger (milestone 2) should record the trial-count position in each verdict.

## 2026-11-16 — Synthetic fixtures for the exit check

Tests use only synthetic data (public repo). The generator is a
regime-switching random walk with persistent drifts, written in Kraken's
exact OHLC format (720-bar overlapping pages with the forming bar). Its
defaults (drift 0.5%/day, vol 1.5%) were chosen so that the clean crossover
has a real edge: it passed on 8 of 8 seeds tried; at drift 0.4%, vol 2% it
passed 5 of 8 and the verifier correctly rejected the weak ones. The seed
was not searched for a pass; the setting was.

## 2026-11-16 — Import boundaries: data is shared, control composes

`firm.data` is a read-only, point-in-time library in v1 and every desk may
import it (and `core`); `data` imports only `core`. `control` (CLI, later
the API) is the composition root and may import any desk; nothing imports
`control`. Desks still never import each other.

## 2026-11-16 — Tests block the network

`tests/conftest.py` makes any socket connection during a test fail, so "no
network in tests" is enforced, not assumed.

## 2026-11-16 — Current libraries; the ari code adapts to them

Owner decision. The ari_signals code will be obsolete; the firm does not
depend on it, its repo or its environment. The firm tracks current library
versions (pandas 3.1.1, numpy 2.5 today) and the ported ari code is changed
to fit them (e.g. pandas 3 removed `fillna(method=...)`; other deprecations
get fixed the same way). The golden baseline, made with pandas 2.3.0, stays
the reference for intent: the parity test passes byte for byte where it can,
and declares each difference caused by a library upgrade, with its cause and
a numeric tolerance. A difference that is neither intended (task findings)
nor declared fails. This replaces the task file's "byte for byte after step 2".

## 2026-11-16 — Downloader retries transient failures

The first real download hit `http.client.IncompleteRead` on a chunked
Coinbase response while curl got the same URL fine. `fetch` now retries up
to 4 times with exponential backoff on incomplete reads, connection errors,
timeouts, HTTP 429 and 5xx; other HTTP errors fail at once. Captures are
write-once and content-hashed, so a re-run after a failure is safe:
identical windows become duplicate captures that the normaliser merges.

## 2026-11-16 — Model choices in config/models.yaml; .env holds only hosts and keys

Owner rule. Which model serves each role (research, fast_worker, judge,
ask), the Q&A approach (tools; full-text search; embeddings optional) and
the egress policy (cloud budget, default 0; private work to cloud only by
per-run opt-in; every cloud call logged) live in `config/models.yaml`,
validated strictly by `load_models_config`. `.env` keeps only secrets and
hosts (`OLLAMA_HOST`, provider keys); the cloud budget moved out of `.env`
because it is policy, not a secret. A test fails if any configured model
name appears in `src/`. Only `provider: ollama` is accepted for now; cloud
providers arrive with Certo's ladder.

## 2026-11-16 — Public-repo guard: one checker, three places

`scripts/check_repo.py` (standard library only) refuses data files and
paths (`data/`, `state/`, data suffixes), files over 200 KB, `.env`-like
files, secret patterns (private keys, cloud/GitHub/model-provider keys,
Telegram tokens, assigned secrets) and home-directory paths. It runs in the
pre-commit hook (staged content, before anything is committed), in the
tests (working tree) and in CI (checkout). A plain git hook in `.githooks/`
(enabled with `git config core.hooksPath .githooks`) instead of the
`pre-commit` package: no new dependency, and the check needs no
environment. CI also runs gitleaks over the full history.

## 2026-11-16 — CI matrix

GitHub Actions: Linux arm64 (`ubuntu-24.04-arm`) and macOS arm64
(`macos-latest`; `macos-14` was refused as unsupported on the first run), Python 3.12 (floor) and 3.14 (latest), `uv sync --locked`
so a stale lockfile fails, then guard, ruff, mypy, invariants, all tests.
Not run yet: it becomes verified with the first push.

## 2026-11-16 — Synthetic and real data never mix

Found while writing the README: `firm data synthetic` defaulted to the real
store and the same series as the downloaded Kraken data, so the normaliser
would have merged invented and real bars. Now the normaliser refuses a
series whose captures mix `synthetic` with any other method, and
`firm data synthetic` refuses a store that holds real captures of that
series. The README demo uses a separate store (`./data-demo`, git-ignored).

## 2026-11-16 — Public TODO, private TODO

Owner rule, after `/pre-push` found real verification results in TODO.md.
`TODO.md` is public and holds the work. `private/TODO.md` (git-ignored,
backed up with `data/` and `state/`) holds anything tied to our real data:
download history, dataset versions, verdict IDs, metrics, strategy
parameters that matter. `scripts/check_repo.py` refuses `private/` paths
and any real-looking dataset (`ds-` + 12 hex) or verdict (`v-` + 16 hex) ID
in committed files. The results that had already been written to TODO.md
were moved to the private file and scrubbed from the unpushed history
(`git filter-branch --index-filter`, TODO.md only) before the first push.

## 2026-11-16 — First CI run, and a narrow gitleaks allowlist

PR #1's first run: both Linux arm64 jobs (Python 3.12 and 3.14) passed.
macOS failed before starting ("configuration 'macos-14-arm64-us-default'
is not supported"): switched to `macos-latest`. gitleaks flagged one
"leak": the fake API key that `tests/repo/test_no_data_in_repo.py` plants on
purpose to test the guard. It stays in history, so `.gitleaks.toml` extends
the default rules with an allowlist for that exact fake value only; any
other secret still fails. `actions/checkout` moved to v5 (Node 20 deprecation).

## 2026-11-16 — Functional gaps closed after milestone 1

- `firm data build-dataset` takes repeatable `--bars PROVIDER:SYMBOL:TF`
  and `--trades PROVIDER:SYMBOL`, so one dataset version can span venues
  and kinds from the CLI. The old `--provider/--symbol/--tf` still works.
- Trades are tested end to end with synthetic Kraken and Coinbase payloads
  (overlapping pages de-duplicated by trade ID, Coinbase maker side flipped
  to the taker's, `as_of` and the capped view respected).
- The downloader is tested with a fake network: transient errors are
  retried, client errors are not, Coinbase windows are contiguous and give
  gap-free bars, Kraken trades follow the `last` cursor. It gained
  `--until` (default now) and `main(argv)` for that.
- `state/` carries `STATE_VERSION` (schema 1), stamped on first write;
  readers refuse another version rather than misread it. State written
  before the stamp reads as "unversioned" (same format) and is stamped on
  its next write. `firm state show` summarises trials, vault events and
  reports, read-only.

## 2026-11-16 — SPEC.md becomes the source of truth (v0.2)

Owner decision (option B of three: re-export from the design doc, edit
SPEC.md, or keep deviations only here). `docs/SPEC.md` was an export of a
design doc; it is now the source of truth, changed only through a pull
request the owner reviews and merges. v0.2 folds in the decisions recorded
above (order flow, bus subjects, config split, data store, point-in-time
rules, verifier method, host-agnostic deployment, public-repo rule) and the
scope the owner added (incremental updates, real-data verdicts, TA
documents, "ask the firm", web UI pages). Firm rule 3 now also covers the
verifier thresholds, and rule 4 states the replay/refusal behaviour, both
matching what the code already does.

## 2026-11-16 — macOS informational; GitHub hardening applied

macOS arm64 jobs went unstarted (GitHub capacity) on all three PRs, each
time blocking `main` for 15 minutes until a re-run. Owner decision: macOS
stays in the CI matrix but is informational; the required checks on `main`
are the two Linux arm64 jobs (Python 3.12 and 3.14, so both versions stay
gated) and the gitleaks scan. Applied with `gh` and read back: secret
scanning and push protection enabled, Dependabot alerts enabled, default
workflow permissions read-only. Actions are pinned to the commit SHAs of
the newest release in each major (checkout v5.1.0, setup-uv v6.8.0,
gitleaks-action v2.3.9; the gitleaks SHA matches the one CI had resolved
for `@v2`), and `.github/dependabot.yml` bumps them weekly via PRs.

## 2026-11-16 — Real fees per venue; OOS scope per strategy, instrument, timeframe

Owner decisions.

**Fees.** The single 40 bps placeholder was about half Kraken's real entry
taker fee. Each venue now has its own schedule with its published
entry-tier fees, source and check date: `kraken_pro_tier1` (40/80 bps
maker/taker; Kraken's tiers since 2026-07-09) and `coinbase_advanced_tier1`
(40/60 bps; from third-party guides, to confirm on Coinbase's signed-in fee
page). The market profile's `commission` maps venue → schedule; the loader
refuses a market whose live venues lack one. Bar backtests charge the taker
fee. Cost per unit traded on Kraken is now 86 bps (was 46); the synthetic
exit-check crossover still passes. Earlier real-data verdicts were made at
46 bps and are stale.

**OOS scope.** The hidden period is opened once per strategy, instrument and
timeframe (was: once per strategy), and every first opening is recorded as
a trial (`experiment_id: oos_opening`), so trying a strategy on many
instruments raises the bar for all. The trial check leaves out the
candidate's *own* opening, which can't have influenced its selection; this
keeps a replay's report identical to the first run's. Vault events written
before scopes existed keep blocking the whole strategy (strict).

**Seals per verifier config.** The candidate key covers code, params,
dataset and window, not costs, so re-verifying an unchanged strategy after
a fee change would have reported "does not reproduce the sealed result".
Results are now sealed per `config_key` (costs + verifier config hash): an
unchanged strategy replayed under a new config gets a new seal; a changed
strategy in the same scope is still refused.

**Thresholds and listing dates.** Verifier thresholds stay as v1 defaults
until there are more real verifications. Listing dates stay as recorded
(Coinbase 2015-01-26 confirmed; Kraken 2013-09-10 approximate).

## 2026-11-16 — A provider layer for data sources; Binance and Yahoo added

Owner request: one abstract layer to call any data source and add new ones
easily, plus Binance and Yahoo Finance. `firm.data.providers` defines one
contract (`base.Provider`): declared capabilities (kinds, timeframes, page
size, politeness interval, licence, raw price adjustment), a request plan
(paged URLs for bars, a cursor for trades) and pure parsers that emit closed
bars only, unadjusted. A registry maps names to instances; captures, configs
and the CLI use those names. Providers do no I/O: `firm.data.fetch` runs the
plans (retries on transient errors only, provider pacing, captures with the
provider's licence and adjustment). `scripts/download_data.py` replaces the
crypto-only `download_crypto.py`.

- **No new dependencies**: Binance and Yahoo are called over HTTP like
  Kraken and Coinbase, because the store keeps each source's own bytes. A
  library-backed provider (yfinance, ccxt) fits the contract but needs the
  owner's approval as a dependency.
- **Binance**: `data-api.binance.vision` (public market-data host, no key).
  Lab use only: the UK profile records it as `not_permitted` for real money
  (FCA permissions cancelled May 2023; may apply in the 2026-09-30 to
  2027-02-28 authorisation window). Owner principle: the firm is a
  laboratory; any source may feed research, training and paper trading;
  real money only on venues the jurisdiction allows, behind the live gate.
- **Yahoo**: unofficial chart endpoint, personal use, can break without
  notice. Its OHLC is split-adjusted (lookahead); the parser undoes it with
  the split events in the same response, so stored prices are what traded.
  Daily bars get `close_time = open + 1 day`, later than the real close, so
  a bar is never known too early. Raw captures are labelled `split_adjusted`.
  Yahoo returns split events only inside the requested window, so the plan
  always requests up to a far-future `period2`; a live check found a
  January-2024-only NVDA request coming back ten times too low otherwise.
  Verified live after the fix: closes match the traded prices on both sides
  of the 2024 split. Binance verified live too (one month of daily bars).
- **Capture-name fix found on the way**: names hashed only the payload, so
  two requests in the same second returning identical bytes (e.g. empty
  pages) collided and the download crashed. Names now hash payload +
  request; the meta's `sha256` stays the payload checksum.
- Binance's trade cursor stops on a short page (fewer than 1,000), saving a
  request. Market profiles' `data.history` now name registered providers; a
  test keeps them in sync (planned ones, e.g. `eodhd`, listed explicitly).

## 2026-11-16 — Timeframes: provider alignment checked, derived timeframes on one grid

Owner question: how are 1m to 1d series managed? Every layer was already
keyed by timeframe; this adds what was missing.

- **Alignment.** Sources disagree on bar boundaries: Kraken's weekly bars
  are epoch-aligned (Thursday), Binance's and Yahoo's start Monday, Yahoo's
  days follow the exchange session. Each provider declares `day_boundary`
  and `week_start`; the normaliser refuses any bar off its declared grid and
  records the alignment (`utc`, `week:<day>`, `session`) per file in the
  manifest. A surprise fails loudly instead of mixing two week definitions.
- **Derived timeframes.** `firm.data.resample` builds coarser bars from finer
  ones on the canonical grid (intraday and days on epoch multiples, so days
  are 00:00-24:00 UTC; weeks start Monday). Only complete buckets are
  emitted, from closed bars, so a derived bar is known at its own close.
  Derived series are stored apart (`derived_from=<tf>` in path and
  manifest) and read only when asked (`bars(..., derived_from="1m")`).
  CLI: `firm data build-dataset ... --derive FROM:TO`. Session-aligned
  sources (shares) are refused until the exchange calendar (milestone 6).
- `NORMALIZER_VERSION` 2: manifests now carry `alignment` and
  `derived_from`, so datasets built from now on get new versions; existing
  manifests still open (the new fields default to empty).
- Checked on the real Coinbase and Kraken daily data: every bar on the UTC
  grid; weekly bars derived on Mondays for both venues.

## 2026-11-16 — Candidates and trials keyed by the content of the bars

Owner decision. A candidate's identity in the OOS vault and its trial ID
were built from the dataset version and window. Re-normalising the same
bars (normaliser v2) creates a new dataset version, so re-verifying an
unchanged strategy was refused as "another version" and counted as a new
trial. Both now use a fingerprint of exactly the bars the verifier read
(`bars_fingerprint`: open/close times and OHLCV, fixed byte order) with the
code and parameters. Same bars in any dataset version: a replay, not a new
trial. Any added, removed or changed bar: a new candidate, as before. The
report records `bars_sha256`.

Migration, append-only: a vault opening recorded under the old key is
linked to the content key by an `alias` event the first time a
verification reproduces the old key (same dataset version and window); a
trial recorded under the old ID is linked by `trial_aliases.jsonl`, and the
registry counts each trial once under its canonical ID. Done on the owner's
real state: same verdicts, trial count unchanged, replays on the rebuilt
dataset.

## 2026-11-16 — The ledger (milestone 2, phase A)

SQLite in WAL mode (`state/ledger.sqlite`, git-ignored). Append-only is
enforced three ways: the API has no update or delete; database triggers
refuse UPDATE and DELETE; and every event's hash covers its content and the
previous hash, so an edit made around the triggers breaks the chain and
`firm ledger verify` names the first bad sequence. Appends are idempotent
on `event_id` (same content: the stored event; different content:
refused). One writer: `LedgerWriter` drains an asyncio queue, so many
producers keep one order.

**The verifier keeps its own state and mirrors it** (a change from the
TODO's "move into the ledger"): golden rule 12 says desks never share a
database, and the OOS vault needs an atomic check-then-append that belongs
to the verifier. Every trial, trial alias, vault event and verdict is also
emitted through an `EventSink` (core protocol, so `verify` doesn't import
`ledger`); the CLI wires the real ledger. Event IDs are deterministic
(`trial:<id>`, `oos:<hash>`, `verdict:<id>`), so a replay re-emits nothing
new and `firm ledger import-state` (one-off history import) is idempotent.
Verdict events carry a deterministic summary and the report's sha256, not
the whole report. `LedgerEventType.OOS_OPENED` became `OOS_VAULT` (all vault
events), and `TRIAL_ALIAS` was added. Firm rule 6's invariant test is real.
Imported the owner's real state: 19 events, chain intact, re-import adds 0.

## 2026-11-17 — The risk desk (milestone 2, phase B)

Paper capital and every limit live in `config/risk/default.yaml` (owner
decision: configuration the owner edits, never code or agents), with the
proposed values: £10,000; position 20%, market 60%, exchange 50% (crypto),
total 100%, single order 10%, daily loss 3%, drawdown 10% per market and
15% total. The schema checks the limits nest (position ≤ market ≤ total)
and that total exposure stays within the jurisdiction's leverage cap.

`RiskDesk.decide(verdict, proposal, state)` is deterministic and records
every decision and halt in the ledger (decision ID from the proposal ID, so
one proposal gets one decision). Order of checks: kill switch on → verdict
mismatch or not passed → drawdown (trips the market or global kill switch)
→ sells capped at holdings (spot, long-only; allowed after the daily loss
limit) → daily loss limit blocks buys → buys shrink to the tightest of
single order, position, exchange, market and total room in GBP. An order
that can't be valued (no FX rate) is rejected, never guessed. Shrunk
quantities round down to 1e-8; venue lot rules apply at execution.

Kill switches are released only by an owner `RESUME` command
(`OwnerCommandKind.RESUME` added). `OrderProposal` gained `reference_price`
and `quote_currency` so risk can value an order. `firm.core.heartbeat`
provides the monitor execution will use to halt when risk goes quiet.

## 2026-11-17 — The paper simulator's fill model (milestone 2, phase C)

The first `VenueAdapter`; paper mode only, configured by `SimConfig` (the
venue's fee schedule, the market profile's spread and slippage, a
participation share, instrument rules, starting balances). Rules:

- Fills only on market data that arrives **after** the order (later
  trades; bars opening at or after it): no same-instant fills.
- Market orders and triggered stops take the next trade price or bar open,
  moved against us by half the spread plus slippage, rounded to the tick
  against us; **on bars** the price is clipped to the bar's range. Taker fee.
- Resting limits fill at their limit when the market trades through it
  (maker fee); stop-limits become limits once triggered. A gap through a
  stop fills at the gap price, never the better stop price.
- Each event's volume × participation (10%) is shared across our orders in
  submission order: large orders fill partially over several events.
- IOC / FOK / DAY (expires at 00:00 UTC) as their names say.
- Rejected before anything else: wrong venue, unknown or untradable
  instrument, below minimum quantity or value, off lot or tick,
  insufficient funds. Cash for open buys and coins for open sells are
  reserved. If prices move so a fill would overdraw, the fill shrinks or
  the remainder is cancelled.
- Every fill carries its order's verdict and risk decision IDs (the M2 exit
  check's traceability). Same events, same fills.

A bug caught by the tests: the price clip was applied to single trades
too, whose "range" is one price, so it silently removed spread and slippage
from every trade-based fill. Clipping now applies to bars only.

Known optimism, to calibrate with the trade journal: queue position for
resting limits isn't modelled, and the participation share and slippage are
assumptions. The conformance suite is now all real tests (its 7 milestone-2
placeholders are gone); a token-bucket `RateLimiter` makes adapters back off.

## 2026-11-17 — Live feeds (milestone 2, phase D)

- **Direct WebSockets, one module per venue**, like the REST providers: the
  store keeps each venue's own bytes, and a parser per format is small.
  `websockets` is the only new dependency (approved).
- **Live data is stored as raw captures** (JSON lines of `{received_at,
  message}`), flushed every minute per symbol, so the bars the firm trades
  on can be rebuilt and backtested later through the same normaliser and
  dataset versions as downloads. A capture holds only trades known by its
  capture time.
- **Trades are the primitive; bars are built from them** on the canonical
  grid and emitted only after their period ends. Venue OHLC channels are
  not used: one source of truth, and the same closed-bar rule as the store.
- **Only bars watched from start to end count.** The first live run showed
  why: joining mid-minute gave a first bar with a fraction of the minute's
  volume (Kraken's subscription snapshot adds older trades too). Bars begun
  before the stream joined, or interrupted by a disconnect, are dropped and
  counted, never passed off as complete; filling them from REST history is
  a TODO.
- **Taker side normalised per venue**: Coinbase's `side` is the maker's,
  Binance's `m` says whether the buyer was the maker.
- **Staleness is configuration** (`data.stale_after_seconds` in each market
  profile: 60 s crypto, 120 s US shares, placeholders) and is reported as
  `FEED_STATUS` events on change; the market worker (phase E) blocks new
  paper orders on a stale feed.
- Reconnects back off 1, 2, 4 … 60 s; replayed trades are de-duplicated by
  trade ID; a refused subscription stops the feed instead of retrying.

## 2026-11-17 — The market worker (milestone 2, phase E)

- **One order path, checked in code and in tests.** The worker's only inputs
  are market data, feed status and risk heartbeats; orders exist only as
  `build_intent(proposal, risk_decision)`. Firm rule 1's invariant test scans
  `src/firm` and fails if any `submit()` takes anything else; rule 2's checks
  that every order the venue receives names the verdict and a risk decision
  that approved exactly that proposal.
- **Sizing is execution's, limits are risk's.** The worker sizes the
  strategy's target exposure against its market's account (with a cost
  buffer so buys stay affordable); risk shrinks it to the limits. Lots:
  `build_intent(lot_size=)` rounds the approved quantity DOWN to the venue's
  lot, never up.
- **Decide at the close, fill after it.** A trade that closes a bar is
  handed to the venue only after the worker decided on that bar, matching
  the backtest (decide at close, fill at the next price). A test checks the
  worker's targets equal the strategy's signals over the full series.
- **Deterministic IDs** from (verdict, venue, instrument, bar close): a
  replayed bar can't create a second order. Simulator fill IDs now derive
  from the client order ID, so they stay unique across restarts (the ledger
  is idempotent on event IDs, and `sim-1-1` would have collided).
- **FX is a live market price** (`execution.fx` in each market profile:
  Kraken's GBP/USD by default), never a typed-in number; a rate older than
  `max_age_seconds` makes the portfolio unvaluable, and the worker orders
  nothing (risk would reject what it can't value anyway).
- **Capital per market** is `paper_allocation_pct` in the risk profile
  (50/50, placeholders; the sum may not exceed 100%). Unallocated capital is
  GBP cash in the equity.
- **`RiskState` and `Holding` moved to `firm.core.risk_state`**: they are the
  message execution sends risk, and desks may not import each other.
- **Deployment gate**: a report reaches paper only if it hashes to its
  verdict ID, passed, matches the strategy file's sha256 and the parameters,
  the venue/instrument/timeframe, and every dataset row traces to an `api`
  capture (`capture_methods`). Synthetic data can never promote a strategy.
- **A crash halts one market**: the runner records `MARKET_STATUS` and
  re-raises, with no final tick (nothing may be ordered after a failure).
- Live composition (`firm paper run`: warm-up history, REST bars for daily
  timeframes, real instrument rules, restart-safe paper account) is left for
  phase H prep; listed in TODO.md.

## 2026-11-17 — `firm paper run`: venue bars, live trades, a ledger-backed account

- **Decisions use the venue's own closed bars (REST), not bars built from
  live trades.** They are the bars the verifier judged the strategy on, they
  need no partial-bar handling, and a daily strategy can start trading on its
  first close instead of a day later. Each fetch is a raw capture in the
  store. The bar is fetched 10 s after its close and retried every 30 s
  until the venue publishes it. After downtime every missed bar is fetched,
  but only the latest is decided on; the rest only fill the history (no
  trades on stale signals). A test caught the first version fetching only
  the last 3 bars, which would have dropped bars after longer downtime.
- **Live trades drive the paper fills and staleness only.** FX to GBP comes
  from a second live stream (the market profile's `execution.fx`).
- **The paper account is opened once and rebuilt from the ledger.** The
  first run records its opening balances (its share of paper capital at the
  live FX rate) as a `MARKET_STATUS` event; every later run rebuilds the
  balances from that plus every fill since, so a restart never resets P&L.
  Rebuilt balances that would go negative are refused.
- **Instrument rules come from the venue's published API** (Kraken
  AssetPairs, Coinbase products) at start, never from config; a pair the
  venue says isn't trading stops the run.
- The run uses the strategy's verified parameters and refuses anything else.
  Its own lookback (`--lookback`, default 200 bars) only has to cover the
  strategy's longest window.

## 2026-11-17 — Implausible bars: quarantine and refuse (owner decision)

Found on real data: a venue's own candle can be corrupt (one hourly bar
opened at ~0). The verifier took it at face value; that one bar dominated the
return statistics, so costs and parameters seemed not to matter. That
verdict was meaningless and its trial is spent (details in the private
notes).

- **Raw stays raw.** Captures are never edited. The normaliser quarantines
  bars under a versioned rule (`config/data/quality.yaml`: any price more
  than 3x away from the last accepted close) and lists each one, with its
  reason and the rule ID, in the dataset manifest. The rule looks only
  backwards, so whether a bar is quarantined never depends on later data,
  and a quarantined bar is never used as the next reference.
- **The verifier refuses what's left.** `data_quality` runs first; an
  implausible bar in the window means UNVERIFIED with nothing judged, and
  the runner records no trial, so bad data can't raise the bar for later
  strategies.
- The threshold (3x) is a placeholder, far above any genuine hourly or
  daily move and far below the corrupt candles; on the real series checked
  so far it catches exactly the corrupt bars and nothing else.
- Normaliser version 3 (manifests now record `quality_rule` and
  `quarantined`); old dataset versions stay readable and their verdicts
  stand.
- Alternatives not taken now: refusing without quarantine (a series would
  be unusable until hand-fixed), and cross-venue checks (more data per
  window; listed in TODO.md as a second opinion).

## 2026-11-17 — Binance as a paper venue; FX chained through USD

- **Binance fees** `binance_spot_regular`: 10 bps maker and taker (regular
  user, fees in the quote asset), assumed by the owner from third-party
  guides because Binance's fee page renders with JavaScript; the owner will
  confirm. Binance stays a lab venue: paper only (`not_permitted` for real
  money in the UK profile).
- **Instrument rules** from Binance's public `exchangeInfo` (PRICE_FILTER,
  LOT_SIZE, NOTIONAL), formatted exactly (no float, no scientific notation).
- **FX is a list of live pairs chained to GBP.** Binance prices BTC in USDT,
  so `execution.fx` lists GBP/USD and USDT/USD (both Kraken's live streams);
  `FxBook` converts directly or through one currency, and every link must be
  fresh or there is no rate. USDT is never assumed to equal USD.

## 2026-11-17 — The unattended paper run: systemd user unit, its own ledger

Phase H runs `firm paper run` on the always-on Linux box under a systemd
**user** unit (`deploy/systemd/firm-paper@.service`, installed by
`scripts/install_paper_service.sh`; runbook `deploy/README.md`).

- **User unit, not system unit:** no root needed to run or stop it, and
  the stores live under the owner's home; `loginctl enable-linger` keeps it
  running when logged out.
- **Settings outside the repo:** the verdict ID and local paths go in
  `~/.config/firm/paper-<name>.conf` (mode 600); the repo has only a
  placeholder example. The unit template carries `@REPO@` and `@UV@`, filled
  in at install, so no machine path is committed.
- **Retry errors, never refusals:** `Restart=on-failure` every 60 s with no
  start limit (a network outage at boot must not end the run), and
  `RestartPreventExitStatus=4`: a deployment-gate refusal (`EXIT_REFUSED`,
  new; a missing report now counts as one) can't change by retrying.
- **No `--hours` in the unit:** each restart would reset the timer; the run
  is stopped by hand.
- **The box keeps its own ledger.** Its state root starts empty except for
  the verifier report; the research ledger (trials, out-of-sample openings)
  stays on the machine that runs research. Paper runs record no trials, so
  the global trial count is unaffected. Two ledgers until the bus (phase F)
  gives the firm one writer; merging them is an open item in TODO.md.

## 2026-11-17 — Roadmap re-aligned with the owner's vision

Owner review of the docs and TODO against the original design. The
foundation matched; the order of work had drifted toward plumbing while the
research side (the reason for the firm) was barely planned. Decisions:

- **Milestone 2 finishes in this order:** trade journal (G), a minimal
  notifier (N, new: urgent alerts to the owner on Telegram, plus a heartbeat
  watchdog), the bus (F), then the 7-day run (H). An unattended run with no
  alerts would fail silently; the bus comes before the run because the
  Linux box and the DGX already run separate parts of the firm.
- **The trial-scaling fix is approved** and lands before milestone 3:
  agents will run many trials across timeframes, and mixed-scale Sharpes
  would distort every verdict.
- **Milestone 3 gets prerequisites:** agent-written strategies live in the
  private store (the repo is public), the egress gate is enforced in code,
  the strategy loader is sandboxed. Its design (experiment agents) is done
  with the owner before any code.
- **The opportunity scout becomes milestone 7**, after milestone 3, numbered
  last so existing milestone references stay valid.
- **Parked until after milestone 3:** TA documents on demand, "Ask the
  firm", and the web-monitoring extras.
- **SPEC hardware roles updated:** the Linux box runs the trading stack
  24/7; the DGX serves models and research.

## 2026-11-17 — Research floor design: autonomous researchers, automatic paper promotion

Owner design session for milestone 3 (SPEC "Research floor").

- **Each agent is a researcher owning one strategy dossier**, autonomous
  from idea to verdict: invents (from published methods: papers, books,
  public talks by firms such as Jane Street; rules, ML, LLM, the ari
  indicators), backtests and optimises on the research view with
  walk-forward, then submits to the verifier, which opens the out-of-sample
  period once. Every run counts in the global trial count.
- **Promotion to paper is automatic** (owner choice): a deterministic gate
  promotes a verified strategy when it is not too correlated with strategies
  already in paper and the paper-strategy cap is not reached. The owner is
  notified and can veto. Paper is fake money; live stays a manual decision
  (firm rule 7 unchanged). This replaces "verified finalists wait for your
  approval".
- **Everything is a parameter** in `config/research.yaml`, owner-editable,
  never agents: concurrent researchers (start with 2), per-dossier budgets,
  promotion-gate limits.
- **Dashboard v1 comes with milestone 3** (owner request): one web page to
  inspect and run the researchers and to watch paper trading in real time,
  on a minimal control API. The rest of the web UI stays in milestone 4.

## 2026-11-17 — Dashboard stack

Owner had no preference; requirements: charts and configurable panels, runs
on any host on a private network.

- **FastAPI + uvicorn** for the control API: Python like the rest of the
  firm, REST plus WebSocket, typed with pydantic (already a dependency).
- **Plain JavaScript modules, no build step:** nothing to compile on the
  Linux box or the DGX; libraries vendored at pinned versions, no CDN.
- **TradingView Lightweight Charts** (Apache-2.0) for price charts. Its
  licence requires naming TradingView as the creator: its attribution logo
  stays on and its NOTICE is kept with the vendored file.
- **Apache ECharts** (Apache-2.0) for everything that is not a price chart.
- **GridStack.js** (MIT) for drag-and-resize panels; layouts saved
  server-side per named view, not in the browser.

## 2026-11-17 — Replay through the paper path; buys sized as the simulator reserves

`firm paper replay` runs history through the live trading path (worker,
risk desk, simulator) and compares it with the verifier's backtest, because
paper results can only confirm a verdict if both engines make the same
decisions. It answers in seconds what a live run answers in weeks (owner:
tests are sized by decisions per hour, not days). It is a check, not a
verification: it needs a passing verdict (the same deployment gate as
`paper run`), records no trial and writes to a scratch ledger.

Its first test found a sizing bug: the worker sized a full buy with fees and
costs added (`taker + half spread + slippage`), while the simulator reserves
them compounded (`(1 + costs) x (1 + taker)`), so a 100% target was always a
few cents short and rejected. `PaperSimulator.taker_buffer_bps()` now gives
the exact figure and both `paper run` and replay size with it. The live run
was unaffected only because the risk caps keep each buy far below the cash.

## 2026-11-18 — Kill switch lets exposure come down; the verifier sizes like the risk desk

Two owner decisions after the first replay of the daily pass.

- **A tripped kill switch blocks buys, not sells** (owner chose this of
  three options: block everything, allow only exposure-reducing orders,
  flatten on trip). Before, a market kill switch rejected every order and
  froze an open position for as long as the owner took to RESUME: in the
  replay, from a crash in 2024 to the end of the data. A sell is capped at
  what is held (spot, long-only), so it can only reduce exposure; it still
  needs its passing verdict. Not "flatten": selling is the strategy's
  decision, the switch only stops new risk.
- **The verifier backtests at the risk desk's position size** (owner: "we
  never risk 100%, so 20% is ok"): a target of 1 holds
  `sizing.position_pct` of equity (20, equal to the risk profile's
  per-instrument cap; a test keeps them equal). It lives in the verifier
  config, not read from the risk profile, so the verifier never reads risk
  config and changing it is an owner edit of a verifier threshold like any
  other. The report records the size and paper trades at it; reports from
  before carry no size and mean fully invested. Every check is Sharpe-based
  and a Sharpe ratio doesn't change when positions scale, so no pass/fail
  changes: only returns, drawdowns and exposure become what paper can earn.
  The config change gives a new `config_key`, so an unchanged candidate
  replays its sealed out-of-sample result under a new seal, as for fees.
  Rejected: replay as a second gate (slower, and it would still compare
  against a fully invested backtest).

## 2026-11-18 — Trade journal: a trade is a round trip, unknowns stay empty

Phase G. A journal row is one **round trip** on one venue account and
instrument (flat -> long -> flat), not one order or one fill: entries come in
steps (the 10% order cap under a 20% position cap) and orders fill in parts,
and what we want to learn from is the whole position's result. Spot and
long-only in v1, so position tracking per (venue, instrument) is exact; a
short side would need its own rule. The journal reads only the ledger (no
second source of truth), and refuses an inconsistent book (a fill without
its intent, a sell above the holding) rather than guess. Fields the ledger
can't give yet (spread paid, excursions, features, judge outputs) are NULL,
never 0, so no analysis mistakes "unknown" for "free" or "flat". Rebuilt
whole into a new file and swapped in, stamped with the ledger position.

## 2026-11-18 — Milestone 2's exit check counts bars, not days

Owner: "instead of 7 days we can keep the check to some candles, example 7
hours with 1h candles". The exit check exercises the plumbing (decisions,
risk, fills, ledger, alerts), and that is measured in bars decided, not in
calendar time: at least 7 closed bars of the strategy's timeframe, deciding
on every one. Seven days of a daily strategy still qualifies; seven hours of
an hourly one is the intended run. Whether a strategy is *proven* on paper
(candidate for real money) is a separate bar, still to set in trades.
Same day, owner: order and position caps stay at 10% and 20% as parameters
(already config); later, agents may search them in simulation only.

## 2026-11-18 — Cleaned data: proved equal, never interpolated; the exit run's strategy

Owner: "if one bar is corrupted, we can have a clean dataset or an
interpolated one", then "apply the best solution".

- **No interpolation for verification.** An interpolated bar is a price
  nobody could trade at; a verdict resting on it would be judging made-up
  data. The quarantine already leaves a corrupt bar out as a gap, which the
  backtest handles like any missing bar.
- **The vault recognises the same candidate on cleaned data.** The daily
  pass opened its hidden period on a dataset holding a corrupt 2017 bar
  (before the quality check existed). On the clean dataset its bars differ,
  so its content key differs and the vault refused it: the strategy could
  never get a verdict on trustworthy data. Firm rule 4 protects the hidden
  period from a second look by a *different* candidate. Here the code, the
  parameters and the hidden-period bars are identical; only bars nobody
  should trust are gone. So the verifier rebuilds the opened bars from the
  dataset the opening cited, drops the bars today's rule quarantines, and
  links the two only if the rest is byte-for-byte what it reads now. The link
  is an append-only `alias` with its reason, and the trial is linked too (the
  same experiment, not a new one). Anything else (another changed bar, a
  longer window, a missing old dataset) is still refused. Rejected: letting
  any dataset "derived from" the opened one replay (a revision could change
  the hidden period's bars), and opening a new scope (a second look).
- **Alias chains resolve to their end.** Found on the real state: the daily
  pass's trial and opening were already linked once (old-style key ->
  content key); aliases were resolved one level only, and a second link from
  the same key collided in the ledger. Both the registry and the vault now
  follow chains, and a new link starts at a chain's end.
- **Exit run (H) on the daily pass.** No strategy passes on 1h; writing one
  by hand only to shorten the run would spend trials the research agents
  will be judged against. The daily pass runs 7 bars unattended while
  milestone 3 is built; a 1h pass, if one exists first, takes its place.

## 2026-11-18 — Minimal notifier: alerts from ledger events, delivered off the trading path

Phase N1. Alerts are derived from the events the desks already write (feed
status, risk halts, market status) by fixed rules, so no desk learns about
Telegram and no model is anywhere in the path. The wrapper sink records
first and alerts second: the ledger never depends on the channel. Sending
happens in a background task with a few retries; a dead channel is recorded
and never raised, because an alert outage must not stop trading (the
watchdog, N2, covers a silent box). Throttling per alert key keeps a
flapping feed from flooding the owner; a "recovered" message is sent only
for an alert the owner actually received. Every send is a NOTIFICATION
event with its delay from the event, which is how milestone 2's "within 60
seconds" is measured. The bot reads nothing in v1 (no commands at all), the
chat is one environment value, and `send` has no recipient argument (firm
rule 10). `.env` is read by a 20-line loader (no new dependency) that fills
only unset keys, so a systemd EnvironmentFile or the shell always wins.

## 2026-11-18 — Bus (F1): one client per desk, two transports, idempotent at both ends

Phase F1. Desks talk through `BusClient`, which carries the service's
identity: it can only publish subjects that service owns (the same table the
envelope checks, and that the server will enforce with per-service users in
F2), and it only ever hands a desk a validated envelope. Malformed or forged
messages are rejected and acknowledged rather than retried, since they can
never become valid. Idempotency at both ends: publishers set `Nats-Msg-Id`
so a retried publish lands once, and consumers skip message IDs they
handled, with durable consumers so a restart resumes rather than replays.
A failing handler is not acknowledged and the message comes again, in
order. An in-memory transport with the same semantics keeps the test suite
off the network; `scripts/bus_smoke.py` checks the real server. One stream
holds every firm subject (a short, explicit list from `Subject`), not `>`,
so nothing outside the table is stored. Everything runs on the Mac for now
(owner, 2026-11-18); moving a service is a `NATS_URL` change.

## 2026-11-18 — Bus (F2): the server enforces the org chart

Phase F2. The NATS server config is generated from `PUBLISHERS`, so the
permission table has one source and the server refuses what the code
refuses: each service logs in as its own user and may publish only the
subjects it owns. Even a client that skips `BusClient`'s check can't put an
order on the bus from an agent's login (checked against a real server by
`scripts/bus_smoke.py`). Services get only the JetStream calls a client
needs, scoped to the firm's stream; creating or changing the stream is an
admin login used by `firm bus up`, and the stream refuses deletes and purges
(the ledger is the record; the bus only ages messages out). Each login
receives only under its own inbox prefix (`_INBOX.<user>.>`), so nothing
reads firm subjects around JetStream and no desk can take another's
deliveries. Durable consumer names can't be restricted per service (NATS
matches whole tokens), so a service could create a consumer under another's
name first and block it (detectable, not silent); F3 closes that by having
the admin create the desks' consumers at `firm bus up`. Passwords are random,
kept in the state folder (600) and never printed, rather than bcrypt hashes
(no new dependency; the conf file is private too). Plain user/password, not
NKeys or JWT accounts, while everything runs on one machine; each machine
will later get only its own services' logins.

## 2026-11-19 — Bus (F3): the paper run's desks talk through the bus

Phase F3. `firm paper run` now runs risk, execution and the notifier as bus
services, each on its own login, still in one process on the Mac (owner,
2026-11-18: everything runs on the laptop for now). Splitting them into
processes later changes the composition root, not the desks.

- **Risk decides on its own state.** The proposal carries no risk state;
  the risk service computes it when it decides, so execution can't shape
  its own approval. In one process both read the same portfolio; when risk
  moves to its own process it will need positions from the bus.
- **No answer means no order.** Execution waits `decision_timeout_seconds`
  and then skips the bar with the reason recorded; it never fabricates a
  decision, and an outage can only stop trading, never cause it. Risk
  ignores proposals older than three timeouts (e.g. replayed after a
  restart): deciding them on today's state could contradict the ledger.
- **Heartbeats over the bus.** Execution's monitor accepts beats without
  raising: replays are ignored, a restarted risk desk (sequence back to 0,
  newer time) is taken as the new reference. Losing the heartbeat is
  recorded once and alerted; its return too. Checked live: killing the bus
  server mid-run raised "heartbeat lost: no new orders" after 10 s, and
  restarting it brought "heartbeat back" without restarting the run.
- **Consumers are declared and created by the admin.** Closes F2's gap:
  services can no longer create consumers, only look up and acknowledge
  their own, so no desk can squat another's or take its deliveries.
- **One ledger writer, one ledger per machine (for now).** The ledger keeps
  being written by the process that holds the desks (the composition
  root's sink), not by a bus subscriber that re-records messages: the
  events already carry reasons and IDs the bus messages don't. With
  everything on one machine there is one ledger. TODO's question (merge
  the research and paper ledgers) is settled as "one per machine" until a
  desk moves to another machine; then a ledger service on the bus.
- **Desks may import `firm.bus`.** The import-boundary test now lets every
  desk use the bus client (which imports only `core`), as they use `core`
  and `data`; desks still never import each other.

## 2026-11-19 — Trial Sharpes on one scale (the trial-count fix)

Owner-approved 2026-11-17, landed before milestone 3. The deflated-Sharpe
benchmark uses the variance of the Sharpe ratios of all trials; they are
per bar, so a daily trial's sat next to an hourly one's, and an hourly
strategy that loses badly looked mild (its per-bar Sharpe is about 1/5 of
the daily figure for the same annual quality). Now every trial's Sharpe is
rescaled to the candidate's bar frequency (x sqrt of the ratio of bars per
year) before the variance is taken. New trials record their bars per year;
older ones take it from their dataset's single bar timeframe, and a trial
whose timeframe can't be known is left out of the variance (still counted
in the number of trials) and the report says how many: leaving it in at the
wrong scale is the bug being fixed.

Measured on the real state (numbers in the private notes): the trials
behind the daily pass mix 1h, 4h and 1d bars, and on one scale their spread
is about three times wider. The daily pass no longer survives the
correction: re-verified on a scratch copy it fails the trial-count check
(the TODO note "changes the numbers, not the outcome" was checked on an
earlier verdict). The verifier is stricter, not looser: the earlier pass
benefited from the bug. Owner, 2026-11-19: the running exit run (H)
finishes as a test of the machinery, labelled "verdict issued before the
trial fix; fails under it"; it never counts as a strategy proven on paper.


## 2026-11-19 — Private strategy store (M3 prerequisite R1)

Agent-written strategies are the firm's private results, and the repo is
public. They are saved in the data store, which is git-ignored and backed
up with the rest of it, under `research/strategies/<dossier>/`, rather than
in a separate git repo or an encrypted folder: one place to back up, and
the same write-once rule as the rest of the store. Files are named by
content (`<stem>-<sha12>.py`) and never edited, because the verifier keys
candidates and trials by the code's hash: an edit in place would make a
file on disk disagree with the verdict that cites it.

The repo guard spots strategy code by its `def signals(` method rather than
by folder, so a strategy pasted anywhere outside `examples/strategies/`,
`src/` and `tests/` is refused even under an innocent name. Markdown is not
checked: the plans quote the method's signature, and a strategy saved as
prose can't be run by mistake.

## 2026-11-19 — Sandbox for agent strategy code (M3 prerequisite R2, part a)

Agent strategy code runs in a separate Python process inside the OS's own
sandbox (`sandbox-exec` on macOS, `bubblewrap` on Linux) rather than in a
container or a Python-level jail. Python can't safely restrict Python (a
restricted `__import__` is escaped in one line through object
introspection), so the import allow-list is only hygiene. Containers would
add a daemon and an image to keep in sync with the venv on both machines;
the OS sandboxes ship with macOS and are one package on Linux, start in
under a second, and reuse the firm's own venv read-only.

It fails closed: with no working OS sandbox nothing runs, and there is no
"unsandboxed" switch for agent code. `sandbox-exec` is marked deprecated by
Apple but is what macOS's own services use; if it ever goes, the firm's
research moves to the DGX (Linux) or a Linux VM, and nothing else changes.

Memory: macOS refuses address-space limits for Python, so a watchdog polls
resident memory four times a second. A fast allocation can overshoot by a
few hundred MB before it is killed; acceptable on 128 GB (DGX) and for the
Mac, where research is temporary. Linux adds `RLIMIT_AS` as a hard cap.

Errors are two types so a broken sandbox never reads as a failed (or
passing) strategy: `StrategyError` for the strategy's own faults,
`SandboxError` for the sandbox's.

## 2026-11-19 — Sandbox wiring (R2b): who is sandboxed, and what a failure means

Sandboxing is decided by where the code lives, not by a flag someone must
remember: anything under the private store (`<data root>/research/`) runs
in the sandbox, and the loader refuses to import it in-process. Hand-written
examples still run in-process (fast, and their code is public and reviewed);
`--sandbox` sandboxes any file on request. The sandbox runs the exact bytes
whose hash the verdict cites, so a file changed between hashing and running
can't slip through.

A sandbox failure is not evidence about the strategy, so it can't produce a
FAIL (or a PASS): the checks already done stay in the report, the rest are
NOT_RUN, and the verdict is UNVERIFIED. A strategy that deliberately breaks
the sandbox gains nothing by it: UNVERIFIED can never be promoted.

Found while wiring it: the OOS check counted its opening trial only after
computing signals, so a crash in between (sandbox or not) left the vault
opened and the trial uncounted, and the retry, a replay, never counts one.
The trial is now recorded as soon as the vault grants the opening.

## 2026-11-19 — The data-egress gate in code (M3 prerequisite R3)

One gateway (`firm.models.ModelGateway`) is the only way to call a model,
and an invariant test scans the code so no other module can name a model
endpoint or import a model SDK. The rules were configuration only; now
they're checks that run before anything is sent.

- **Local or cloud** is configuration (`egress.local_providers`), not a
  guess from the provider's name: anything not listed is cloud.
- **Tag is required** on every call, with no default: forgetting it is a
  TypeError, not a silent "public".
- **Budget per calendar month (UTC)**, read back from the ledger so it holds
  across restarts. Default £0 means no cloud call at all. Cost is known only
  after a call, so the last call of a month may overshoot; it is refused
  from then on. A monthly window matches how providers bill and gives the
  owner one number to set.
- **Refusals are logged too**, as MODEL_CALL events with outcome `refused`:
  an attempt to send private work out is itself worth knowing about.
- **No prompts in the ledger**: only a SHA-256 of the messages. The ledger is
  private, but it is also what the journal, the dashboard and later "ask
  the firm" read; a hash proves which messages were sent without copying
  strategy code into every consumer.
- **The opt-in is a record, not a switch**: `OwnerOptIn(run_id, reason)`,
  written into every call it allows. How the owner grants one (a logged
  owner command) comes with the research manager.

## 2026-11-19 — Research backtests are run by the verification desk (M3 prerequisite R4)

The plan had researchers record their own trials and publish them on
`trial.recorded`. Built instead: the verification desk runs every research
backtest (`verify.research.ResearchBacktester`) and records its trial
before returning a result. Reasons:

- The desk that corrects for trials should be the one counting them: a
  research desk that records its own trials could forget one, and a
  forgotten trial makes the deflated Sharpe too kind.
- It already owns the hidden period, so it can cap research reads at the
  start of the out-of-sample period it will open later.
- Researchers can't backtest on their own anyway: their code runs in the
  sandbox, which can't read the data store.
- Import boundaries agree: research may not import the backtest engine.

Trial identity stays code + parameters + bars, so repeating a backtest
isn't a new trial, and the same search across two experiments counts once.
A research backtest and the later verification read different windows
(the research view ends where the hidden period starts), so the candidate's
own verification adds one more trial: stricter, never kinder.

`trial.recorded` on the bus comes with the research manager as a service
(M2): it will ask for backtests over the bus and announce the trials.

## 2026-11-19 — Research config and dossiers (M3, M1)

`config/research.yaml` holds the floor's settings, checked against the rest
of the config at load: a dossier's cloud budget can't exceed the monthly
cloud budget in `models.yaml` (both are £0 now), and the promotion gate's
strategies together can't exceed paper capital (3 × 10% = 30%). The owner
set the gate (3 / 0.7 / 10%); the per-dossier trials, hours and tokens are
placeholders to set before the first research run. No GPU memory budget
yet: nothing in research uses the GPU until the ML desk.

A dossier is an append-only event log, folded on read, like the OOS vault:
nothing in it is rewritten, so what a researcher claimed and when stays
on record. The hypothesis is locked by the first backtest, because a
hypothesis written after seeing results is not a hypothesis; a new idea is
a new dossier. A failed verdict is final for the dossier (a changed
strategy is a new candidate, so a new dossier); an unverified one (bad
data, a broken sandbox) says nothing about the idea and sends it back to
research.

The ledger gets every stage change but never the hypothesis, rationale or
notes, for the same reason it never gets model prompts: it is read by the
journal, the dashboard and later "ask the firm", and strategy ideas stay in
the private store.

Firm rule 3 now covers `research.yaml`. Its invariant test stays a
placeholder until every agent-facing interface exists; a partial test
proves agent code (sandboxed, with the repo hidden) can't read or write any
protected profile.

## 2026-11-19 — The research manager (M3, M2)

Built as a library plus owner commands, with no command that runs
researchers yet: there is no researcher to run until the agent (M4), and a
"floor" command that did nothing would mislead. The manager is exercised
by a fake researcher in the tests, end to end through the real research
backtester, verifier and sandbox.

- **Scheduling is derived, not stored**: who runs is the oldest
  `researchers` open, unpaused dossiers. Nothing to get out of step after a
  crash or a restart, and pausing one hands its place to the next at once.
- **Budgets are enforced by the manager, around the researcher**: a
  researcher acts only through `ResearchTools`, which checks the budget
  before every action, and the manager checks again after each step and
  closes a dossier that has spent one. The trial budget is the exception
  that proves the rule: it refuses the next backtest rather than closing
  the dossier, so a researcher that used its last trial can still submit.
  Each dossier keeps the budget it was opened with: lowering
  `research.yaml` affects new dossiers, never silently a running one.
- **Tokens and cloud spend come from the ledger** (MODEL_CALL events now
  carry an optional `dossier_id`), so the budget can't drift from what the
  model gateway actually recorded.
- **Redirect closes and reopens** once research has started: the hypothesis
  is locked by the first backtest, so a new direction is a new dossier,
  linked to the old one. A draft just takes the new brief.
- **Pause is not a stage**: a paused dossier keeps where it was, and only
  stops running. Every owner command is an OWNER_COMMAND ledger event.
