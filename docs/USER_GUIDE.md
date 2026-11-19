# The Firm: user guide

How to install the firm, try it safely, verify a strategy, paper-trade it,
and find answers later. Every command and option is in the
[command reference](COMMANDS.md); this guide explains what they are for and
how they fit together, and links there instead of repeating the details.

> **Not financial advice. Paper money only.** This is a personal research
> and engineering project. It gives no signals or advice to anyone and does
> not trade real money: `live_trading` is false in version 1, the config
> loader refuses `live_trading: true`, and the only venue adapter is the
> paper simulator. No command in this repository places a real-money order.
> A strategy the verifier passes can still lose money on future data. Use
> at your own risk.

## Contents

1. [What the firm is](#1-what-the-firm-is)
2. [Implemented now vs planned](#2-implemented-now-vs-planned)
3. [Ideas you'll meet](#3-ideas-youll-meet)
4. [Install](#4-install)
5. [First run: a synthetic demo store](#5-first-run-a-synthetic-demo-store)
6. [Real market data](#6-real-market-data)
7. [Writing a strategy](#7-writing-a-strategy)
8. [Verifying a strategy](#8-verifying-a-strategy)
9. [Paper trading](#9-paper-trading)
10. [Ledger and trade journal](#10-ledger-and-trade-journal)
11. [Configuration and environment](#11-configuration-and-environment)
12. [Running unattended on Linux](#12-running-unattended-on-linux)
13. [Troubleshooting](#13-troubleshooting)
14. [Where to read more](#14-where-to-read-more)

---

## 1. What the firm is

A one-person trading firm, run by one owner on their own machines. Its
principle is **agents propose, code decides**: a strategy, whoever wrote it,
counts only once a deterministic verifier says its backtest is trustworthy,
and every order passes a deterministic risk desk before it reaches a venue.

What you can do with it today:

- download public market data into a local, versioned, point-in-time store;
- verify a strategy (a small Python class) against an exact dataset
  version, and get **pass**, **fail** or **unverified** with a report;
- replay a verified strategy's history through the paper-trading path;
- paper-trade one verified crypto strategy against live public market data,
  unattended, with urgent alerts on Telegram;
- inspect everything afterwards in an append-only, hash-chained ledger and
  a trade journal rebuilt from it.

It is for someone comfortable with a terminal, Python and git who wants to
test trading ideas honestly. It is not a trading bot to point at a brokerage
account, and it is not a signal service.

## 2. Implemented now vs planned

The design is in [SPEC.md](SPEC.md); not all of it is built. Status is
tracked in [TODO.md](../TODO.md). As of this guide:

| Area | Status |
| --- | --- |
| Data store: downloads from Kraken, Coinbase, Binance, Yahoo; normalisation; dataset versions; data-quality quarantine; instrument records | **implemented** |
| Strategy verifier: data check, lookahead truncation, costs, sensitivity, trial count, hidden out-of-sample | **implemented** (thresholds are placeholders) |
| Paper simulator, risk desk, execution, deployment gate | **implemented**, one strategy on one market per run |
| Live crypto feeds (Kraken, Coinbase, Binance streams) | **implemented** |
| Ledger (append-only, hash-chained) and trade journal (build, show, export) | **implemented** |
| Message bus between desks (NATS JetStream, one login per desk) | **implemented**; desks run in one process, over the bus |
| Telegram alerts to the owner (urgent alerts, start/stop) | **implemented**; no chat commands |
| Sandbox for agent-written strategy code; private strategy store | **implemented** (used by `firm verify`, `firm paper run` and `firm paper replay`) |
| Model gateway with the data-egress gate | **implemented** as a library; no command calls a model yet |
| Research backtests counted as trials (run by the verification desk, research view only) | **implemented** as a library, for the research floor; no command yet |
| Unattended Linux service for `firm paper run` (systemd user unit) | **implemented**; a unit for `firm bus up` is still to do |
| Research floor: dossiers, budgets, the research manager, `firm research` commands (start, list, show, pause, resume, redirect, close, floor brief) | **implemented**; nothing runs a researcher yet |
| Research agents (the researcher loop on Certo and local models), methods library, promotion gate | **planned** (milestone 3, in progress) |
| Dashboard (`firm dashboard serve`, [DASHBOARD.md](DASHBOARD.md)): research floor with every steering command, paper run (account, P&L, price chart with fills, feeds, risk), ledger, command reference; live updates; arrangeable panels; token login; loopback only | **implemented** |
| Kill switch per market and global (`firm kill` / `firm resume`, and the dashboard): through the bus to the risk desk, confirmed, survives a restart | **implemented** |
| Kill switch from the dashboard, MCP endpoint, remote access over a private network | **planned** |
| Judge (local decision model), opportunity scout | **planned** |
| US shares paper trading (IBKR), EODHD data | **planned**; a `us_equities` profile exists and Yahoo daily bars can be downloaded, but there is no shares paper path |
| Live trading with real money | **not in v1**; refused by the config loader |

If something in the SPEC isn't in this table as implemented, assume it
doesn't exist yet.

## 3. Ideas you'll meet

- **Data store** (`$FIRM_DATA_ROOT`, default `./data`): your local market
  data. Raw downloads exactly as received, normalised Parquet, frozen
  dataset versions, instrument records. Git-ignored. Written once, never
  edited: a correction is a new capture.
- **Dataset version** (`ds-` followed by 12 hex digits): a frozen,
  content-addressed list of files and checksums. Verdicts cite one, so a
  verification can be reproduced byte for byte.
- **Point-in-time**: every read takes an `as_of` time and returns only what
  was known then. A bar is known at its close; a forming bar never enters
  the store.
- **Strategy**: a Python class that returns a target exposure (0 to 1) per
  bar. See [section 7](#7-writing-a-strategy).
- **Verdict** (`v-` followed by 16 hex digits): the verifier's result. The
  report's hash is its ID, so a changed report no longer matches its ID.
- **Trial**: one strategy run that could have influenced what you picked.
  All trials are counted globally, and the verifier makes the bar higher
  the more were tried.
- **Hidden out-of-sample (OOS)**: the last 25% of the verification window,
  opened once per strategy, instrument and timeframe, and only after the
  other checks pass.
- **State** (`$FIRM_STATE_ROOT`, default `./state`): the verifier's trials,
  OOS log and reports, the ledger, the journal, bus logins and logs.
  Git-ignored and private.
- **Ledger**: the append-only, hash-chained record of every trial, verdict,
  proposal, risk decision, order, fill, alert and status change.
- **Desks and the bus**: risk, execution and the notifier are separate
  services that talk over NATS with typed messages; each may publish only
  its own subjects, and the server enforces it.
- **Deployment gate**: what `firm paper run` checks before trading: a
  passing, intact report, on real data, for exactly this code, these
  parameters, venue, instrument and timeframe.

## 4. Install

### Prerequisites

| Need | Why | How |
| --- | --- | --- |
| macOS or Linux (CI tests both on arm64) | | |
| Python 3.12 or newer | the firm | `uv` can install it for you |
| [uv](https://docs.astral.sh/uv/) | environment and dependencies | see its install page |
| git | the checkout | |
| `nats-server` 2.10+ with JetStream | the bus: `firm bus up`, and `firm paper run` unless `--no-bus` | macOS: `brew install nats-server`; Linux: a release binary from github.com/nats-io/nats-server/releases on the `PATH` |
| A Telegram bot (optional) | urgent alerts on your phone | [section 9](#alerts) |
| An OS sandbox (only for agent-written strategies) | runs untrusted strategy code | macOS: `sandbox-exec`, built in; Linux: `bubblewrap` (`sudo apt install bubblewrap`) |

You don't need a GPU, API keys or an exchange account. The tests need none
of the above beyond Python and uv: they use an in-memory bus, synthetic
data and no network.

### Steps

```bash
git clone https://github.com/alessioricco/TheFirm.git
cd TheFirm
uv sync --extra dev                      # creates .venv with the firm and the dev tools
git config core.hooksPath .githooks      # the pre-commit guard (once per clone)
uv run firm --version
uv run pytest -q                         # optional: the whole suite, no network
nats-server --version                    # only if you'll paper-trade
```

Run every command from the repository folder through `uv run`; you never
need to activate the virtual environment.

## 5. First run: a synthetic demo store

Start with synthetic data in a **separate** store, so nothing mixes with
real data later. No network, no keys.

```bash
export FIRM_DATA_ROOT=./data-demo FIRM_STATE_ROOT=./data-demo/state

uv run firm data synthetic                      # synthetic Kraken XBTUSD daily bars
uv run firm data build-dataset --bars kraken:XBTUSD:1d
#   -> ds-xxxxxxxxxxxx  (6 files, 2000 rows)

uv run firm verify --dataset ds-xxxxxxxxxxxx --venue kraken --instrument BTC-USD \
    --tf 1d --market crypto \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover
#   -> v-xxxxxxxxxxxxxxxx  PASS, one line per check, then the report path

uv run firm verify --dataset ds-xxxxxxxxxxxx --venue kraken --instrument BTC-USD \
    --tf 1d --market crypto \
    --strategy examples/strategies/lookahead_ma.py:LookaheadCrossover
#   -> FAIL: "signals change when future data is removed"; OOS kept closed
```

Use the dataset version your `build-dataset` printed. The second strategy
peeks at the future on purpose, and the verifier catches it. Then look
around:

```bash
uv run firm state show          # trials counted, OOS openings, reports
uv run firm ledger show         # what was recorded
uv run firm ledger verify       # "OK: hash chain intact over N events"
```

What you **can't** do with synthetic data is paper-trade or replay: the
deployment gate refuses a verdict whose dataset isn't all real downloads
(`refused: dataset ... is not all real data`, exit 4). That is deliberate:
a strategy proven only on made-up prices must never reach a paper account.

When you're done, `unset FIRM_DATA_ROOT FIRM_STATE_ROOT` (or open a new
terminal) before working with real data, and delete `./data-demo` if you
like. Both `data-*/` and `state/` are git-ignored.

## 6. Real market data

### Where it comes from

`scripts/download_data.py` fetches **public** market data, with no API
keys, into your data store. You run it yourself; tests never touch the
network (a socket guard fails any test that tries).

| Provider | What | Caveats |
| --- | --- | --- |
| `coinbase` | spot bars and recent trades | candles back to 2015 for BTC-USD; 300 per request |
| `kraken` | spot bars and recent trades | **only the last 720 bars per timeframe**; older history needs trades |
| `binance` | spot bars and recent trades | lab only: not a permitted real-money venue in the UK profile |
| `yahoo` | shares and indices, bars | unofficial endpoint, personal use; prices stored **un-split-adjusted**; intraday history is short |

`uv run python scripts/download_data.py --providers` lists them with their
timeframes. Check each provider's terms; the data is for your own use. A
source being usable for research says nothing about trading there with
real money (that's the jurisdiction profile, and it's off in v1).

### Download, then freeze a dataset version

```bash
uv run python scripts/download_data.py --provider coinbase --symbol BTC-USD \
    --instrument BTC-USD --tf 1d --since 2017-01-01 --listed-at 2015-01-26
uv run firm data build-dataset --bars coinbase:BTC-USD:1d
#   -> ds-xxxxxxxxxxxx
```

- `--symbol` is how the **provider** spells it (`XBTUSD` on Kraken,
  `BTC-USD` on Coinbase, `BTCUSDT` on Binance); `--instrument` is the
  **firm's** ID (`BTC-USD`, or `NVDA.XNAS` for a share). `--listed-at` is
  needed the first time an instrument is recorded.
- `--dry-run` prints the requests and downloads nothing.
- Downloading again adds new captures; it never edits old ones.
- `build-dataset` normalises the captures and freezes a version. Repeat
  `--bars` for several series, add `--trades` for trade series, and
  `--derive 1d:1w` to build coarser bars on one canonical grid (days at
  00:00 UTC, weeks on Monday). Exact syntax: [COMMANDS.md, firm data](COMMANDS.md#firm-data).

### Implausible bars

Venues sometimes publish corrupt candles (a bar opening near zero). Raw
captures keep them exactly as received; `build-dataset` leaves out any bar
priced more than `max_price_ratio` times away from the last accepted close
(`config/data/quality.yaml`) and lists it in the dataset manifest. If a
verification window still contains one, the verifier judges nothing: the
verdict is **unverified** and no trial is counted. Fix the data and build a
new version; data is never interpolated.

### Stream symbols

A live stream may spell an instrument differently from the REST API.
`firm paper run` looks the stream's spelling up in the instrument record
and refuses to start without it. Record it once:

```bash
uv run firm data add-symbol --venue coinbase --instrument BTC-USD \
    --provider coinbase_ws --symbol BTC-USD
```

`uv run firm feed watch --provider coinbase_ws --symbol BTC-USD --seconds 60`
shows a stream's trades and bars before you rely on it (needs network).

### Looking after the store

- Never commit it (the repo is public; `scripts/check_repo.py` refuses
  data files, and `.gitignore` excludes the store).
- Back it up yourself; it isn't in git. It only ever grows, so a plain
  `rsync -a` copy works, and `rsync -a --ignore-existing` merges new
  captures from another machine.
- Never mix synthetic and real data in one store.
- Layout and naming: ARCHITECTURE.md, "Data store".

## 7. Writing a strategy

A strategy is a class in a Python file with three things:

```python
from typing import ClassVar

import pandas as pd


class MyStrategy:
    name = "my_strategy"  # stable ID
    default_params: ClassVar[dict[str, int]] = {"fast": 10, "slow": 40}

    def signals(self, bars: pd.DataFrame, params) -> pd.Series:
        close = bars["close"]
        fast = close.rolling(int(params["fast"])).mean()
        slow = close.rolling(int(params["slow"])).mean()
        return (fast > slow).astype(float).where(slow.notna())
```

- `bars` has the columns `open_time`, `close_time`, `open`, `high`, `low`,
  `close`, `volume`, one row per closed bar, oldest first.
- `signals` returns one value per bar: the **target exposure** in [0, 1]
  (spot only in v1: no leverage, no shorting), or NaN while warming up
  (treated as flat).
- The value at row *i* is decided at the close of bar *i* and may use rows
  up to *i* only. The backtest fills at the next bar's open. Don't trust
  yourself on this: the lookahead check tests it.
- `default_params` values are int, float, str or bool. The sensitivity
  check nudges every numeric one by ±10% and ±20%.

Working examples: `examples/strategies/ma_crossover.py` (clean) and
`examples/strategies/lookahead_ma.py` (planted lookahead).

**Where to keep your files.** The repository is public, and
`scripts/check_repo.py` refuses strategy code (any `def signals(` in a
`.py` or `.ipynb` file) outside `examples/strategies/`, `src/` and
`tests/`. Keep your own strategies outside the repository and pass their
path to `--strategy`. Strategies written by agents live in the private
store, `<data root>/research/strategies/<dossier>/`; those always run in
the [sandbox](#sandboxed-strategies).

## 8. Verifying a strategy

```bash
uv run firm verify \
    --strategy path/to/my_strategy.py:MyStrategy \
    --dataset ds-xxxxxxxxxxxx --venue coinbase --instrument BTC-USD \
    --tf 1d --market crypto
```

All options (`--params`, `--as-of`, `--report`, `--verifier`, ...):
[COMMANDS.md, firm verify](COMMANDS.md#firm-verify).

### The checks

In order; thresholds in `config/verify/default.yaml` (placeholders):

1. **Data quality**: an implausible bar in the window stops everything
   (unverified, no trial).
2. **Lookahead truncation**: re-runs the strategy with the data cut at 250
   decision times; any past signal that changes is a fail.
3. **Cost realism**: must clear a minimum net Sharpe after the venue's
   published taker fee, half the spread and slippage, and not lose money
   with costs doubled.
4. **Parameter sensitivity**: each nudged parameter must keep at least half
   the base Sharpe.
5. **Trial count**: the Sharpe is deflated for the global number of trials
   (Bailey and López de Prado, 2014); every trial behind the candidate must
   be recorded.
6. **Hidden out-of-sample**: the last 25% of the window, opened only if
   checks 1–5 pass, once per strategy, instrument and timeframe. Each first
   opening counts as a trial.

### Outcomes and exit codes

| Outcome | Exit | Meaning |
| --- | --- | --- |
| PASS | 0 | every check ran and passed |
| FAIL | 1 | at least one check failed |
| UNVERIFIED | 2 | something couldn't be judged (bad data, too few bars, the sandbox failed): never treat it as a pass |
| error | 3 | bad input, missing file, config problem |

### The report

`<state>/reports/<verdict>.json`, deterministic: the same dataset version,
candidate and global trial count give the same bytes. It cites the code's
SHA-256 (not its text), the parameters, the dataset version, the window,
every check with its metrics, and the costs used.

### Trials and the hidden period: what to know

- **Every verification is a trial.** Trying many variants raises the bar for
  all of them, by design. The same code, parameters and bars again is the
  same trial (no inflation from re-running).
- **The OOS period opens once.** Re-verifying the *identical* candidate
  replays its sealed result. A *changed* strategy, or changed parameters, in
  the same scope fails with "out-of-sample already opened for another
  version": there is no second look. Use `--as-of` to re-verify over the
  window an earlier opening used, after new bars have arrived.
- **State is the memory.** Trials and OOS openings live in your state
  folder. A fresh, empty state forgets them, and its verdicts are kinder
  than they should be. Use one state for all your research.

### What a PASS means, and what it doesn't

A PASS says the backtest is **trustworthy as a backtest**: no lookahead
found, profitable after realistic costs, not a knife-edge parameter choice,
still good after correcting for how many things you tried, and positive on
data you never saw while choosing it. It is the entry ticket to paper
trading.

It does **not** say the strategy will make money. Markets change; the
thresholds are placeholders; the cost model is an estimate; the hidden
period is one sample. Paper trading is the next test, and live trading is
not part of v1.

### Sandboxed strategies

Code in the private store (`<data root>/research/`) always runs in a
separate process under the OS sandbox: no network, no access to your files
or keys, CPU, memory and time limits. `--sandbox` does the same for any
other file. The verdict is the same as running the file directly. If no OS
sandbox is available (for example inside another sandbox on macOS), or the
sandbox fails, the result is **unverified** (exit 2), never pass or fail.

## 9. Paper trading

### Before you start

- A **PASS** verdict on **real** data, for exactly the venue, instrument and
  timeframe you'll trade.
- The **stream symbol** on record (`firm data add-symbol`, see
  [section 6](#stream-symbols)) or `--stream-symbol`.
- Network access to the venue's public stream (and to Kraken's, for the
  GBP exchange rate the risk desk uses).
- `nats-server` installed, unless you use `--no-bus`.

### Replay first (optional, minutes)

```bash
uv run firm paper replay \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --verdict v-xxxxxxxxxxxxxxxx --market crypto --venue coinbase \
    --instrument BTC-USD --tf 1d
```

Runs the verified history (from the hidden period, by default) through the
real paper path (risk desk, paper venue, ledger) in a scratch folder, and
checks its signals match the backtest's. Exit 1 if they disagree. It only
reads your real ledger.

### The deployment gate

`firm paper run` and `firm paper replay` refuse (exit 4) unless:

- the report exists and still hashes to its verdict ID;
- the verdict is a PASS;
- the strategy file is byte-for-byte the verified one;
- the parameters equal the verified ones;
- venue, instrument and timeframe match the verified ones;
- the dataset is entirely real downloads (no synthetic captures).

A refusal is final for that input: a supervisor must not retry it.

### Run it

```bash
uv run firm bus up                  # terminal 1: the bus, in the foreground
uv run firm paper run \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --verdict v-xxxxxxxxxxxxxxxx \
    --market crypto --venue coinbase --instrument BTC-USD --tf 1d   # terminal 2
tail -f state/logs/paper-crypto-coinbase.log                        # terminal 3
```

On a Mac, prefix the long-running commands with `caffeinate -is` so the
machine doesn't sleep (on mains power; closing the lid can still sleep it).

What happens: the gate checks the verdict; the first run opens a paper
account (capital from `config/risk/default.yaml`) and records it in the
ledger; the run warms up on the venue's closed bars, then decides at every
bar close (00:00 UTC for daily bars). Every order goes execution → risk →
paper venue over the bus; the risk desk can approve, resize down or reject.

- **Bus or no bus.** By default the desks talk through NATS and the run
  refuses to start (exit 1) if `firm bus up` isn't running. `--no-bus` runs
  the same desks calling each other in one process: fine for a quick try.
- **Duration.** Until Ctrl-C, or `--hours N`.

### Monitoring

The easiest way to watch a run is the **dashboard**: `uv run firm dashboard
serve`, then open the link `uv run firm dashboard url` prints. It shows the
run, the account, P&L, a price chart with the fills, the feeds and risk,
live. Everything about turning it on and using it is in
[DASHBOARD.md](DASHBOARD.md). From the terminal:

```bash
uv run firm paper status                         # accounts, runs, feeds, halts, P&L (from the ledger)
tail -f state/logs/paper-crypto-coinbase.log     # decisions + a status line every minute
uv run firm bus status                           # the stream and each desk's consumer
uv run firm ledger show --tail 20                # the latest recorded events
uv run firm journal build && uv run firm journal show   # trades so far
```

### Alerts

Alerts go to **one** Telegram chat, the owner's; the bot reads no commands.

1. Create a bot with Telegram's BotFather and put `TELEGRAM_BOT_TOKEN=...`
   in `.env`.
2. Send your bot any message, then `uv run firm notify chat-id` and put
   `TELEGRAM_OWNER_CHAT_ID=<id>` in `.env`.
3. `uv run firm notify test` sends a test alert and prints how long it took.

During a run you get **urgent** alerts for a stale or disconnected feed (no
new orders on that market until it's back), a risk halt (daily loss limit,
drawdown kill switch), a crashed market worker and a lost risk heartbeat
(no new orders until it's back), plus "started" / "stopped". The same alert
repeats at most every 5 minutes (`config/notify.yaml`), and you hear when
it recovers. Without the Telegram settings the run still works and prints
`notifications: OFF (...)`; `--no-notify` turns them off on purpose. Every
delivery attempt is a NOTIFICATION event in the ledger.

### Stopping and restarting

- **Stop**: Ctrl-C (or SIGTERM) in the paper terminal, then in the bus
  terminal. Feeds close and "stopped" is recorded.
- **Kill switch**: to stop new buying without stopping the run,
  `uv run firm kill --market crypto --reason "..." --confirm` (or `--all`),
  or **Kill** in the dashboard's Trading → Risk panel. The risk desk
  confirms it, you get an alert, and it stays on across restarts until
  `uv run firm resume --market crypto --reason "..." --confirm`. Sells that
  reduce a position still go through. See [COMMANDS.md](COMMANDS.md#firm-kill).
- **Stopping the bus mid-run is safe**: the run reports "risk heartbeat
  lost: no new orders" and "back" when the bus returns.
- **Restart** carries on from the ledger: the paper account and balances
  are rebuilt and no bar is decided twice.
- **Known limitations** ([TODO.md](../TODO.md), "Paper-run follow-ups"):
  a bar that closes while the run is down is **skipped**, not decided late
  (a daily strategy down across 00:00 UTC misses that day); orders still
  open when the process stops are lost (the simulator is in memory); the
  average entry price isn't rebuilt on restart; the kill switch stops new
  buying but doesn't stop the process (Ctrl-C or `systemctl` does).
- If the strategy itself raises an error (or its sandbox dies), that
  market stops: no further orders, and an urgent alert.

## 10. Ledger and trade journal

The **ledger** (`<state>/ledger.sqlite`) is the source of truth: every
trial, OOS event, verdict, order proposal, risk decision, order, fill,
feed and market status change, notification and owner command, each with a
reason. It is append-only and hash-chained; nothing is edited or deleted.

```bash
uv run firm ledger show --tail 50     # latest events, one line each
uv run firm ledger verify             # exit 1 if the hash chain is broken
uv run firm ledger import-state       # record pre-ledger verifier state (idempotent)
```

Each machine keeps its own ledger, written by the process that holds the
desks: the research ledger where you verify, the paper ledger where the
paper run trades. Don't copy one machine's ledger onto another.

The **trade journal** (`<state>/journal.duckdb`) has one row per paper trade
(flat → long → flat): identity, verdict and risk decisions, timing, prices,
slippage, fees and P&L. What the ledger can't tell yet (spread paid, maximum
adverse and favourable excursion, feature snapshots) is left unknown, never
zero. It is never edited, only rebuilt from the ledger, and every row
records the ledger position it was built from.

```bash
uv run firm journal build
uv run firm journal show --tail 10
uv run firm journal export --from 2026-12-01 --to 2027-01-01 --format csv \
    --out ../exports/trades.csv
```

Exports are private: write them outside the repository.

Where everything is written:
[COMMANDS.md, "Where things are written"](COMMANDS.md#where-things-are-written).

## 11. Configuration and environment

### Profiles (`config/`)

| File | Holds |
| --- | --- |
| `jurisdictions/uk.yaml` | base currency (GBP), venues permitted for real money with their regulatory status, leverage cap, `live_trading: false` |
| `markets/crypto.yaml`, `markets/us_equities.yaml` | calendar, data sources, live streams, staleness limit, costs (fee schedule per venue, spread, slippage, FX), paper execution settings |
| `fees/*.yaml` | each venue's published maker/taker fees, with source and date |
| `risk/default.yaml` | paper capital, firm-wide and per-market limits, kill switches |
| `verify/default.yaml` | verifier thresholds |
| `data/quality.yaml` | the implausible-bar rule |
| `bus.yaml` | bus host (loopback), port, stream, retention, heartbeat and timeouts |
| `notify.yaml` | alert throttle, retries, start/stop announcements |
| `models.yaml` | model per role and the egress policy (cloud budget £0) |
| `research.yaml` | the research floor (being built): researchers at once, per-dossier budgets, the promotion gate. Agents can't read it |

Validation is strict: an unknown key, a missing value or an inconsistency
(a venue without a fee schedule, a market without risk limits) is an error,
and `live_trading: true` is refused. Commands only read the config;
`--config` points them at another folder. Change a profile in its file,
never in code. The numbers are placeholders still to calibrate.

### Environment variables

| Variable | Default | Used for |
| --- | --- | --- |
| `FIRM_DATA_ROOT` | `./data` | the data store (`--root` overrides) |
| `FIRM_STATE_ROOT` | `./state` | state and ledger (`--state-root` overrides) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_CHAT_ID` | unset | alerts |
| `FIRM_DOTENV` | `1` | `0` stops the CLI from reading `.env` |

Copy `.env.example` to `.env` (git-ignored) for these. The CLI reads `.env`
from the current folder at start-up and only fills variables that aren't
already set, so the shell or a service's environment file wins. It never
prints a secret. `.env.example` also lists settings for later milestones
(model host, bus URL, share data key); the current commands don't read them.

### Secrets and what never to commit

The repository is public. Never commit `.env`, the data store, the state
folder, journal exports, your own strategies, real dataset or verdict IDs,
or results. The pre-commit hook (`git config core.hooksPath .githooks`) and
CI run `scripts/check_repo.py`, which refuses data files, large files,
secret-looking strings, machine-specific paths, real dataset and verdict
IDs, and strategy code outside the allowed folders. Run it yourself any
time:

```bash
python3 scripts/check_repo.py --all
```

Bus logins are generated into `<state>/bus/credentials.json` (mode 600) and
never printed.

## 12. Running unattended on Linux

For a long paper run on an always-on Linux machine, `firm paper run` runs as
a systemd **user** service that restarts after a crash or reboot. Outline:

1. Clone, `uv sync --frozen`, run the tests on that machine.
2. Copy the data store (the verdict's dataset, the raw sidecars,
   `reference/`); start an **empty** state folder holding only the
   verdict's report.
3. Install `nats-server`; start `firm bus up` (by hand for now) or use
   `--no-bus`.
4. `scripts/install_paper_service.sh <name>` and fill in
   `~/.config/firm/paper-<name>.conf` with exactly what was verified.
5. Try it in the foreground, then
   `systemctl --user enable --now firm-paper@<name>`.

A gate refusal exits 4 and is not retried; other failures restart after 60
seconds. The full runbook is [deploy/README.md](../deploy/README.md).

## 13. Troubleshooting

| Symptom | Cause | What to do |
| --- | --- | --- |
| `externally-managed-environment` when installing | trying to install into the system Python | use `uv sync`; never `pip install` into the system |
| `nats-server: command not found`, or `firm paper run` says the bus isn't running (exit 1) | NATS not installed, or `firm bus up` not started | install it ([section 4](#prerequisites)); start `firm bus up` in another terminal; or add `--no-bus` |
| `firm bus up` refuses to start: port in use | another `nats-server` (or an old `bus up`) holds the port | stop it, or change `port` in `config/bus.yaml` |
| `firm bus status`: `no bus credentials ...: run firm bus up once` | the bus has never run with this state folder | run `firm bus up` once |
| `error: config/<file>.yaml: 1 validation error ...` (exit 3) | a typo, unknown key or missing value in a profile | the message names the file and the key; validation is strict on purpose |
| `error: unknown dataset version ...` / `error: ...: no bars for ...` (exit 3) | wrong `--dataset` or data root, or a venue / instrument / timeframe that dataset doesn't hold | use the version and firm IDs `build-dataset` printed (e.g. venue `kraken`, instrument `BTC-USD`), with the same `FIRM_DATA_ROOT` |
| `--strategy must be PATH:CLASS` | missing `:ClassName` | e.g. `examples/strategies/ma_crossover.py:MovingAverageCrossover` |
| verdict UNVERIFIED: implausible bars | a corrupt candle in the window | rebuild the dataset (the quality rule quarantines it); never edit data |
| verdict UNVERIFIED: too few in-sample bars | less history than `min_bars` | download more history |
| OOS FAILED: "already opened for another version" | another version of this strategy already opened the hidden period for this instrument and timeframe | by design: there is no second look. Only the identical candidate replays its sealed result; another instrument or timeframe is a separate opening, counted as a trial |
| verdict UNVERIFIED: "sandbox failed" / "no OS sandbox available" | private-store code with no working OS sandbox, e.g. run from inside another sandbox on macOS, or no bubblewrap on Linux | run from your own terminal; on Linux install `bubblewrap` and check `bwrap --unshare-all --ro-bind / / true` works |
| `refused: ...` and exit 4 from `paper run` / `replay` | the deployment gate | read the reason: synthetic data, a changed file, different parameters, venue or timeframe, or a report that isn't a PASS |
| `paper run`: `error: no coinbase_ws symbol for BTC-USD ...` (exit 3) | the instrument record doesn't name the live stream's spelling | the message prints the `firm data add-symbol` command to run; or pass `--stream-symbol` |
| `firm kill` exits 1: "not sent" or "not confirmed" | the bus isn't running, or no paper run answered | the kill is recorded and applies at the run's next start; to stop a running run now, Ctrl-C in its terminal |
| the paper run says "kill switch is ON" at start and buys nothing | a kill switch you issued earlier is still on | `uv run firm resume --market <market> --reason "..." --confirm` |
| "feed stale" alerts, no orders | no trades from the venue's stream (network drop, venue outage) | orders resume when trades arrive; check with `firm feed watch` |
| "risk heartbeat lost: no new orders" | the bus stopped or the risk desk is unreachable | restart `firm bus up`; you get "back" when it recovers |
| `notifications: OFF (...)` / `notify test` exits 2 | Telegram settings missing | set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_CHAT_ID` in `.env` ([alerts](#alerts)) |
| `firm journal export` exits 2: `no journal at ...` | the journal was never built | run `firm journal build` first |
| `firm journal export` writes 0 trades | no closed trades in the range | check the dates: `--from` is inclusive, `--to` exclusive, both UTC |
| `firm ledger verify` exits 1 | the hash chain is broken: something edited the ledger | stop, keep a copy, and investigate; the ledger must never be edited |
| commit refused by the pre-commit hook | `scripts/check_repo.py` found data, a secret, a machine path or strategy code | move the file out of the repo; don't bypass the hook |

## 14. Where to read more

- [COMMANDS.md](COMMANDS.md): every command and option, with examples (the
  authoritative reference).
- [DASHBOARD.md](DASHBOARD.md): the web dashboard: turning it on, logging
  in, each view, safety.
- [deploy/README.md](../deploy/README.md): the unattended Linux runbook.
- [SPEC.md](SPEC.md): the design and the firm rules (target, not status).
- [TODO.md](../TODO.md): what's done, what's next, known bugs.
- [ARCHITECTURE.md](../ARCHITECTURE.md): how each module works.
- [DECISIONS.md](../DECISIONS.md): why things are the way they are.
- [plans/milestone-3.md](plans/milestone-3.md): the research floor being
  built now.
