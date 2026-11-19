# Milestone 3 plan: research floor and dashboard v1

Owner's answers 2026-11-19 are folded in (see "Decisions" at the end). The design was agreed on
2026-11-17 (SPEC "Research floor"); this file turns it into phases. Each
phase is one pull request, test-first for anything in verification, risk,
simulator, cost model or ledger, and each starts only after the owner says
go, as in milestone 2.

**Exit check (SPEC):** agents produce at least one strategy that passes
verification without human code changes; every trial they ran is in the
global count; no private data left the machines without a logged opt-in.

**Where it runs:** the firm runs on the owner's Mac; the models run on the
DGX Spark (Ollama, 128 GB), reached over the private network
(`OLLAMA_HOST` in `.env`). Certo runs on the Mac for now and moves to the
DGX later. Hosts stay configuration throughout.

## Order at a glance

| # | Phase | Why here | Size |
| --- | --- | --- | --- |
| R1 | Private strategy store | agent code must never reach the public repo | S |
| R2 | Sandboxed strategy runner | agent code runs nowhere unsandboxed | M |
| R3 | Data-egress gate in code | every model call is tagged, gated and logged | M |
| R4 | Experiment trials in the global count | firm rule 5 for agents | S |
| M1 | `config/research.yaml` and dossiers | budgets, gate settings, the researcher's record | S |
| M2 | Research manager (no model yet) | budgets, queue, pause/redirect/close, CLI | M |
| M3 | Dashboard v1 and control API | the owner watches the floor while the agent is built | L |
| M4 | Researcher agent v1 | the loop that writes and submits strategies | L |
| M5 | Methods library | where ideas come from | S |
| M6 | Promotion gate and multi-strategy paper | verified strategies reach paper by rule | M |
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
    library), widened later to standard ML tools (scikit-learn, Prophet)
    as the owner approves them.
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

**M3. Dashboard v1 and minimal control API.** Right after the manager
(owner, 2026-11-19: "I want to watch the research").
- FastAPI with REST and one WebSocket, on loopback only for now, behind
  authentication.
- Two views:
  - **research floor:** researchers, dossiers, stage, trials against budget,
    results, live activity, and the steering actions;
  - **paper trading:** positions, fills, P&L per strategy and in GBP, risk
    use and halts, feed health, kill switch with confirmation (the running
    paper run is its first subject).
- Stack as decided: plain JS modules, Lightweight Charts with the
  TradingView attribution kept, ECharts, GridStack with saved layouts, all
  vendored.
- Adds `firm paper status`.

**M4. Researcher agent v1.**
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
- The loop runs through Certo (its ladder, budgets and code verdict), on
  the Mac for now with the research model on the DGX; when Certo is
  installed on the DGX the loop moves there by configuration. Certo's
  verdict is about the code; the firm's verifier still judges the strategy.
  Exit-check run: one researcher, one brief, until a strategy passes or the
  budget is spent.

**M5. Methods library.**
- A short, public, generic list of method families with references: papers,
  books, public talks (momentum, mean reversion, pairs/stat arb,
  funding-rate carry, cross-exchange basis, volatility regimes, order flow).
- It also lists the families out of scope: market making and high frequency.
- Researchers search it by full text. It gives methods, not answers.

**M6. Promotion gate and multi-strategy paper.**
- The gate is deterministic: a passing verdict, correlation with paper
  strategies below the limit, fewer than the maximum running.
- A promoted strategy gets its paper allocation behind the risk desk; the
  owner is notified and can veto.
- An invariant test checks that no agent-facing interface can promote a
  strategy or change the gate.
- `firm paper run` grows from one strategy into a paper service hosting
  several deployments, with one risk desk across them. That is the larger
  part of this phase.

**Later, not in the exit check:** ML desk jobs (training runs, model
artifacts with their training record, capped memory). They need the DGX to
be worth it, and a rules-based researcher can meet the exit check without
them.

## Certo integration and model cost

Grounded in Certo's own documentation (checkout at 0.2.4; file references
are to that repo). Certo is used only through its documented interfaces.

**How the firm calls Certo.**
- One coding task = one `certo --json --project <dossier> <task file>` run,
  started by the research manager as a subprocess
  (`docs/reference/commands.md`). The manager reads the documented JSON
  result (`docs/reference/json-result.md`, `result_version` 1): `verdict`,
  `checks`, `files_changed`, `check_file_changes` (weakened tests) and
  `usage` (tokens, duration, cost).
