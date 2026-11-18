# The Firm — Design v0.2

Nov 15, 2026 · @Alessio · v0.2 Nov 16, 2026: decisions from step 0 and milestone 1 (details and reasons in `DECISIONS.md`). This file is the source of truth; it changes only through a pull request the owner reviews.

## Purpose and principles

Build a one-person trading firm that runs on local models: agents research and train strategies, deterministic code verifies, sizes and executes them, and version 1 trades paper money only. It is built with Claude Code and Certo, and v1 runs on a DGX Spark (nothing in the code assumes a particular machine).

**In scope for v1:** crypto, then US shares; paper trading against live market data; strategy research and model training by agents; a web UI and CLI; notifications to the owner only.

**Out of scope for v1:** live trading with real money; signals or advice for other people; high-frequency trading.

**Principles**

- **Agents propose, code decides.** No language model is ever in the order path.
- **Never trust a backtest.** A strategy counts only with a verdict from deterministic checks.
- **Local-first.** Local models on the DGX by default; frontier models through Certo's ladder when a task needs them, behind a data-egress gate. Private strategy data goes to a cloud model only with an explicit, logged opt-in.
- **Configurable, not hard-coded.** Country, markets, venues and data vendors are swappable profiles.
- **Everything is recorded.** Every proposal, verdict, refusal and fill goes in the ledger with its reason.
- **Paper first, live by hand.** Live trading stays off until the owner turns it on manually.
- **A laboratory first.** Research, training, strategy design and paper trading may use any data source; real money only on venues the jurisdiction profile permits, behind the live readiness gate.
- **Public code, private data.** The repository is public. Market data, derived prices, datasets, results, strategy parameters that matter, and secrets never go in git; they live in git-ignored stores, and tests use synthetic data.

## Architecture overview

The firm has three desks in a line: AI research proposes, deterministic gates verify and approve, deterministic trading executes on paper.

&#91;embedded content: firm architecture · 3 desks, 3 shared services, control\]

Candidates flow left to right; the judge and the data plane feed in from below, and every step lands in the ledger, which drives your notifications.

## Components

Eleven components, split by one rule: AI may only produce candidates, features and reports; only deterministic code may approve, size or place an order.

| Component | Kind | Does | Never does | Main interface |
| --- | --- | --- | --- | --- |
| Research desk | AI agents (Certo) | Writes hypotheses, strategy code and experiments; experiment manager runs budgeted searches on the DGX | Places orders, reads the final out-of-sample data | `StrategyCandidate` (code + spec + trial log) |
| ML desk | AI agents + GPU jobs | Trains and tunes models, logs every trial | Changes features after seeing out-of-sample data | `ModelArtifact` (weights + training record) |
| Judge | Local decision model | Typed calls: news events, regime, proposal consistency | Outputs orders; its outputs are features only | `JudgeCall` (state + questions → typed answers) |
| Verification desk | Deterministic | Lookahead truncation, hidden out-of-sample, sensitivity, costs, trial count | Uses an LLM to decide a verdict; calls anything unchecked a pass | `Verdict` (pass / fail / unverified + report + ID) |
| Risk desk | Deterministic | Position, exposure and drawdown limits; kill switch; veto | Accepts a change from an agent; resizes an order upwards | `RiskDecision` on an `OrderProposal` (approve / resize down / reject + reason) |
| Execution desk | Deterministic | Sizes orders as proposals; places and reconciles approved orders through an adapter | Trades without a verdict ID and a risk approval | `OrderProposal` → `RiskDecision` → `OrderIntent` → `Fill` |
| Paper simulator | Deterministic | Fills orders against live data with fees, spread, slippage, partial fills | Fills at prices that weren't available | Same as a broker adapter |
| Data plane | Deterministic | Ingests, versions and serves point-in-time market data; feature library (ari\_signals Level 0 generator, \~30 indicators → JSON, behind a fixed-lookback, closed-bar wrapper) | Serves data from after the requested timestamp | `bars()`, `trades()`, `book()`, `universe()` with an `as_of` time, on an exact dataset version |
| Ledger | Deterministic store | Records every proposal, verdict, decision, order and fill | Lets anything be edited or deleted | Append-only events, replayable |
| Notifier | Deterministic | Urgent alerts, actions, daily digest to the owner | Accepts commands other than status and kill switch | `Notification(level, text, links)` |
| Control API | Deterministic | Serves the web UI, CLI and a read-only MCP endpoint | Lets agents promote strategies, change the promotion gate or edit risk | REST + WebSocket, authenticated |

