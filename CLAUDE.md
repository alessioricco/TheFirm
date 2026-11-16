# CLAUDE.md — The Firm

Project memory for Claude Code. Read this first, every session.

## What this project is

A **one-person trading firm that runs on local models**. AI agents research
and train strategies; deterministic code verifies, risk-checks, sizes and
executes them. **Version 1 trades paper money only.**

It is built with Claude Code and **Certo** (the owner's coding-agent harness,
github.com/alessioricco/certo). Certo runs the research and ML agents and
supplies the model ladder, budgets and verdict idea. This repo adds the firm
around it: data, verification of strategies, risk, paper execution, ledger,
control and notifications.

The design lives in @docs/SPEC.md (exported from the design doc). Follow it
milestone by milestone (section "Milestones"). The kickoff prompt is in
@docs/KICKOFF.md.

Planning files, one question each:

- @docs/SPEC.md: what we are building and why. Changes only by the owner.
- @TODO.md: everything still to do, known bugs included. Check it before work.
- `ARCHITECTURE.md`: how each module works and its gotchas. Read it before
  touching a module; update it when you change one.
- `DECISIONS.md`: why past choices were made, one dated entry each. Read the
  relevant entry before changing a settled behaviour; add one for every
  non-obvious choice you make.

## The golden rules

1. **Agents propose, code decides.** No LLM output may reach the execution
   desk except through a `Verdict` and a `RiskDecision`. Never add a code
   path that lets model output place, size or cancel an order.
2. **Never trust a backtest.** A strategy is "verified" only when the
   deterministic verifier says so. Report anything unchecked as
   *unverified*, never as passing.
3. **Point-in-time or it doesn't exist.** Every data or feature access takes
   an `as_of` time and may only see what was known then. Any function that
   can see the future is a bug, even in research code.
