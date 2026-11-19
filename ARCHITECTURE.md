# Architecture

How each module works and its gotchas. Read the section before touching a
module; update it when you change one. Status: milestone 1 (data plane and
verifier) built; later desks are interfaces only.

## Layout

```
src/firm/
  core/         typed domain objects, config profiles, bus messages (no deps on other desks)
  bus/          message bus client: in-memory and NATS JetStream transports (M2, F)
  data/         data plane: store, providers, normalizer, datasets, reference  (M1)
  verify/       verification desk: backtest, 5 checks, verifier, state       (M1)
  risk/         risk desk                               (M2)
  execution/    VenueAdapter interface + paper simulator (M2)
  ledger/       append-only hash-chained ledger          (M2)
  markets/      one asyncio worker per market            (M2)
  research/     Certo integration, experiment manager    (M3)
  control/      control API + CLI                        (M4; CLI stub now)
  notify/       notifier                                 (M4)
  judge/        d3 decision model calls                  (M5)
  scout/        opportunity scout
config/
  jurisdictions/uk.yaml                     (agents: no access)
  markets/crypto.yaml, markets/us_equities.yaml (agents: read)
  risk/default.yaml                         (agents: no access)
deploy/              systemd unit + runbook for the unattended paper run
examples/strategies/  ma_crossover.py (clean), lookahead_ma.py (planted lookahead)
scripts/
  download_data.py                 owner-run: any registered provider -> raw captures
  ari_golden/generate_baseline.py  one-off; ran with the old repo's venv
tests/
  invariants/   one test per SPEC firm rule
  conformance/  shared VenueAdapter suite, parametrised over adapters
  core/ config/ execution/ control/
  features/     feature-library tests (ari golden fixture integrity now)
  fixtures/ari_golden/  MANIFEST of the frozen ari_signals baseline (data is in the store)
  repo/         guard: no data files in the public repo tree
  factories.py  valid example objects (on pytest's pythonpath)
```

Each desk package has an `interface.py` with `Protocol`s; implementations
arrive in the milestone shown.

## Data store (`$FIRM_DATA_ROOT`, default `<repo>/data/`, git-ignored)

**The repo is public. No market data, derived prices, datasets or models are
ever committed** (`tests/repo/test_no_data_in_repo.py` fails if one appears
in the tree; `.gitignore` blocks the common formats). The store is backed up
separately from git. It does not depend on the host: any machine, any path.

Zones, each written once and never edited:

```
data/
  raw/         exactly what the provider sent: one file + .meta.json per capture
  normalized/  Parquet in the firm schema, rebuilt from raw by versioned code
  datasets/    ds-<sha12>.json: frozen file lists + checksums that backtests cite
  reference/   instrument master: symbology, listings, delistings, corporate actions
  golden/      frozen test baselines (e.g. golden/ari_signals-03a8b8d/)
  research/    the private strategy store (milestone 3, see "research")
```

Paths are Hive-style `key=value` (DuckDB / pyarrow read them directly):

```
raw/provider=<p>/kind=<k>/symbol=<provider symbol>/tf=<tf>/<YYYYMMDDTHHMMSSZ>_<sha8>.<ext>
raw/.../<YYYYMMDDTHHMMSSZ>_<sha8>.meta.json
normalized/kind=<k>/venue=<v>/instrument=<firm id>/tf=<tf>/year=<YYYY>/part-<sha8>.parquet
datasets/ds-<sha12>.json
```

- Keys lowercase; values keep their case. Times UTC, compact ISO
  (`20261116T100200Z`), no colons or spaces. `/` in a provider symbol
  becomes `-` in the path; the exact symbol is in `.meta.json`.
- `tf` vocabulary: `1m 5m 15m 1h 4h 1d 1w`. `kind`: `bars`, `trades`, `book`,
  `funding`, `corporate_actions`, ...
- **provider** = who delivered the bytes (binance, kraken, yahoo, eodhd);
  **venue** = where trading happened (binance, kraken, xnas). raw is keyed
  by provider, normalized by venue.
- **Firm instrument IDs**: crypto `BASE-QUOTE` (`BTC-USDT`); shares
  `TICKER.MIC` (`NVDA.XNAS`). Provider symbols appear only in raw; the
  instrument master maps them, with validity dates (point-in-time).
- `.meta.json` per capture: provider, via (library), provider_symbol, kind,
  tf, request/endpoint, captured_at (null for legacy imports), sha256,
  bytes, rows, price_adjustment, licence, tag (`private`).
