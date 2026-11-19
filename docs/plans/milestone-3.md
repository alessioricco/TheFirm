# Milestone 3 plan: research floor and dashboard v1

Draft for the owner's review, 2026-11-19. The design was agreed on
2026-11-17 (SPEC "Research floor"); this file turns it into phases. Each
phase is one pull request, test-first for anything in verification, risk,
simulator, cost model or ledger, and each starts only after the owner says
go, as in milestone 2.

**Exit check (SPEC):** agents produce at least one strategy that passes
verification without human code changes; every trial they ran is in the
global count; no private data left the machines without a logged opt-in.

**Where it runs:** everything on the owner's Mac for now (owner,
2026-11-18). Hosts stay configuration, so the model server, the research
manager or the trading stack can move to the DGX or the Linux box later
without code changes. Open question Q1 covers models on the Mac.

## Order at a glance

| # | Phase | Why here | Size |
| --- | --- | --- | --- |
| R1 | Private strategy store | agent code must never reach the public repo | S |
| R2 | Sandboxed strategy runner | agent code runs nowhere unsandboxed | M |
| R3 | Data-egress gate in code | every model call is tagged, gated and logged | M |
| R4 | Experiment trials in the global count | firm rule 5 for agents | S |
| M1 | `config/research.yaml` and dossiers | budgets, gate settings, the researcher's record | S |
| M2 | Research manager (no model yet) | budgets, queue, pause/redirect/close, CLI | M |
| M3 | Researcher agent v1 | the loop that writes and submits strategies | L |
| M4 | Methods library | where ideas come from | S |
| M5 | Promotion gate and multi-strategy paper | verified strategies reach paper by rule | M |
| M6 | Dashboard v1 and control API | watch and steer the floor and paper trading | L |
| (later) | ML desk jobs | not needed for the exit check | — |

R1–R4 are the prerequisites already in TODO.md; nothing in M2 onwards runs
agent-written code or calls a model before they land.

## Prerequisites

**R1. Private strategy store.**
- Agent-written strategies live in the data store under
  `research/strategies/<dossier>/` (git-ignored, covered by the Mac's
  backup), never in the repo. Only hand-written examples stay public in
  `examples/strategies/`.
- `scripts/check_repo.py` refuses strategy files outside `examples/`, `src/`
  and `tests/`: any file with a strategy class or `def signals(`. A guard
  test proves it.
- `firm verify` and `firm paper run` accept a path in the private store.
  Reports cite the code's SHA-256, not its text.

**R2. Sandboxed strategy runner.** The loader imports and executes strategy
code today, which is fine for hand-written code and not for agent code.
- Agent strategies run in a separate worker process:
  - a clean environment with no `.env` or keys;
  - a temporary working folder;
  - CPU, memory and wall-clock limits;
  - no network: `sandbox-exec` on macOS, `bubblewrap` on Linux;
  - imports limited to an allow-list (numpy, pandas, the firm's feature
    library).
- Bars go in and signals come out through Arrow over a pipe. The verifier
  keeps one worker per verification, because it calls `signals` hundreds of
  times (truncation, sensitivity).
- Tests prove the worker can't open a socket, read `.env`, write outside
  its folder, or outlive its limits.

**R3. Data-egress gate in code** (firm rule 8, today only configuration).
- One gateway for every model call: `role`, `messages`, a tag (`public` or
  `private`) and an optional per-run opt-in.
- It refuses private work on a cloud provider without the opt-in, enforces
  the cloud budget (default £0), and writes a `MODEL_CALL` ledger event for
  every call with its tag, provider, tokens and cost.
- The rule 8 invariant test changes from xfail to a real test.

**R4. Experiment trials in the global count.**
- Every backtest a researcher runs is a trial with `experiment_id` and its
  bars per year. It is recorded through the same registry, published on
  `trial.recorded`, and written to the ledger.
- Tests check that a researcher can't run a backtest that isn't counted,
  and that the deflated Sharpe of a later candidate sees those trials.

## Build

**M1. Research config and dossiers.**
- `config/research.yaml` with a strict loader:
  - concurrent researchers (default 2);
  - per-dossier budgets: trials, wall-clock hours, tokens, cloud spend (0);
  - promotion gate: maximum paper strategies, maximum correlation with
    strategies already in paper, paper allocation per new strategy.