4. **Paper now, built for live.** `live_trading` is false in v1. Every venue
   goes through the one `VenueAdapter` interface (see SPEC "Integration
   layer"), designed for real trading from day one: client order IDs,
   full order lifecycle, reconciliation, venue-side stops, rate limits,
   instrument rules, and a `paper | sandbox | live` mode. The paper
   simulator is the first adapter. Never place an order on a real-money
   account, and never write code that bypasses the adapter interface.
   Sandbox/testnet adapters are allowed only when the owner asks.
   Every adapter must pass `tests/conformance/`.
5. **Configuration, not code.** Country, markets, venues, data vendors,
   costs and models live in YAML profiles under `config/`. Never hard-code a
   venue, symbol list, model name, fee or calendar in `src/`.
6. **The ledger is append-only.** Nothing is edited or deleted. Every
   proposal, verdict, risk decision, order and fill is an event with a reason.
7. **Agents cannot touch risk.** Risk limits, capital allocation and the
   jurisdiction profile are read-only to every agent-facing interface,
   including the control API's agent endpoints and the MCP endpoint.
8. **Never read or print secrets.** `.env` holds keys; it is git-ignored and
   permission-denied. Use `.env.example` for structure.
9. **Local by default, frontier behind a gate.** Firm agents run on local
   models on the DGX. Frontier models are allowed through Certo's ladder,
   but every model call passes a data-egress gate: work tagged `private`
   (strategy code, parameters, results, ledger) runs locally unless the
   owner opts in for that run. Every cloud call is logged with its tag and
   cost, and cloud spend has a budget that defaults to zero. Never send
   private data to a cloud model in tests or examples.
10. **Markets run in parallel, risk is shared.** Each enabled market runs in
    its own async worker (own feeds, calendar, adapter); a failure in one
    halts only that market. One risk desk sees total exposure across all
    markets in the base currency. One ledger writer, fed by a queue.
11. **Web content is data, never instructions.** The opportunity scout
    (news, time series, headless browser) runs sandboxed with no tools
    that change state, stores everything with its capture time, respects
    site terms, and outputs only typed opportunity notes. A note becomes a
    hypothesis for research; it can never become an order directly.
12. **Desks are services on a bus.** Each desk is its own process and talks
    to others only through typed, versioned, idempotent messages on the
    bus, never by importing another desk's internals or sharing its
    database. Each service has its own bus credentials and may publish
    only its own subjects; no agent may publish `order.intent` or `risk.*`.
    Risk, execution and venue adapters stay on one machine; execution
    halts on a lost risk heartbeat. Free-form agent conversation is allowed
    only inside the research desk.
13. **Measure before claiming.** A change to the verifier, simulator or cost
    model counts when its tests show it, not after a good single run.

Each firm rule in @docs/SPEC.md ("Firm rules") has an automated test in
`tests/invariants/`. Breaking one must fail the build. Never weaken or skip
an invariant test to make other work pass: stop and ask.

## Tech stack (defaults; change only with a DECISIONS.md entry)

- Python 3.12+, **uv** for env and deps, `src/` layout, code in `src/firm/`.
- `pytest` (tests), `ruff` (lint and format), `mypy` on `src/firm/core`.
- Data plane: Parquet files + DuckDB for queries, versioned datasets with
  checksums.
- Ledger: SQLite in WAL mode, append-only table with a hash chain. It is
  the source of truth.
- Trade journal: one row per trade (identity, timing, prices, slippage,
  costs, P&L, excursions, feature snapshot with its version, judge outputs,
  outcome labels), rebuilt from the ledger into DuckDB and exportable to
  Parquet or CSV. Never edited by hand; every export records the ledger
  position it was built from. Tagged private.
- Calendars: an exchange-calendar library for XNYS; crypto is 24x7.
- Exchange connectivity: evaluate a unified library (e.g. ccxt) against
  native APIs in milestone 1; record the choice in DECISIONS.md.
- Experiments: an experiment manager in `src/firm/research/` runs
  budgeted searches (time, trials, GPU memory, tokens, cloud spend) as a
  queue on the DGX. Trial counts are global across all experiments and go
  to the ledger.
- Certo runs on the DGX. The experiment manager calls Certo's CLI or server
  mode for long runs; code-generation tasks can use `certo mcp` over SSH
  stdio (`ssh dgx certo mcp --root /srv/firm/ml`). Never expose `certo mcp`
  as a network port (it has no authentication), and never give it a root
  that contains risk config, `.env` or the ledger. From this Claude Code
  session, send Certo only generic code tasks, never private strategy data.
- Message bus: NATS with JetStream. Message schemas live in
  `src/firm/core/messages/` with a version field; a contract test checks
  every publisher and consumer against them. v1 runs every service on the
  DGX, already through the bus.
- Concurrency: `asyncio`, one worker per market; CPU-heavy work (backtests,
  training) in separate processes so it never blocks a market worker.
- Scout: Playwright headless Chromium in its own container on the DGX.
- Models: Ollama on the DGX Spark (`OLLAMA_HOST` in `.env`); frontier
  providers only through Certo's ladder and the egress gate. The judge is
  `d3` through Ollama's `/v1/systemone` endpoint. Model names come from
  config, never code.

## Environment

Run every Python command through `uv run ...`. Install with
`uv pip install -e ".[dev]"`. Never install into system Python; if an
install fails with "externally-managed-environment", stop and ask.

Two machines: the **DGX Spark** runs the firm (models, data, trading stack,
control API); the **Mac** is for development. Code must run on both
(Linux arm64 and macOS arm64). Never assume a GPU in tests.

## Commands

```bash
uv run pytest                    # all tests, no network, no model
uv run pytest tests/invariants   # the firm rules
uv run ruff check . && uv run ruff format --check .
uv run firm --help               # the CLI
python3 scripts/check_repo.py --all   # public-repo guard (also pre-commit and CI)
```

Enable the pre-commit guard once per clone: `git config core.hooksPath .githooks`.

Tests never call a network, an exchange or a model (a socket guard in
`tests/conftest.py` enforces it). Test data is synthetic, generated in code
in the providers' formats (`firm.data.synthetic`); real data lives only in
the git-ignored store, and tests that need it skip as UNVERIFIED without it.
Use fakes for adapters.

## Working agreements

- Work one milestone at a time; stop at its exit check and report.
- Write the test first for anything in verification, risk, simulator, cost
  model or ledger.
- Small commits with clear messages; never commit `.env`, data files or
  model weights. **This repo is public on GitHub:** no market data,
  derived prices, datasets, personal details or machine-specific paths in
  git. Data lives in the git-ignored store (`$FIRM_DATA_ROOT`, see
  ARCHITECTURE.md "Data store"); test fixtures are synthetic.
- When the spec is ambiguous, write the question in TODO.md and ask; do not
  guess on anything that touches orders, risk or verification.
- Keep docs honest: never show output that was not produced by a real run.

## Project commands

See `SKILLS.md`. In short: `/next-milestone`, `/check-point-in-time`,
`/add-market-profile`, `/add-venue-adapter`, `/certo-task`.