A strategy moves through fixed stages: idea → verified backtest → paper trading → (later) small live → (later) scaled. Live drifting from the backtest demotes it automatically.

## Configuration

Profiles keep the core free of country and market details: one jurisdiction profile (where you live) and one market profile per market (what you trade), plus a risk profile, fee schedules, verifier thresholds and model choices. Moving country or adding a market means a new YAML file, not new core code. The loader is strict: unknown keys are an error, and `live_trading: true` is refused in v1.

**Jurisdiction profile** — `config/jurisdictions/uk.yaml`

```yaml
jurisdiction: uk
base_currency: GBP
allowed_venues: [kraken, coinbase, ibkr]
venue_status:              # every allowed venue needs an entry
  kraken: {regulator: FCA, status: registered, as_of: 2026-11-15, review_by: 2027-10-01}
  # ... coinbase, ibkr likewise; status: authorised | registered | pending | not_permitted
leverage_max: 1.0          # no leverage in v1 (refused above 1.0)
tax:
  report: uk_cgt
  dividend_withholding: w8ben
notifications:
  recipients: [owner]      # never other people
live_trading: false         # manual switch only
```

**Market profile** — one per market, e.g. `config/markets/us_equities.yaml`

```yaml
market: us_equities
calendar: XNYS             # exchange hours, holidays, early closes
currency: USD
universe:
  source: eodhd
  include_delisted: true
data:
  history: eodhd
  realtime: ibkr
costs:
  commission: ibkr_tiered   # a fee schedule in config/fees/
  spread_model: quoted
  spread_bps: 2             # assumed spread when there are no quotes (bar backtests)
  slippage_bps: 5
  fx_conversion_bps: 3      # GBP to USD
corporate_actions: [splits, dividends]
broker:
  paper: simulator
  live: ibkr                # inactive while live_trading is false
```

The crypto profile has the same shape, with `calendar: 24x7` and funding rates instead of corporate actions; crypto pairs are USD-quoted, and `commission` maps each venue to its own fee schedule (Kraken and Coinbase charge differently). A strategy declares which market profiles it runs on, and the verifier uses that profile's costs and calendar. All numbers are placeholders still to calibrate.

**Other profiles**

| File | Holds | Who may read it |
| --- | --- | --- |
| `config/risk/<name>.yaml` | Limits and caps, e.g. the per-exchange exposure cap for crypto | Deterministic desks only; no agent |
| `config/fees/<name>.yaml` | One schedule per venue and tier: the venue's real published maker/taker fees, with source and date | Everyone |
| `config/verify/<name>.yaml` | Verifier thresholds | Read by the verifier; no agent may change them |
| `config/models.yaml` | Model per role, Q&A approach, egress policy (cloud budget 0) | Everyone; changed by the owner |

Hosts and keys go in `.env` (git-ignored); choices go in YAML. No venue, symbol list, fee, calendar or model name is written in code.

## Venues and data sources

Start cheap: free exchange data for crypto and IBKR plus EODHD for US shares, adding paid tick data only when a strategy needs it. All of these are proposals to confirm before purchase; prices and terms change.