- Exit codes: 0 means `verified`, `inconclusive` or `unchanged`;
  `--require-verification` makes the last two fail.
- Not the HTTP server (no authentication, no milestones) and not
  `certo mcp` (experimental, stdio, one run per folder) in v1.
- Certo's verdict is about the code (tests and build pass). The firm's
  verifier still judges the strategy.
- **Upgrade first:** the Mac has Certo 0.2.2. The 0.2.3 and 0.2.4 releases
  add the project-root guard, "least costly" auto-model and the `notices`
  field. The plan assumes 0.2.4 on the Mac, and later on the DGX.

**Local models only, by configuration.**
- `CERTO_LLM_MODEL=ollama/<model>` and `CERTO_LLM_BASE_URL=http://<dgx>:11434`,
  with a long timeout and a context of at least 16K
  (`docs/guides/ollama-and-local-models.md`, "another machine").
- Certo has no "never use cloud" switch, so the firm enforces it:
  - a firm-owned Certo `models.yaml` that lists only the DGX's models
    ("only the models listed here are ever used",
    `docs/reference/models-yaml.md`);
  - an explicit local-only ladder if escalation is used. A derived ladder can
    climb into cloud models (`docs/guides/escalate-between-models.md`);
  - planner model local too; update check off.
- The egress gate (R3) refuses to start a Certo run for private work with
  any non-local model.

**Cost and budgets.**
- Local models cost $0 in Certo's accounting, and so do unknown prices
  (`docs/guides/run-records-and-costs.md`), so `CERTO_MAX_TASK_COST` does
  nothing for local runs.
- The real costs are DGX time and tokens. The research manager budgets each
  run with Certo's limits: `CERTO_MAX_TASK_SECONDS`,
  `CERTO_MAX_ITERATIONS`, `CERTO_MAX_VERIFY_RETRIES`
  (`docs/concepts/safety.md` "Budgets"; `docs/reference/configuration.md`).
- It also counts tokens and hours per dossier from `usage` against the
  dossier's budget in `config/research.yaml`.
- Every Certo run becomes a `MODEL_CALL` ledger event (role, model, tag,
  tokens, duration, cost, verdict), and the dashboard shows spend against
  budget per dossier.
- LLM work is kept to what needs it: writing the hypothesis and the code.
  Backtests, parameter search and walk-forward are deterministic code
  (every run still a counted trial), not model calls.
- Independent tests roughly double a run's cost
  (`docs/guides/keep-costs-down.md`), so they're off by default.
- Cloud spend stays £0 unless the owner raises the budget. Private strategy
  work never goes to the cloud without a per-run opt-in.

**One model server, many researchers.**
- A local server serves one model at a time; Certo refuses parallel
  benchmarks on local models (`docs/troubleshooting.md`).
- The manager therefore queues Certo runs per model: by default one at a
  time on the DGX, even with 2 researchers. Researchers spend most of their
  time in deterministic backtests anyway.

**Certo is not a sandbox.**
- In local mode Certo runs commands, including the tests it writes, as the
  user, with the network open; the project-root guard is "a guard rail, not
  a sandbox" (`docs/concepts/safety.md`). Docker mode is experimental and
  leaves default networking.
- So:
  - each dossier's Certo project lives in the private research workspace
    (`CERTO_PROJECTS_DIR` under `$FIRM_DATA_ROOT/research/`), holding no
    secrets, ledger, risk config or `.env`;
  - the Certo process itself runs under the same OS sandbox profile as R2,
    with file access limited to that workspace and network only to the
    model server.

## Decisions (owner, 2026-11-19)

1. **Models:** Ollama on the DGX Spark (128 GB) serves every role, reached
   from the Mac over the private network. The research role keeps its
   ~120B MoE model. Nothing private leaves the firm's machines.
2. **Certo:** available on the Mac now; it must be installed on the DGX
   (owner). Until then the researcher loop runs Certo on the Mac, using the
   DGX's models. Use Certo's documentation and be cost-conscious about
   models (owner): see "Certo integration and model cost".
3. **Sandbox:** yes, a separate locked-down process. The import allow-list
   starts with pandas, numpy and the feature library; standard ML tools
   (scikit-learn, Prophet, ...) are added later.
4. **Private strategies:** `$FIRM_DATA_ROOT/research/`, backed up with the
   rest of `data/`.
5. **Promotion defaults:** at most 3 paper strategies, correlation at most
   0.7 with any of them, 10% of paper capital each (config placeholders).
6. **Dashboard:** right after the research manager, so the owner can watch
   the research while the agent is built (now M3).
