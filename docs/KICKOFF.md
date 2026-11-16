# Kickoff prompt — the firm — paste this into Claude Code

You are starting a new project: **the firm** (the system's name; CLI `firm`), a one-person
trading firm that runs on local models. Read `CLAUDE.md` and
`docs/SPEC.md` in full before doing anything else.

## Context in one paragraph

AI agents (run by Certo, github.com/alessioricco/certo) research and train
strategies on a DGX Spark. Deterministic code verifies them, risk-checks
them and executes them on a paper simulator against live market data.
Version 1 is paper money only. Markets: crypto first (Kraken, Coinbase),
US shares second (IBKR, EODHD), selected through YAML market profiles; the
owner is in the UK (jurisdiction profile). A local decision model (`d3` on
Ollama) acts as a judge whose outputs are features, never orders. A web UI
and a CLI share one control API. Notifications go to the owner only.

## Your job in this session: set up the repo, then milestone 1

### Step 0 — Repository skeleton (stop and show me before step 1)

1. Create the `src/firm/` package with these subpackages, empty except
   for interfaces and docstrings:
   `core` (typed domain objects), `data` (data plane + feature library),
   `verify` (verification desk), `risk`, `execution` (adapters + paper
   simulator), `ledger`, `research` (Certo integration), `judge`,
   `control` (API + CLI), `notify`, `scout` (opportunity scout), `markets` (one async worker per market).
2. Define the domain objects in `src/firm/core` as frozen dataclasses or
   pydantic models, matching the interfaces in the SPEC's components table:
   `StrategyCandidate`, `ModelArtifact`, `JudgeCall`, `Verdict`,
   `RiskDecision`, `OrderIntent`, `Fill`, `Notification`, `LedgerEvent`.
   Every `OrderIntent` must carry a `verdict_id` and a `risk_decision_id`.
   Define the bus message envelope too (id, subject, schema version,
   timestamp, sender service, payload) and the subject list from the
   SPEC section "Desks as services on a message bus", with a per-service
   publish permission table that a test checks.
3. Create `config/jurisdictions/uk.yaml`, `config/markets/crypto.yaml` and
   `config/markets/us_equities.yaml` from the SPEC's examples, plus a loader
   with schema validation that refuses unknown keys and refuses
   `live_trading: true` in v1.
4. Define the `VenueAdapter` interface in `src/firm/execution/` from the
   SPEC's "Integration layer" table: capabilities declaration, idempotent
   submit with client order IDs, the full order lifecycle, cancel,
   reconciliation of positions, balances and open orders, venue-side stops,
   rate limiting, instrument rules, and a `paper | sandbox | live` mode that
   refuses `live` while `live_trading` is false. Create
   `tests/conformance/` as one shared suite parametrised over adapters,
   with the paper simulator as the only adapter for now (tests may be
   `xfail` until milestone 2 builds the simulator).
5. Create `tests/invariants/` with one failing-by-design placeholder per
   firm rule in the SPEC, each named after the rule, marked `xfail` with a
   reason, so the list of rules is visible in the test report.
6. Create `TODO.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `.env.example`,
   `pyproject.toml` (uv, ruff, pytest, mypy) and a `firm` CLI entry point
   that prints its version.

Stop here. Show me the tree, the domain objects and the test report.

### Step 1 — Milestone 1: data plane and verifier (crypto)

Goal: a verifier that can say whether a hand-written strategy's backtest
is trustworthy, on versioned point-in-time crypto data.

1. **Data plane.** Ingest Kraken and Coinbase OHLCV bars and trades into
   versioned Parquet datasets with checksums. Every read API takes `as_of`
   and returns only data with timestamps at or before it. Delisted pairs are
   kept. Recorded fixtures for tests; real downloads only in a separate
   script I run myself.
2. **Backtest engine.** Event-driven, bar by bar, calling the strategy with
   only the data visible at each bar. Costs from the market profile (fees,
   spread, slippage).
3. **Verifier checks**, each a separate, tested module returning a typed
   result:
   - Lookahead truncation: rerun with data cut at each decision time;
     any changed past signal fails.
   - Hidden out-of-sample: a final period the strategy author never sees;
     opened once, logged.
   - Parameter sensitivity: perturb parameters; a collapse in results fails.
   - Cost realism: results must survive the profile's costs.
   - Trial count: record every trial and correct the result for it (pick and
     document a method in DECISIONS.md, e.g. a deflated Sharpe ratio).
   The verifier combines them into one `Verdict` with an ID, a report and an
   exit code a script can rely on.
4. **Exit check (all must hold):**
   - A test strategy with planted lookahead is rejected by the truncation check.
   - A clean moving-average crossover passes on the fixture data.
   - The same dataset version and strategy produce byte-identical reports.
   - `uv run pytest` is green with no network and no model.

Stop at the exit check and report: what was built, test results, open
questions, and anything you were unsure about. Do not start milestone 2.

## Rules for this session

- Follow the golden rules in `CLAUDE.md`, especially: no live trading, no
  LLM in the order path, point-in-time everything, config not code.
- Ask before adding any dependency not listed in `CLAUDE.md`.
- If something in the SPEC conflicts with `CLAUDE.md`, `CLAUDE.md` wins;
  write the conflict in TODO.md and tell me.