- Rows carry bar open and close time (UTC); `as_of` filters on close time.
  Rows carry `captured_at`.
- **Unadjusted prices only** in normalized; corporate actions in
  `reference/`; adjustment happens at query time as of the request
  (adjusted history is lookahead). Yahoo's adjusted series are raw-only.
- Corrections are new captures → new dataset version. No overwrite, no
  delete except an explicit purge tool. Delisted instruments stay.
- Tests use synthetic data generated in code. Tests that need the store
  skip with an `UNVERIFIED: ... data store absent` reason.

## core

- `base.py`: `FirmModel` (frozen, `extra="forbid"`), constrained types `Id`,
  `Reason`, `Sha256`, `CurrencyCode`, `Probability`.
- `risk_state.py`: `RiskState`, `Holding`: what execution tells risk when it
  asks for a decision (in core so execution needn't import the risk desk).
- `domain.py`: the SPEC components' interfaces: `StrategyCandidate`
  (+`StrategySpec`, `TrialRecord`), `ModelArtifact` (+`TrainingRecord`),
  `JudgeCall` (+questions/answers, labels checked), `OpportunityNote`,
  `Verdict` (+`CheckResult`), `OrderProposal`, `RiskDecision`,
  `OrderIntent`, `OrderUpdate`, `Fill`, `Notification`, `LedgerEvent`.
  Also the order lifecycle (`OrderStatus`, `ORDER_TRANSITIONS`,
  `can_transition`) and `build_intent`.
- Order path: `Verdict` → `OrderProposal` (`order.proposed`) →
  `RiskDecision` (`risk.decision`, names the proposal) → `build_intent()` →
  `OrderIntent` (`order.intent`) → adapter → `OrderUpdate` / `Fill`.
- `config.py`: `JurisdictionProfile` (+`VenueStatus`), `MarketProfile`,
  `RiskProfile`, `FirmConfig`, `load_config(root, jurisdiction, markets,
  risk="default")`. Raises `ConfigError`. No clock access.
- `messages/`: `Subject`, `Service`, `PUBLISHERS` (exactly one owner per
  subject), `PAYLOADS`, `SCHEMA_VERSIONS`, `Envelope`, `wrap()`. Alerts are
  `alert.<level>.<service>`; the notifier subscribes to `alert.>` and
  `digest.>` and fans out to connectors (Telegram first).

Gotchas:
- pydantic wraps validator exceptions: a forbidden sender surfaces as
  `ValidationError` with "permission denied", not a custom exception.
- `OrderIntent` validates price fields against `order_type` (limit needs
  `limit_price`, stop needs `stop_price`, market neither).
- `Verdict.outcome` must agree with its checks or construction fails.
- `LedgerEvent.prev_hash` is `None` only at `sequence == 0`. The hashing
  function itself comes with the ledger in M2.
- `Notification` has no recipient field on purpose (firm rule 10).
- Never construct `OrderIntent` directly outside tests; use `build_intent`.
  (An invariant test enforcing this lands with the execution desk, M2.)
- YAML dates (`as_of: 2026-11-15`) are parsed by PyYAML into `date`.

## data (milestone 1)

- `store.py`: `data_root()`, path rules (`partition`, `path_value`,
  `stamp`), `write_once` (atomic, refuses different bytes), raw captures
  (`write_capture`, `iter_captures`, `CaptureMeta`).
- `providers/`: one contract (`base.Provider`) and a registry
  (`providers.get(name)`, `available()`). A provider declares its kinds,
  timeframes, page size, politeness interval, licence and raw price
  adjustment; turns a request into a plan of URLs (`bar_requests`, cursor-paged
  `trade_request`/`next_trade_cursor`); and parses bytes into canonical rows,
  closed bars only, unadjusted. Built in: `kraken`, `coinbase`, `binance`
  (data-api.binance.vision), `yahoo` (unofficial; undoes split adjustment
  with the split events in the same response). No I/O inside providers.
  To add one: a module that `register()`s an instance, imported in
  `providers/__init__.py`, plus tests with synthetic payloads in its format.
- `fetch.py`: the only network code. `http_get` (retries transient errors
  only), `Fetcher(root, transport, clock, sleep)` runs a provider's plan and
  stores each response as a capture; `ensure_instrument` creates/extends the
  instrument record. Transport, clock and sleep are injectable for tests.
- `reference.py`: `InstrumentRecord` (firm ID, provider symbols, listed /
  delisted, `status(as_of)`), versioned files, `resolve(provider, symbol)`.
- `normalize.py`: captures → Parquet (`BAR_SCHEMA`, `TRADE_SCHEMA`), merge
  overlapping captures, count revisions. `NORMALIZER_VERSION` is part of
  every dataset version: bump it when output bytes change.
- `resample.py`: `derive_bars(root, files, to_tf)` builds coarser bars on the
  canonical grid (complete buckets only, `derived_from` set, UTC-aligned
  sources only); `aggregate()` is the pure core.
- `dataset.py`: `build_dataset` → `ds-<sha12>.json`; `open_dataset` (verifies
  checksums) → `Dataset.bars/trades/universe` with `as_of` (`bars(...,
  derived_from=None)` reads native bars; name the source tf for derived
  ones); `Dataset.alignment(venue, instrument, tf)`;
  `Dataset.capped(hidden_from)` → `CappedDataset` raising
  `HiddenPeriodError` (the research view).
- `streams/` (milestone 2): live data. `base.StreamProvider` (URL, subscribe
  messages, `parse(message)` → `(symbol, TradeRow)` with the TAKER's side,
  `parse_capture` for stored captures; no I/O) and a registry. Built in:
  `kraken_ws` (v2 trade channel), `coinbase_ws` (`matches`; its side is the
  maker's), `binance_ws` (aggTrade on data-stream.binance.vision; `m` true =
  taker sold). `feed.Feed(provider, symbols, root=, sink=, on_trade=,
  on_gap=, connect=, clock=, sleep=)` connects, resubscribes with backoff
  (1, 2, 4 … 60 s), de-duplicates replayed trades by ID, writes one raw
  capture per symbol every `flush_interval` (JSON lines `{received_at,
  message}`, `via: websockets`), and emits `FEED_STATUS` events (connected /
  disconnected / stale / fresh, on change only). `check()` reports staleness
  (`stale_after_seconds` in the market profile). A refused subscription
  (`StreamError`) stops the feed; garbage is logged, not fatal.
  `bars.BarBuilder(tf, started_ns=)` makes closed bars from trades: emitted
  only once their period is over, only if watched start to end (bars begun
  before the stream joined or across a disconnect, via `mark_gap`, are
  dropped and counted), late trades dropped, no invented bars.
  `normalize_trades` reads stream captures like REST ones; the instrument
  record needs the stream's symbol (`provider_symbols: {kraken_ws: BTC/USD}`).
- `quality.py`: `implausible_bars(bars, rule)`: a bar is implausible when any
  price is more than `max_price_ratio` x away from the last ACCEPTED close
  (point-in-time; a bad bar is never a reference). `normalize_bars(...,
  quality=)` leaves them out and lists them in `NormalizedFile.quarantined`
  with `quality_rule`; `firm data build-dataset` applies
  `config/data/quality.yaml` and prints each one. Normaliser version 3.
- `synthetic.py`: deterministic synthetic bars and trades in provider
  formats; `seed_store` / `seed_trades` write an instrument + overlapping
  captures. Tests use only this.

Gotchas: alignment is per provider (`Provider.day_boundary`, `week_start`)
and checked by the normaliser; `firm.core.timeframes` holds the grid helpers.
Capture names hash payload + request (identical bytes from two
requests in the same second must not collide); the meta's `sha256` is the
payload's own checksum. DuckDB queries take bound parameters (`$files`, `$as_of`); timestamps
are int64 ns until the reader converts them; `as_of` must be timezone-aware.

## verify (milestone 1)

- Checks run in order: `data_quality` first (the runner applies the rule to
  the window; any implausible bar → every check NOT_RUN, UNVERIFIED, and the
  runner records NO trial), then the five checks below. Reports state the
  rule and how many bars the normaliser quarantined.

- `strategy.py`: the `Strategy` protocol (`name`, `default_params`,
  `signals(bars, params)` → target in [0, 1]) and `load_strategy` (executes
  the file: hand-written code only until a sandbox exists).
- `backtest.py`: `run_backtest` (decide at close, fill at next open,
  cost per unit traded; `position=` scales a target of 1 to that share of
  equity), `metrics`, `SignalError`. The verifier sizes at
  `config/verify/*.yaml` `sizing.position_pct` (20, the risk profile's
  per-instrument cap; a test keeps them equal) and records it in the report
  as `sizing`. Sharpe-based checks are unchanged by the size; returns,
  drawdowns and exposure are not.
- `stats.py`: `deflated_sharpe`, `expected_max_sharpe`, `probabilistic_sharpe`,
  `rescale_sharpe(sr, from_periods, to_periods)` (per-bar Sharpe on another
  bar frequency: x sqrt(from / to)).
- Trial Sharpes on one scale (2026-11-19): each manual trial records
  `periods_per_year` next to `sharpe_per_bar`; the trial-count check calls
  `TrialRegistry.scaled_sharpes(candidate_ppy, ctx.trial_periods)` so the
  variance is taken on the candidate's bar frequency. Older trials get their
  scale from their dataset (`trial_scale.periods_from_dataset`: one bar
  timeframe in the manifest, `session` alignment = exchange calendar, else
  24x7); unknown ones are left out of the variance, still counted in n, and
  reported (`trial_sharpes_unscaled`, plus `scale_periods_per_year`).
- `state.py`: `state_root()`, `TrialRegistry` (counts canonical IDs; legacy
  IDs linked via `trial_aliases.jsonl`), `OosVault` (JSON lines; `alias`
  events link pre-content keys to content keys, and, with a `reason`, an
  opened candidate to the same candidate on cleaned bars; aliases resolve
  along whole chains (`resolve`), and a new link always starts at a chain's
  end, so each key is linked at most once;
  openings keyed by `oos_scope(strategy, instruments, tf)`, seals per
  `config_key`; `opening_trial_id()` for openings counted as trials),
  `STATE_SCHEMA_VERSION` (stamped in `STATE_VERSION` on first write; other
  versions are refused), `state_summary()`.
- `context.py`: `VerifyContext` (frozen inputs, cached in-sample backtests).
- `checks/`: `lookahead`, `costs`, `sensitivity`, `trials`, `oos`; each
  exposes `NAME` and `run(ctx)`.
- `verifier.py`: `verify(...)` → `VerificationResult(verdict, report)`;
  `bars_fingerprint` and `candidate_key` (code + params + bars content);
  `legacy_candidate_key` only to recognise pre-2026-11-16 vault entries;
  `EXIT_CODES`; `cost_bps` (taker fee of the venue's schedule + half spread +
  slippage); `split_in_sample`. The report's `costs` block names the fee
  schedule, its source and date.
- `runner.py`: `verify_strategy_file(...)` wires files, config, dataset,
  trial recording and the verifier (used by the CLI and tests).
  `quarantine_equivalents(...)`: for each opening in the scope, rebuilds the
  bars it saw from the dataset it cited (up to its hidden period's end; they
  must hash to the opened key) and drops the bars today's quality rule finds
  implausible; if that equals the bars read now, the opening is the same
  candidate: the vault links it (`equivalent_keys`) and its trial is linked
  too, so the count doesn't grow. A longer window is a different candidate:
  pass `firm verify --as-of <end of the opening>`. A missing old dataset
  proves nothing (refused as before).

Gotchas: the in-sample frame is everything before the hidden period and is
all the lookahead/cost/sensitivity/trial checks ever see; only `checks/oos`
touches the tail, after the vault allows it. Changing anything that alters
report bytes (rounding, keys) changes every verdict ID.

## ledger (milestone 2)

- `store.py`: `Ledger(path)`: SQLite WAL, `append(type, reason, payload,
  event_id=)` (idempotent on event_id), `read(from_sequence)`, `head()`,
  `count()`, `verify_chain()` → `ChainReport`. Triggers refuse UPDATE and
  DELETE; each hash covers content + previous hash.
- `writer.py`: `LedgerWriter`: the single writer; producers `await submit()`.
- `sink.py`: `LedgerSink` (an `EventSink` writing to a ledger),
  `ledger_path(state)`, `state_history(state)` for the one-off import.
- `journal.py`: the trade journal. `trades_from_ledger(ledger)` walks the
  ledger once: proposals and intents are indexed, and each fill is traced
  fill -> intent -> proposal (a missing link raises `JournalError`, as does a
  sell larger than the holding or a fee not in the quote currency). A trade
  is one round trip per (venue, instrument): it opens on the buy from flat and
  closes when sells bring it back to flat; every fill in between belongs to it.
  Prices are quantity-weighted; slippage is in bps against the proposal's
  `reference_price`, positive when it cost us. `build_journal` writes a new
  DuckDB file (`trades` + `journal_meta`) and `os.replace`s it in: never
  edited. Each row has `ledger_sequence` / `ledger_hash`. `export_journal`
  filters on decision time ([from, to)), CSV or Parquet. Fields not yet
  known are NULL (see TODO "Journal follow-ups"). Gotcha: fetching a
  TIMESTAMPTZ into Python needs `pytz`, which we don't depend on; format
  timestamps in SQL (`strftime`) or export instead.

Desks emit through `firm.core.events.EventSink` (`NULL_SINK` by default); the
composition root (`firm.control`) wires the ledger. Use deterministic event
IDs for facts that can be emitted twice.

## risk (milestone 2)

- `desk.py`: `RiskDesk(profile, sink)`. `decide(verdict, proposal, state)`
  → `RiskDecision` (see the module doc for the order of checks);
  `halted(market)`, `command(owner_cmd)` (KILL / RESUME), `resume()`,
  `heartbeat(now)`. `RiskState` (equity, peak, day-start equity, holdings,
  market P&L and peaks, FX to GBP) and `Holding` are its inputs.
- Limits come only from `RiskProfile` (frozen); the desk has no setter.
- A kill switch (owner KILL or a drawdown trip) rejects every buy until the
  owner's RESUME, but a sell still goes through, capped at what is held: it
  can only reduce exposure. It still needs its passing verdict.

- `service.py` (F3): `RiskService(desk, client, verdicts=, state=,
  heartbeat_seconds=, max_age=)`, the desk on the bus. Reads
  `order.proposed` (consumer `risk-proposals`), answers each with
  `risk.decision`; computes its own `RiskState` at decision time (what
  execution saw is not trusted); an unknown verdict or no state is a
  recorded reject; a proposal older than `max_age` is not decided at all
  (execution gave up; deciding it on today's state could contradict the
  ledger). `run(stop)` publishes `risk.heartbeat`; message IDs carry a boot
  ID so a restarted desk's sequence 0 isn't a duplicate. A failing
  heartbeat publish is logged once per outage.

Gotchas: everything is valued in GBP via `state.fx_to_gbp`; a missing rate
rejects. Market drawdown is measured on that market's P&L below its peak,
as a % of current equity. `firm.core.heartbeat.HeartbeatMonitor` is the
execution-side check.

## execution

- `adapter.py`: `VenueAdapter` ABC. Constructor runs the live-mode gate and
  checks the mode is in `capabilities().modes`. Methods (async): `submit`
  (idempotent on `client_order_id`), `cancel`, `order_status`, `open_orders`,
  `positions`, `balances`, `reconcile`, `instrument_rules`. Sync:
  `capabilities()`. Supporting types: `VenueMode`, `VenueCapabilities`,
  `RateLimitPolicy`, `InstrumentRules`, `Position`, `Balance`,
  `ReconciliationSnapshot`, `LiveTradingRefused`.
- `bus_risk.py` (F3): `BusRisk(client, timeout=, on_beat=)`, the risk desk
  as execution reaches it over the bus. `decide` publishes the proposal and
  waits for the `risk.decision` naming it; no answer (or the bus down)
  raises `RiskUnavailable` and the worker skips the bar with that reason:
  execution never invents a decision. Heartbeats go to `on_beat`
  (`HeartbeatMonitor.accept`: never raises, ignores replays, takes a
  restarted desk's lower sequence when its time is newer). The worker's
  `RiskDecider.decide` may return a decision or an awaitable of one.
- `runner.py` heartbeat watch: `watch_heartbeat(now)` each tick records
  `MARKET_STATUS heartbeat_lost` (once, after the monitor's timeout, also
  if risk was never heard) and `heartbeat_restored`; the notifier alerts on
  both. `risk_heartbeat=None` when heartbeats arrive on the bus.
- `simulator.py`: `PaperSimulator(mode, live_trading=, config=SimConfig)`,
  paper mode only. `on_market(MarketTrade | MarketBar)` drives fills and
  returns the updates/fills it caused; `fills()`, `history(client_order_id)`.
  Fill model in the module doc and DECISIONS.md.
- `ratelimit.py`: `TokenBucket(policy)`: `await acquire()` backs off when the
  venue's rate limit is reached.
- `worker.py`: `MarketWorker(deployment, strategy, adapter, risk, risk_state,
  heartbeat, sink=, clock=)`. Inputs only: `on_bar(BarRow)` (decides),
  `on_market(MarketTrade | MarketBar)` (feeds the paper venue, records fills),
  `on_feed_status(stale)`, `on_heartbeat(beat)`; no method accepts an order.
  Per closed bar: halts (stale feed, lost heartbeat, no risk state) → target
  from `signals` over the last `lookback_bars` (NaN = flat) → size against
  the market account's equity with a cost buffer, lot-rounded, skip under
  the rebalance threshold, one working order at a time → `OrderProposal` →
  `risk.decide` → `build_intent(..., lot_size=)` → `adapter.submit`; each step
  a ledger event. IDs derive from (verdict, venue, instrument, bar close), so
  a replayed bar resubmits the same client order ID. `Deployment` requires a
  passing verdict for the same strategy; its `position_fraction` (from the
  report's `sizing`) is the share of equity a target of 1 asks for.
- `runner.py`: `MarketRunner` is one market's asyncio loop: `on_trade` (the
  feed callback) queues; `run(stop)` updates FX on the FX pair, closes bars
  BEFORE the closing trade reaches the venue (no fill on a trade the order
  saw), and ticks each second (risk heartbeat, staleness, bars closed by
  time). An exception records `MARKET_STATUS` and stops this market only,
  with no final tick.
- `portfolio.py`: `FxBook` (latest live price per pair; to GBP directly or
  through one other currency, e.g. USDT → USD → GBP; any link older than
  `max_age` means no rate), `MarketAccount`, `Portfolio.risk_state(now, accounts, fx)`
  → the `RiskState` risk reads (equity, peak, UTC day start, P&L per market vs
  its allocation; unallocated capital counts as GBP cash), or `None` when
  anything can't be valued. `starting_cash()` from the allocation.
- `deploy.py`: `verdict_for_deployment(report, code_sha256=, params=, venue=,
  instrument=, tf=, capture_methods=)`: the report must hash to its verdict
  ID, pass, match the code and params, the venue/instrument/tf, and rest only
  on `api` captures (`firm.data.dataset.capture_methods`).
  `position_fraction(report)`: the size the verdict was judged at (1 for
  reports from before 2026-11-18, which were judged fully invested).

To add a venue: implement `VenueAdapter`, register a factory in
`tests/conformance/conftest.py::ADAPTERS`, pass the suite (`/add-venue-adapter`).

## notify (milestone 2: minimal notifier)

- `telegram.py`: `TelegramNotifier(token, chat_id)`: `send(notification)`
  posts to the one owner chat (no recipient argument, firm rule 10);
  `chat_ids(token)` (getUpdates, the owner's one-off helper);
  `telegram_from_env(env)` -> notifier or None and why. Errors are rebuilt
  without the token (it sits in the API URL); the repr hides it.
- `alerts.py`: `alert_for(event_type, reason, payload, cfg)` (urgent: feed
  stale/disconnected, risk halt, market crash; action: run started/stopped,
  feed recovered); `AlertingSink` (an `EventSink`: records first, then
  queues; throttles the same urgent key to once per `throttle_seconds`; a
  recovery is sent only if its alert was); `Outbox` (async worker `run(stop)`,
  sends in a thread with retries, `drain()` at shutdown; every send is a
  `NOTIFICATION` ledger event with `delivered`, `attempts`,
  `latency_seconds`, `error`). A dead channel never raises into trading.
- `bus.py` (F3): `BusAlerts`, the `AlertQueue` the desks' `AlertingSink`
  feeds when the bus is on: publishes each alert on its desk's subject
  (`route`: a risk halt on `alert.urgent.risk` as risk; everything else as
  execution, urgent or action), in a background task; a publish that keeps
  failing is recorded as an undelivered `NOTIFICATION`. `NotifierService`
  reads `alert.>` (consumer `notifier-alerts`) into the `Outbox`; `settle`
  waits at shutdown for the last alerts. `AlertingSink.put(n, source)` passes
  the raising event type so alerts can be routed.
- Config: `config/notify.yaml` (`load_notify`). Secrets: `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_OWNER_CHAT_ID` from the environment; `firm.core.env` fills unset
  keys from `.env` at CLI start (`FIRM_DOTENV=0` turns it off; the tests do).
  Gotcha: `scripts/check_repo.py` flags `NAME_WITH_TOKEN = "<16+ chars>"`
  as a secret, even an environment variable's name.

## bus (milestone 2, phase F)

Schemas, subjects and who may publish what live in `core/messages/`
(`Envelope`, `PUBLISHERS`, `PAYLOADS`); `bus/` moves envelopes.

- `client.py`: `BusClient(transport, service)`, one per desk, bound to its
  identity. `publish(subject, payload, message_id=)` refuses a subject the
  service does not own and wraps the typed payload. `subscribe(pattern,
  consumer, handler)` decodes into a validated `Envelope`; a message that
  fails its schema, is forged (sender not the subject's owner) or arrives on
  another subject than it names is rejected (`rejected`, `on_reject`) and
  acknowledged, never handed over; a message ID already handled is skipped
  (bounded memory of IDs, per subscription); a handler that raises leaves it
  unacknowledged, so it comes again. Durable name: `<service>-<consumer>`.
- `memory.py`: `MemoryBus`, JetStream's semantics in one process (stream
  log, publish dedupe by ID, durable cursors, no ack on raise, `*`/`>`
  wildcards). Tests use it; no server, no socket.
- `nats.py`: `NatsBus.connect(cfg, user=, password=)` or
  `NatsBus.for_service(cfg, state, service)`: one file-backed stream
  (`FIRM`) holding exactly the firm's subjects, created or updated only by
  the admin (`ensure_stream`); publish with `Nats-Msg-Id` (server-side dedupe window, 2 minutes
  by default); durable push consumers with manual ack, `nak(delay)` on a
  failing handler. Imported only where a server is used.
- `auth.py` (F2): the server-side copy of the permission table. One NATS
  user per `Service`, allowed to publish exactly its own subjects plus the
  JetStream client calls on the firm's stream (stream info, create/look up
  consumers, ack); no service may create, update, delete or purge a stream.
  `firm-admin` manages the stream and publishes no firm subject. Every user
  receives only under its own inbox prefix, `_INBOX.<user>.>` (its JetStream
  deliveries and replies; `NatsBus.connect` sets the prefix).
  Passwords: random, generated once into `<state>/bus/credentials.json`
  (600), kept across runs, never printed; `login(state, service)`.
  `write_server_config` writes `<state>/bus/nats-server.conf` (JSON, 600).
- `server.py`: `start(cfg, state, log_file)` checks the config with
  `nats-server -t`, refuses a port already in use, starts the server, waits
  for it. CLI: `firm bus up` (foreground; SIGINT/SIGTERM stop the server
  too; creates or updates the stream as admin) and `firm bus status`.
  Config: `config/bus.yaml` (`load_bus`): host (loopback), port, stream,
  retention, duplicate window, redelivery delay, publish timeout. The
  stream is `deny_delete` and `deny_purge`.
- `consumers.py` (F3): `CONSUMERS`, every durable consumer declared once
  (name, service, filter, start `all`/`new`): `risk-proposals`,
  `execution-decisions`, `execution-heartbeat`, `notifier-alerts`. The admin
  creates them at `firm bus up` (`NatsBus.ensure_consumers`, existing ones
  untouched so they keep their position), each delivering to
  `_INBOX.<service>.c.<name>`. Services can only look up and acknowledge
  their own (`auth.client_api`) and bind with `subscribe_bind`; they cannot
  create a consumer. `BusClient.subscribe(name, handler)` refuses another
  service's consumer. To add a reader: declare it here, restart `firm bus up`.
- `scripts/bus_smoke.py`: starts a throwaway server exactly as `firm bus up`
  does (on a free port, temp state) and checks the client behaviour plus
  enforcement by the server: an agent login can't publish `order.intent`
  even raw, a service can't delete or purge the stream, a wrong password is
  refused. Run it after changing `bus/`. Needs `nats-server` (README).
- Logging: the same client error is logged at most once a minute;
  disconnects and reconnects once each (not on our own `close()`).
- Gotcha: a refused publish surfaces as a JetStream ack timeout (the server
  drops it and reports a permissions violation to the connection's error
  callback, which logs it). A wrong password reconnects until timed out.
- Gotcha: the client's ID memory is in-process; after a restart a durable
  consumer resumes after its last ack, and the ledger refuses an event ID
  twice, so a replay still can't make a desk act twice.

## research (milestone 3)

Being built in the order of `docs/plans/milestone-3.md`. So far:

- **Private strategy store** (`research/store.py`, R1). Agent-written
  strategies never go in git: `save_strategy(root, dossier, code, stem=)`
  writes `<data root>/research/strategies/<dossier>/<stem>-<sha12>.py` once,
  named by content, so a changed strategy is a new file and a new
  candidate. Dossier IDs and stems are plain names (letters, digits, `-`,
  `_`), so nothing can write outside the store. `firm verify` and
  `firm paper run` take a path there like any other `PATH:CLASS`, and the
  verdict report cites the code's SHA-256 and file name, never its text or
  folder.
- `scripts/check_repo.py` refuses strategy code (a `def signals(` method in
  a `.py` or `.ipynb` file) anywhere except `examples/strategies/`, `src/`
  and `tests/`.
- Gotcha: saving to the store does not make code safe to run. The loader
  (`verify/strategy.py`) still imports the file in-process; agent code must
  wait for the sandboxed runner (R2).

## control
- `paper.py`: `PaperRun` (`firm paper run`), the composition root for one
  market on paper. With `bus` (the CLI default; `--no-bus` turns it off)
  risk, execution and the notifier each log in as themselves and talk only
  through the bus (`RiskService`, `BusRisk`, `BusAlerts`, `NotifierService`);
  the CLI refuses to start (exit 1, says how) when the bus isn't running,
  after the deployment gate. One process, one ledger writer. Then:
  deployment gate on the stored report; instrument rules
  from the venue (`Provider.rules_url` / `parse_rules`); `RestBars` (the
  venue's closed REST bars, fetched into the store: warm-up for the lookback,
  then each bar once its close + 10 s grace has passed, retried every 30 s,
  every missed bar after downtime); trades and FX streams; `paper_account`
  + `balances_after` (opening balances recorded once as `MARKET_STATUS
  paper_account_opened`, rebuilt from fills on restart); everything into the
  ledger, the interesting events to the terminal (`Tee`). `progress.py`
  (display only) prints each bar's outcome as it happens and a status line
  every `--status-every` seconds (price, holding, cash, equity, trades
  received, time to the next close); the CLI tees every line to
  `<state>/logs/paper-<market>-<venue>.log`. A refusal by the
  gate (or a missing report) exits 4 (`EXIT_REFUSED`), apart from errors (3),
  so a supervisor retries errors but never a refusal. Unattended runs:
  `deploy/` (systemd user unit `firm-paper@<name>`, settings outside the repo
  in `~/.config/firm/paper-<name>.conf`, runbook `deploy/README.md`);
  `tests/control/test_deploy_unit.py` keeps the unit in step with the CLI.
  `venue_rules` (shared with replay) fetches the venue's published rules.
- `replay.py`: `firm paper replay`. Feeds a dataset's closed bars through
  the same worker, risk desk and simulator as `paper run` (each bar: the
  simulator sees it, filling the previous order at its open; then the worker
  decides at its close, the backtest's timing) and compares with
  `run_backtest` on the same bars (signals masked to flat before the first
  decided bar, as the paper account starts flat): signal mismatches per bar,
  backtest trades without a paper order (with the worker's or risk's
  reason), extra paper orders, return and exposure. Default window: the
  report's hidden period; FX: the paper account's opening rate. Events go to
  a scratch ledger (`<state>/replays/...` or `--out`), never the firm's.
  Exit 1 if the signals disagree (lookback too short, or not causal).

- `cli.py`: `firm version`; `firm data synthetic`; `firm data build-dataset
  --bars P:S:TF [--bars ...] [--trades P:S ...]` (one dataset version across
  venues and kinds; legacy `--provider/--symbol/--tf` still accepted);
  `firm data add-symbol --venue --instrument --provider --symbol` (how one
  more provider, e.g. a live stream, spells an existing instrument: a new
  record version, never a remap); `firm state show [--json]` (read-only); `firm paper run --strategy P:C
  --verdict v-... --market --venue --instrument --tf [--hours N]`; `firm feed watch --provider P
  --symbol S [--seconds N] [--no-store]` (a live stream for a while: prints
  closed bars and status changes; needs network); `firm verify --strategy PATH:CLASS
  --dataset ds-... --venue --instrument --tf --market` (exit 0 pass, 1 fail,
  2 unverified, 3 error; report to `<state>/reports/<verdict>.json`).

## Tests

- `uv run pytest` runs everything; no network, no model.
- Placeholders are `xfail(strict=True)`: when you implement the behaviour,
  remove the marker or the run fails with XPASS.
- `tests/core/test_bus.py::test_permission_table_snapshot` pins who may
  publish what; edit it deliberately alongside `PUBLISHERS`.
- `tests/core/test_import_boundaries.py` fails if one desk imports another
  (desks may import `core` and `data`; only `control` imports desks).
- `tests/conftest.py` blocks sockets: any network use fails the test.
- `scripts/check_repo.py` (no data, secrets, machine paths, big files) runs
  in `.githooks/pre-commit`, in `tests/repo/` and in CI
  (`.github/workflows/ci.yml`).
- `tests/verify/test_exit_check.py` is the milestone 1 exit check.