| Need | Crypto | US shares |
| --- | --- | --- |
| Venue (paper now, live later) | [Kraken](https://www.bitget.cloud/academy/coinbase-fca-regulat), Coinbase (both FCA-registered) | [Interactive Brokers](https://global-best-ai-brokers-uk.pages.dev/interactive-brokers-review) (paper account, mature API) |
| Lab-only data sources | Binance (public market data; not permitted for real money in the UK profile) | Yahoo Finance (unofficial, personal use; daily history, prices stored un-split-adjusted) |
| Real-time data | Exchange WebSockets (free) | IBKR market data subscription; Databento or Massive if needed |
| Historical bars | Exchange REST APIs (free) | [EODHD](https://eodhd.com/financial-apis/delisted-stock-companies-data-2) (includes delisted symbols) |
| Tick and order book history | [Tardis.dev](https://coinranking.com/blog/best-historical-crypto-data-apis/), from $450/month, history from 2019 (later) | Databento (later) |
| Survivorship-free universe | Keep delisted coins in our own store | EODHD delisted list; [Norgate](https://norgatedata.com/) as a cross-check (US daily only) |

Every source sits behind one provider interface (`firm.data.providers`: declared capabilities, a request plan, parsers that emit closed, unadjusted bars) and a registry; adding a source is one module plus tests. Built in: Kraken, Coinbase, Binance, Yahoo. Sources are called over their public HTTP APIs directly (no unified library: the store keeps each source's own bytes); a library-backed provider fits the same interface if one is ever needed. Kraken serves only the last 720 bars of a timeframe; Coinbase pages back to 2015. Public trade endpoints give recent trades only; deep trade history needs a paid vendor (later).

**Rules for the data plane**

- Every query takes an `as_of` timestamp and returns only what was known then. A bar is known at its close; a still-forming bar never enters the store.
- Raw data is stored as received, with a dataset version and checksum, so any backtest can be reproduced.
- Delisted instruments stay in the universe forever; an instrument's status is evaluated as of the query, so a later delisting never leaks backwards.
- Every series is keyed by timeframe (1m to 1w) and records how its bars are aligned. Sources differ (Kraken's weeks start Thursday, Binance's Monday, share data follows exchange sessions), so the store refuses bars off their source's declared grid, and coarser timeframes can be derived on one canonical grid (days 00:00 UTC, weeks Monday) from complete sets of finer bars, stored apart from native series.
- Implausible bars are quarantined, never silently trusted. A venue's own candles can be corrupt (a bar opening at ~0), and raw captures keep them exactly as received; the normaliser leaves such bars out as gaps under a versioned rule in `config/data/` (a price too many times away from the last accepted close: point-in-time) and lists them in the dataset manifest.
- Feed staleness raises an urgent notification and blocks new paper orders on that market.
- Data never goes in git. The store (`$FIRM_DATA_ROOT`) is git-ignored and has fixed zones, all written once: `raw/` (exactly as received, with a sidecar describing each download), `normalized/` (Parquet in the firm's schema), `datasets/` (content-addressed versions that backtests cite), `reference/` (instrument master), `golden/` (frozen test baselines). Synthetic and real data never mix in one series.

The UK crypto regime changes in October 2027; each venue's status lives in the jurisdiction profile (`venue_status`, with a review date) so it can change without code.

## Models and hardware

The DGX Spark runs the whole firm 24/7 on mixture-of-experts models; the Mac is where you build and approve. The Spark's limit is memory bandwidth (about 273 GB/s), so large dense models are too slow: [LMSYS measured a dense 70B at 2.7 tokens/s](https://verdictbits.com/reviews/nvidia-dgx-spark-2026/), while [gpt-oss-120b decodes at about 43 tokens/s under Ollama](https://cline.bot/blog/what-a-sigkill-race-reveals-about-inference-speed).

**Memory plan (128 GB shared, approximate)**

| Role | Model (candidate) | Memory |
| --- | --- | --- |
| Research and coding agents | \~120B MoE, e.g. gpt-oss-120b or DeepSeek V4 Flash | \~65 GB |
| Fast workers | \~35B MoE, e.g. Qwen 3.6 35B-A3B | \~20 GB |
| Decision model (judge) | d3 on Ollama (vLLM Semantic Router, Apache 2.0): start with d3:9b, test d3:27b | \~10 GB (27b: \~29 GB) |
| Trading stack and data | No LLM | \~10 GB, always reserved |
| Training headroom | ML desk jobs, capped | \~15 GB |

**Rules**

- The trading stack's memory and CPU are reserved; training runs at lower priority and can never delay an order or a stop.
- Large training runs unload the big agent model first; a scheduler owns that switch.
- Models are chosen by Certo's benchmark on our own agent tasks (most verified results), not by speed.
- Ollama to start; vLLM or SGLang when many agents run at once. Certo's model ladder hides the difference.
- The model for each role is configuration (`config/models.yaml`), never code.

**Roles of each machine**

- **DGX Spark:** model server, research and experiment agents, ML training, the research data store, the verifier and the research ledger.
- **Linux box (always on):** the trading stack 24/7: market workers, risk, paper simulator, execution, its own ledger, notifier. No GPU needed. It can also back up the DGX's stores.
- **Mac:** Claude Code and Certo for building the harness; web UI and CLI for approvals and monitoring.
- **Watchdog:** a small service off the trading machine (the Linux box for the DGX, a tiny cloud server for the Linux box) that only checks heartbeats and alerts on Telegram if one goes silent. A UPS for each always-on machine is recommended.

This is the v1 deployment, not a requirement: hosts are configuration, and any service can move to another machine. Whatever runs the data and state stores needs its own backup.

## Control and notifications

One control API on the DGX serves both the web UI and the CLI, so anything you can click you can also script, and both always show the same state. It grows from Certo's existing CLI and server mode.

**What you can do**

| Area | Web UI and CLI |
| --- | --- |
| Agents | Start, pause, stop; assign research tasks; set budgets and model ladders; watch runs and verdicts |
| Research floor | Researchers and their dossiers: stage, hypothesis, trials used vs budget, backtest results, live log of what each is doing; start, pause, redirect, close; write briefs |
| Strategies | Each strategy's stage and verification report; veto a paper promotion; approve live (later) |
| Markets | Watchlists, live prices and charts, market hours, judge regime labels |
| Paper portfolio | Positions, fills, P&L per strategy and total, live-versus-backtest drift |
| Ledger | Search any decision or order and replay why it happened |
| Data | Captures, series, gaps, revisions, dataset versions, updates; real versus synthetic |
| Analysis | Technical-analysis documents on demand; "ask the firm" questions with cited sources |
| System | Risk limits (read-only), feed health, model server load, GPU memory, kill switches |

**CLI sketch**

```
firm agents list
firm research start "momentum on BTC 1h" --ladder local
firm strategies show s-042
firm paper pnl --today
firm market watch BTC-USD AAPL
firm kill --confirm
```

**Notifications (owner only)**

- **Channels:** a private Telegram bot first; [Hermes Agent's gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/) for WhatsApp and others, using its direct webhook delivery so no LLM sits in the alert path. Prefer the official WhatsApp Cloud API route.
- **Urgent:** kill switch, drawdown limit, feed stale, venue disconnected, strategy demoted.
- **Actions (optional per strategy):** paper orders and fills with reason and verdict ID.
- **Digest:** after the US close and daily for crypto: P&L, drift, new strategies promoted to paper (with a veto link).
- **Two-way, read-only:** ask status and P&L from chat; the only command is the kill switch, with confirmation.

**Dashboard stack** (owner: no preference; must show charts and configurable panels):

- **Backend:** the control API in Python (FastAPI with uvicorn): REST for actions, one WebSocket for live updates; it reads the ledger, journal and stores, and sends owner commands as bus messages.
- **Frontend:** plain HTML and JavaScript modules, no build step; libraries vendored into the repo at pinned versions (no CDN, works on a private network).
- **Price charts:** TradingView Lightweight Charts (Apache-2.0, candlesticks, live updates, several panes). Its licence requires crediting TradingView: keep its attribution logo on and the NOTICE in the repo.
- **Other charts** (P&L curves, drawdown, trials vs budget, distributions): Apache ECharts (Apache-2.0).
- **Configurable panels:** a drag-and-resize grid (GridStack.js, MIT). Each panel is a type (price chart, positions, fills, P&L, risk use, feed health, researcher list, dossier view, activity log, alerts) with its own settings; layouts are saved server-side per named view (for example "Trading", "Research"), so they follow the owner to any browser.

**Access:** private network only (for example Tailscale), authenticated, never on the public internet. An MCP endpoint lets Claude Code read state while building; it is read-only by default.

## Research floor (experiment agents)

Autonomous researcher agents invent, test and optimise new strategies on the DGX; each researcher owns one strategy from idea to verdict, and a deterministic gate promotes verified strategies to paper trading by itself. The owner steers with research briefs and can pause, redirect, close or veto at any time; live trading stays a manual decision.

**A researcher's dossier.** One strategy per researcher: the hypothesis and why the edge should exist (written before any backtest), the code or model, every trial, results, and notes. Dossiers live in the private store, never in git.

**How a researcher works**

1. **Idea:** from a published method family, an owner brief, or (milestone 7) a scout note. Sources of inspiration are published methods: academic papers, books and public talks by firms such as Jane Street, AQR, Two Sigma and Man, and well-known families (momentum, mean reversion, pairs and stat arb, funding-rate carry, cross-exchange basis, volatility regimes, order flow). These give methods, not answers; firms do not publish their strategies, and well-known effects are often weaker today. Families the paper simulator cannot test honestly (market making, high frequency) are out of scope.
2. **Prototype:** rules, machine learning, or both, built on the data plane and the ari feature library; code written with local models and Certo.
3. **Research loop:** backtest, tune, change features, retrain, with walk-forward validation on the research view only. Every run is a trial in the global count. The loop ends when the dossier's budget is spent or the researcher submits.
4. **Submit:** the verifier runs its checks and opens the hidden out-of-sample period once. A failed version is closed; a changed strategy is a new candidate, and both count as trials.
5. **Promotion gate (deterministic):** a verified strategy goes to paper trading automatically if its correlation with strategies already in paper is below the limit and fewer than the maximum number of paper strategies are running. It gets a small paper allocation, behind the risk desk like every strategy. The owner is notified and can veto.
6. **Live:** never automatic; the owner decides (live readiness gate).

**What researchers can never do:** see the hidden out-of-sample data (the data plane refuses it); change risk limits, the promotion gate or their own budgets; place orders; push strategy code to git; send private data to a cloud model without the owner's per-run opt-in.

**Owner direction:** research briefs (market, timeframes, themes, data to use or avoid, strategies to stay uncorrelated with), assigned to one researcher or to the whole floor; pause, redirect, close or veto from the dashboard and the CLI.

**Parameters** (`config/research.yaml`, owner-editable, never agents): number of concurrent researchers (start: 2), per-dossier budgets (trials, wall-clock hours, tokens, GPU memory, cloud spend, zero by default), promotion gate (maximum paper strategies at once, maximum correlation with paper strategies, paper allocation per new strategy). The manager pauses or unloads researchers when the trading stack needs resources.

**Model routing:** local models by default; a frontier model only for work with no private data, or with the owner's per-run opt-in, logged with cost.

**Example:** `firm research start --brief briefs/crypto-carry.md --market crypto --tf 1h,1d`

## Certo on the DGX

Certo runs on the DGX next to the models, reached two ways: the experiment manager calls it directly for long runs, and `certo mcp` serves code-generation tasks (the ML desk's training code, features, strategies) to any MCP client.

| Caller | How | Use it for |
| --- | --- | --- |
| Experiment manager (on the DGX) | Certo's CLI or server mode, local | Overnight and long runs; runs survive without a client attached |
| Claude Code on the Mac | `certo mcp` over SSH stdio: `ssh dgx certo mcp --root /srv/firm/ml` | Delegating ML code tasks to local models and getting Certo's verdict back |
| Firm agents (on the DGX) | `certo mcp` over local stdio | An agent asking for code it then hands to the verifier |

**Rules**

- `certo mcp` is stdio with no authentication, so it is never exposed as a network port; remote use goes through SSH, which supplies the authentication.
- MCP runs live in memory and stop when the client closes, so anything longer than a session goes through the experiment manager instead.
- Certo only reaches folders under its `--root` and projects directory: give it the firm's ML workspace, never the risk config or the ledger.
- Claude Code itself runs on a cloud model, so a task sent from it is already outside the egress gate. Send it only generic code work; private strategy tasks start from the firm's local agents.
- Certo's verdict is about the code (tests and build pass); the firm's verifier still judges the strategy.

## Trade journal (data for improvement)

Every paper trade is recorded with everything known when it was decided, so the firm's own history becomes training and calibration data. The ledger stays the source of truth; the journal is a table rebuilt from it, stored in DuckDB and exportable to Parquet or CSV.

**One row per trade**

| Group | Fields |
| --- | --- |
| Identity | trade ID, strategy ID and version, experiment ID, verdict ID, risk decision ID |
| Market | market profile, instrument, venue or simulator, side |
| Timing | decision time, order time, fill times, exit time, holding period |
| Prices | intended price, fill prices, exit price, slippage vs intended (bps) |
| Costs | fees, spread paid, FX conversion |
| Result | gross and net P&L, return, maximum adverse and favourable excursion |
| Context at decision | feature snapshot (with feature-library version), judge outputs and probabilities, model versions |
| Outcome labels | hit target or stop, reason for exit, regime label afterwards |

**What it improves**

- **Simulator realism:** real slippage and fill rates recalibrate the cost model in each market profile.
- **Judge calibration:** d3's probabilities are checked against real outcomes, and the labels can later fine-tune it.
- **Drift detection:** paper results compared with the backtest, per strategy, feed demotions.
- **Post-mortems and new experiments:** agents read losing and winning trades to propose the next hypotheses.

**Rules:** the journal is tagged private (local models only by default); every export carries the ledger position it was built from, so any analysis can be reproduced; the journal is never edited by hand, only rebuilt.

**Access:** `firm journal export --from 2026-12-01 --format csv`, plus a journal page in the web UI with filters by strategy, market and date.

## Trading several markets at once

The firm trades all enabled markets at the same time: each market runs in its own asynchronous worker, while one risk desk sees the combined exposure across all of them.

- **One worker per market** (crypto, US shares, later others), each with its own event loop, data feeds, calendar and venue adapter. A crash or stale feed in one market halts that market only.
- **One shared risk desk** with limits per market and in total, in the base currency (GBP). Cross-market exposure, such as crypto and tech shares falling together, counts against the total.
- **One ledger writer** fed by a queue from all workers, so events stay in order and the hash chain stays intact.
- **Market-hours aware:** a US worker sleeps outside its session; crypto runs 24x7; the digest follows each market's close.
- **Kill switches:** per market and global.

**What the UI shows:** a markets panel (open, closed or 24x7, feed health), positions and P&L by market and consolidated in GBP, exposure by market and asset class, and each strategy tagged with the markets it trades.

## Opportunity scout

A scout agent watches the markets and the web for anything that looks like an opportunity, and turns what it finds into opportunity notes for the research desk. It never trades: a note becomes a hypothesis, then an experiment, then a verdict, like any other idea.

**What it watches**

- **News and filings:** official feeds and APIs first; each headline classified into typed events by d3 (earnings surprise, guidance change, listing, hack, regulation).
- **Time series:** downloads through the data plane, scanning for unusual moves, volume, volatility or correlation breaks.
- **Charts on the web:** a headless browser (Playwright) on the DGX captures chart pages; d3 or a local vision model classifies the still images. Where possible it draws the same chart from our own data instead, which is more reliable and point-in-time.

**Rules**

- **Web content is data, never instructions.** Pages, news and images can contain text aimed at AI agents; the scout reads them in a sandboxed container with no tools that can change anything, and outputs only typed notes.
- **Everything captured is stored with its capture time**, so a signal it finds can be backtested point-in-time later.
- **Respect each site's terms:** official APIs and feeds first, rate limits honoured, nothing behind paywalls or logins it isn't entitled to.
- **Budgets:** sources, frequency, browser sessions and tokens per day; notes arrive in the digest, urgent ones as alerts.

## Desks as services on a message bus

Each desk is its own service that talks to the others only through typed messages on a message bus, so any desk can later move to another machine on the LAN or in the cloud by changing configuration. Only some desks are AI agents; the others are plain programs.

| Desk | Service kind | Publishes | Subscribes to | Can move to |
| --- | --- | --- | --- | --- |
| Scout | AI agent | `notes.opportunity` | market data, news | LAN or cloud (public data only) |
| Research and experiments | AI agents + Certo | `strategy.candidate`, `trial.recorded` | notes, data | LAN; cloud only for non-private work |
| Verification | Deterministic | `verdict.issued` | `strategy.candidate` | LAN |
| Risk | Deterministic | `risk.decision`, `risk.halt`, `risk.heartbeat`, `alert.urgent.risk` | verdicts, positions, `order.proposed` | Stays with execution |
| Execution (per market) | Deterministic | `order.proposed`, `order.intent`, `order.update`, `fill.executed`, `alert.urgent.execution`, `alert.action.execution` | `risk.decision`, `risk.heartbeat` | Stays with risk |
| Ledger and journal | Deterministic | `digest.daily` | everything | LAN |
| Notifier | Deterministic | — | `alert.>`, `digest.>`; delivers to connectors (Telegram first) | Anywhere, including a small cloud server |
| Control API | Deterministic | `control.command` (owner commands, kill switch) | everything (read) | LAN |

Order flow: execution publishes a sized `OrderProposal`; risk answers with a `RiskDecision` naming that proposal; only then does execution build the `OrderIntent` (with both the verdict ID and the risk decision ID) and send it to the venue. Alerts are namespaced by the desk that raises them (`alert.<level>.<desk>`), so every subject has exactly one publisher.

**Rules for the bus**

- **Typed messages across desks.** Every message has a schema and version. Free-form conversation between agents is allowed only inside the research desk; what crosses a desk boundary is a typed object.
- **Permissions are the org chart.** Each service has its own credentials and may publish only its own subjects: no agent can publish `order.*`, `risk.*`, `fill.*`, verdicts or alerts. The golden rules are enforced by the bus as well as by code.
- **The order path stays together.** Risk, execution and the venue adapters run on the same machine, and execution halts by itself if it stops hearing risk's heartbeat. A slow network can delay research, never a stop order.
- **Placement follows the egress gate.** A service handling private data can move within the LAN; it moves to the cloud only if its data is public or the owner opts in.
- **Messages are durable and idempotent.** Each message has an ID; consumers can replay after a restart without acting twice.

**Bus choice:** NATS with JetStream (lightweight, runs on the DGX and the Mac, per-subject permissions, durable streams). v1 runs every service on the DGX, but already through the bus, so moving one later is a config change.

## Integration layer (ready for live, off in v1)

Every venue sits behind one adapter interface designed for real trading from day one, so going live later means writing and certifying an adapter, not changing the core. The paper simulator is simply the first adapter.

**The adapter interface covers what real venues need**

| Capability | Why it matters live |
| --- | --- |
| Order types: market, limit, stop, stop-limit; time in force | Real venues reject what they don't support; the adapter declares its capabilities |
| Client order IDs (idempotent submit) | A retry after a timeout must never create a second order |
| Order lifecycle: accepted, partial, filled, cancelled, rejected, expired | The ledger and risk desk need every state, not just fills |
| Reconciliation: positions, balances, open orders | On restart or disconnect, the venue is the truth; mismatches halt trading |
| Resting stops at the venue | Positions stay protected if the DGX goes offline |
| Rate limits and backoff | Exchanges ban clients that flood them |
| Instrument rules: tick size, lot size, minimum notional | Orders are rounded and checked before they leave |
| Mode: `paper`, `sandbox`, `live` | One switch per venue; `live` refused while `live_trading` is false |

**Conformance suite:** one shared test suite every adapter must pass, run against the paper simulator now and against venue sandboxes or testnets later. An adapter that doesn't pass can't be enabled.

**Credentials:** kept outside the repo (OS keychain or a secrets file the code can't print); trade-only API keys with withdrawals disabled and IP restrictions where the venue allows.

**Live readiness gate (all required before any real money):** conformance suite passes on the venue's sandbox; 3 months of paper results within the drift tolerance; reconciliation and kill switch tested end to end; per-strategy and total capital caps set; the owner flips `live_trading` and approves in the web UI.

## How the verifier decides

The verifier runs a strategy against an exact dataset version and returns **pass**, **fail** or **unverified**, never "pass" for something it could not check. First it checks the data: if the window still holds an implausible bar, nothing is judged, the verdict is unverified, and no trial is counted. Then:

1. **Lookahead truncation:** re-run with the data cut at many decision times; any changed past signal fails.
2. **Cost realism:** must survive the real costs of the venue it trades on (published taker fee, half the spread, slippage), and not lose money with costs doubled.
3. **Parameter sensitivity:** nudging each parameter up and down must not collapse the result.
4. **Trial count:** the Sharpe ratio is deflated for the global number of trials (Bailey and López de Prado, 2014).
5. **Hidden out-of-sample:** the last part of the window, opened only if checks 1–4 pass, once per strategy, instrument and timeframe. Each first opening counts as a trial, so trying a strategy on many instruments raises the bar.

The backtest decides at a bar's close and fills at the next bar's open. The report is deterministic and its hash is the verdict ID: the same dataset version, candidate and global trial count give the same bytes. All thresholds live in `config/verify/` and are placeholders.

## Firm rules (safety invariants)

These rules are enforced in code and each one gets an automated test; breaking one fails the build.

1. No LLM output can reach the execution desk except through a verdict and a risk approval.
2. Every order carries a verdict ID and a risk decision ID; an order without both is rejected.
3. Agents cannot read or write risk configuration, capital allocation, or the jurisdiction profile, and cannot change the verifier's thresholds.
4. The final out-of-sample period is opened once per strategy, instrument and timeframe, by the verifier only, after the in-sample checks pass; every opening is logged and counted as a trial. Re-verifying the identical candidate replays the sealed result; any other version in the same scope is refused.
5. Every trial an agent runs is counted, and the verifier corrects for the trial count.
6. The ledger is append-only; nothing is edited or deleted.
7. `live_trading` is false in v1; turning it on requires a config change plus a manual approval in the web UI.
8. Every model call passes a data-egress gate: tasks tagged private (strategy code, parameters, results, ledger) run locally unless the owner opts in for that run; every cloud call is logged with its tag and cost.
9. Training jobs can never take memory or CPU reserved for the trading stack.
10. Notifications go to the owner only; chat commands are read-only except the confirmed kill switch.

## Milestones

The first build target is milestone 1 on crypto: a data plane and a verifier that can say whether a hand-written strategy's backtest is trustworthy. Each milestone ends with a numeric exit check, in Certo's style.

1. **Data plane and verifier (crypto).** *Done Nov 16, 2026.* Ingest Kraken and Coinbase bars and trades; point-in-time queries; lookahead truncation, hidden out-of-sample, sensitivity, cost and trial-count checks.
   - Exit: a strategy with planted lookahead is rejected; a clean moving-average strategy passes; results reproduce byte for byte from a dataset version (with the same candidate and global trial count).
2. **Paper simulator, risk desk, ledger, trade journal.** Fills against live data with fees, spread, slippage and partial fills; limits and kill switch; append-only ledger; trade data for fills (live feeds plus recent trades). The trial registry and out-of-sample log move into the ledger. Before the unattended run: the trade journal, a minimal notifier (urgent alerts to the owner on Telegram), and the bus, so the run is observed and the Linux box and the DGX already talk as services.
   - Exit: a verified strategy paper-trades unattended for at least 7 closed bars of its timeframe (for example 7 hours on 1h bars), deciding on every bar; every fill traces to a verdict ID; an urgent alert raised during the run reaches the owner within 60 seconds.
3. **Research agents on Certo.** The experiment manager runs budgeted searches on the DGX; research and ML desks propose and train strategies; the verifier gates them. Prerequisites: trial Sharpes on one scale in the trial-count check; agent-written strategies stored in the private store, never in git (only hand-written examples are public); the data-egress gate enforced in code, not only configured; agent strategy code run in a sandbox. Design agreed with the owner Nov 17, 2026 (see "Research floor"). Delivered with **dashboard v1**: one web page for the research floor (inspect, start, pause, redirect, close researchers) and live paper trading (positions, fills, P&L, risk use, updated in real time), on a minimal control API.
   - Exit: agents produce at least one strategy that passes verification without human code changes; every trial they ran is in the global count; no private data left the machines without a logged opt-in.
4. **Control and notifications.** Control API, CLI, first web UI pages, Telegram bot.
   - Exit: urgent alerts arrive within 60 seconds; the daily digest lands after the close.
5. **Judge.** Local decision model added as features; measured against the same strategies without it.
   - Exit: keep it only if verified paper results improve.
6. **US shares.** Second market profile: XNYS calendar, IBKR data, EODHD universe with delistings, FX costs.
   - Exit: milestone 1 and 2 checks pass on US shares.
7. **Opportunity scout.** Sandboxed agent over news, time series and chart pages; outputs only typed opportunity notes that become research hypotheses (see "Opportunity scout"). Can start any time after milestone 3, since its notes feed the research desk.
   - Exit: a note captured with its time becomes an experiment that the verifier judges point-in-time; nothing the scout reads can change state.

**How it gets built:** each milestone is written as task files for Certo and Claude Code, with tests as the checks. Agents build the harness; the harness's own verifier and tests decide when a milestone is done.

## Open questions

- [ ] Judge model: d3 on Ollama chosen; pick d3:9b or d3:27b by testing on our own labelled market cases.
- [x] Verifier thresholds: the method is decided (see "How the verifier decides"); the current numbers stay as v1 defaults until there are more real verifications to calibrate them on.
- [x] Out-of-sample scope: once per strategy, instrument and timeframe, each opening counted as a trial.
- [ ] Paper-to-live drift tolerance that triggers a demotion.
- [x] Name of the system and its CLI: "the firm", CLI `firm`.
- [x] The harness lives in its own (public) repo and uses Certo.
- [x] Web UI stack (decided Nov 17, 2026): see "Dashboard stack" under "Control and notifications".

* [ ] Indicator code reviewed (ari\_signals, timeseries\_info): code moves into the firm repo and gets fixed there (lookback skew, unfinished candle, weekly mode, pivot confirmation, as-of time); golden baseline first, then archive the old repo. The baseline is frozen; the port adapts to current library versions.

## Planned additions (owner requests, Nov 2026)

Tracked in `TODO.md`; listed here because they widen what v1 is. Technical-analysis documents and "Ask the firm" are parked until after milestone 3 (owner, Nov 17, 2026), so the research side is built first.

- **Incremental data updates:** downloading a series again extends the store (only new bars, gap detection, dataset lineage) instead of starting over.
- **Strategies judged on real data:** synthetic data is for tests only; every verdict states what data it rests on, and a verdict on synthetic data can never promote a strategy.
- **Technical-analysis documents on demand:** from any dataset version, through the indicator wrapper, versioned and point-in-time; optional narrative by a local model, never an input to the order path.
- **Ask the firm:** a local model answers questions about our data and reports through typed, read-only tools (full-text search for documents; embeddings only if needed), citing its sources. Served through the read-only MCP endpoint and `firm ask`.

## Sources

- [Interactive Brokers review (UK)](https://global-best-ai-brokers-uk.pages.dev/interactive-brokers-review)
- [Coinbase and Kraken FCA status](https://www.bitget.cloud/academy/coinbase-fca-regulat)
- [Historical crypto data APIs and pricing (Coinranking)](https://coinranking.com/blog/best-historical-crypto-data-apis/)
- [EODHD delisted stock data](https://eodhd.com/financial-apis/delisted-stock-companies-data-2)
- [Norgate Data](https://norgatedata.com/)
- [DGX Spark review 2026 (VerdictBits)](https://verdictbits.com/reviews/nvidia-dgx-spark-2026/)
- [Inference speed on DGX Spark (Cline)](https://cline.bot/blog/what-a-sigkill-race-reveals-about-inference-speed)
- [Hermes Agent messaging gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/)
