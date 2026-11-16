# Architecture

How each module works and its gotchas. Read the section before touching a
module; update it when you change one. Status as of step 0 (skeleton).

## Layout

```
src/firm/
  core/         typed domain objects, config profiles, bus messages (no deps on other desks)
  data/         data plane + feature library            (M1)
  verify/       verification desk                       (M1)
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
scripts/
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

## execution

- `adapter.py`: `VenueAdapter` ABC. Constructor runs the live-mode gate and
  checks the mode is in `capabilities().modes`. Methods (async): `submit`
  (idempotent on `client_order_id`), `cancel`, `order_status`, `open_orders`,
  `positions`, `balances`, `reconcile`, `instrument_rules`. Sync:
  `capabilities()`. Supporting types: `VenueMode`, `VenueCapabilities`,
  `RateLimitPolicy`, `InstrumentRules`, `Position`, `Balance`,
  `ReconciliationSnapshot`, `LiveTradingRefused`.
- `simulator.py`: `PaperSimulator`, paper mode only; trading methods raise
  `NotImplementedError` until M2.

To add a venue: implement `VenueAdapter`, register a factory in
`tests/conformance/conftest.py::ADAPTERS`, pass the suite (`/add-venue-adapter`).

## control

- `cli.py`: `firm --version`, `firm version`. Entry point in `pyproject.toml`.

## Tests

- `uv run pytest` runs everything; no network, no model.
- Placeholders are `xfail(strict=True)`: when you implement the behaviour,
  remove the marker or the run fails with XPASS.
- `tests/core/test_bus.py::test_permission_table_snapshot` pins who may
  publish what; edit it deliberately alongside `PUBLISHERS`.
- `tests/core/test_import_boundaries.py` fails if one desk imports another.
