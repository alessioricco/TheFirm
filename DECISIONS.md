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
(`macos-14`), Python 3.12 (floor) and 3.14 (latest), `uv sync --locked`
so a stale lockfile fails, then guard, ruff, mypy, invariants, all tests.
Not run yet: it becomes verified with the first push.

## 2026-11-16 — Synthetic and real data never mix

Found while writing the README: `firm data synthetic` defaulted to the real
store and the same series as the downloaded Kraken data, so the normaliser
would have merged invented and real bars. Now the normaliser refuses a
series whose captures mix `synthetic` with any other method, and
`firm data synthetic` refuses a store that holds real captures of that
series. The README demo uses a separate store (`./data-demo`, git-ignored).