- No agent-facing interface can read or write it; firm rule 3 extended, with
  an invariant test.
- The dossier schema: hypothesis and rationale (written before any
  backtest), stage, code reference, trials, results, notes. Stored privately;
  every stage change is a ledger event.

**M2. Research manager**, deterministic, with a fake researcher (no model) in
the tests.
- Runs N researchers and queues the rest.
- Enforces budgets itself (the agent can't extend them) and stops a
  researcher whose budget is spent.
- Pause, resume, redirect (new brief), close; owner briefs per researcher or
  for the whole floor.
- CLI: `firm research start | list | show | pause | resume | redirect |
  close | brief`. Documented in `docs/COMMANDS.md` in the same PR.

**M3. Researcher agent v1.**
- The loop:
  1. idea with written rationale;
  2. strategy code, written by the research model through the gateway (R3);
  3. research loop: backtest, tune, walk-forward on the research view only.
     The data plane already refuses the hidden period, and every run is a
     trial (R4);
  4. submit to the verifier, which opens the hidden period once.
- Code runs only in the sandbox (R2) and is saved to the private store (R1).
- A failed submission closes the dossier; a changed strategy is a new
  candidate.
- How Certo fits is open question Q2. Exit-check run: one researcher, one
  brief, until a strategy passes or the budget is spent.

**M4. Methods library.**
- A short, public, generic list of method families with references: papers,
  books, public talks (momentum, mean reversion, pairs/stat arb,
  funding-rate carry, cross-exchange basis, volatility regimes, order flow).
- It also lists the families out of scope: market making and high frequency.
- Researchers search it by full text. It gives methods, not answers.

**M5. Promotion gate and multi-strategy paper.**
- The gate is deterministic: a passing verdict, correlation with paper
  strategies below the limit, fewer than the maximum running.
- A promoted strategy gets its paper allocation behind the risk desk; the
  owner is notified and can veto.
- An invariant test checks that no agent-facing interface can promote a
  strategy or change the gate.
- `firm paper run` grows from one strategy into a paper service hosting
  several deployments, with one risk desk across them. That is the larger
  part of this phase.

**M6. Dashboard v1 and minimal control API.**
- FastAPI with REST and one WebSocket, on loopback only for now, behind
  authentication.
- Two views:
  - **research floor:** researchers, dossiers, stage, trials against budget,
    results, live activity, and the steering actions;
  - **paper trading:** positions, fills, P&L per strategy and in GBP, risk
    use and halts, feed health, kill switch with confirmation.
- Stack as decided: plain JS modules, Lightweight Charts with the
  TradingView attribution kept, ECharts, GridStack with saved layouts, all
  vendored.
- Adds `firm paper status`.

**Later, not in the exit check:** ML desk jobs (training runs, model
artifacts with their training record, capped memory). They need the DGX to
be worth it, and a rules-based researcher can meet the exit check without
them.

## Open questions for the owner

1. **Models on the Mac.** The research role is set to `gpt-oss:120b`
   (about 65 GB), sized for the DGX. Is Ollama installed on the Mac, and how
   much memory does it have? Options:
   - (a) a smaller local model on the Mac for now;
   - (b) the DGX as model server only, reached over Tailscale, with
     everything else on the Mac;
   - (c) a frontier model for public, generic work only, through the egress
     gate. Strategy code is private, so this doesn't cover the researcher
     itself.
2. **Certo.** Is Certo installed on the Mac? Should the researcher loop run
   inside Certo (its ladder, budgets and verdicts), or start as a minimal
   loop of our own on the gateway, with Certo added once it works?
3. **Sandbox.** OK with a separate process plus the OS sandbox
   (`sandbox-exec` on macOS, `bubblewrap` on Linux, a new system package
   there), with a pandas/numpy-only import allow-list for agent strategies?
4. **Private store location.** `$FIRM_DATA_ROOT/research/`, covered by Time
   Machine like `data/`?
5. **Promotion gate defaults.** Proposed: at most 3 paper strategies, at most
   0.7 correlation with any of them, 10% of paper capital each. Placeholders
   in config either way.
6. **Dashboard timing.** At the end (M6) as listed, or right after M2 so the
   floor can be watched while the agent is built?
