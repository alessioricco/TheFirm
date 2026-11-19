# Commands

Every `firm` command, what it does, its options, and worked examples. Run
everything from the repository folder through `uv run` (for example
`uv run firm bus status`). `uv run firm <command> --help` prints the same
options. A test (`tests/control/test_commands_doc.py`) fails the build if a
command or option is missing here, so this page stays complete.

IDs in the examples are placeholders: `ds-xxxxxxxxxxxx` is a dataset
version, `v-xxxxxxxxxxxxxxxx` a verdict. Use the ones your own runs print.

## Contents

- [Before you start](#before-you-start)
- [Walkthrough: verify a strategy, then paper-trade it unattended](#walkthrough-verify-a-strategy-then-paper-trade-it-unattended)
- Reference: [data](#firm-data) · [verify](#firm-verify) · [state](#firm-state) ·
  [ledger](#firm-ledger) · [journal](#firm-journal) · [feed](#firm-feed) ·
  [paper](#firm-paper) · [bus](#firm-bus) · [notify](#firm-notify) ·
  [version](#firm-version)
- [Scripts](#scripts)
- [Exit codes](#exit-codes) · [Where things are written](#where-things-are-written)

## Before you start

- Python 3.12+, [uv](https://docs.astral.sh/uv/), then `uv sync --extra dev`.
- The NATS server for anything that trades (`firm bus up`, `firm paper run`):
  `brew install nats-server` on macOS, a release binary on Linux.
- Telegram alerts (optional): `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_OWNER_CHAT_ID` in `.env` (see [notify](#firm-notify)).
- Two folders, both git-ignored: the **data store** (`$FIRM_DATA_ROOT`,
  default `./data`) and the **state** (`$FIRM_STATE_ROOT`, default
  `./state`: ledger, verifier reports, bus logins, logs). Every command takes
  `--root` / `--state-root` to point elsewhere, for example a scratch copy.
- Configuration lives in `config/` (`--config`, default `config`):
  jurisdiction, markets, risk, fees, verifier thresholds, data quality, bus,
  notifications. It is read, never written, by these commands.

## Walkthrough: verify a strategy, then paper-trade it unattended

This is how milestone 2's unattended run is done, using the hand-written
example strategy `examples/strategies/ma_crossover.py` on Coinbase BTC-USD
daily bars.

**1. Download the bars and freeze a dataset version** (public data, no keys):

```bash
uv run python scripts/download_data.py --provider coinbase --symbol BTC-USD \
    --instrument BTC-USD --tf 1d --since 2017-01-01 --listed-at 2015-01-26
uv run firm data build-dataset --bars coinbase:BTC-USD:1d
#   -> dataset version ds-xxxxxxxxxxxx
```

**2. Verify the strategy on it.** Only a PASS can be deployed:

```bash
uv run firm verify \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --dataset ds-xxxxxxxxxxxx --venue coinbase --instrument BTC-USD \
    --tf 1d --market crypto
#   -> PASS v-xxxxxxxxxxxxxxxx  (report in state/reports/v-....json)
```

**3. (Optional) Replay its history through the paper path**, in minutes, to
see that paper trading reproduces the backtest's signals:

```bash
uv run firm paper replay \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --verdict v-xxxxxxxxxxxxxxxx --market crypto --venue coinbase \
    --instrument BTC-USD --tf 1d
```

**4. Check alerts reach your phone** (once):

```bash
uv run firm notify test
```

**5. Start the bus** in its own terminal, and leave it open:

```bash
caffeinate -is uv run firm bus up        # macOS: keep the Mac awake while it runs
```

**6. Start the paper run** in a second terminal:

```bash
caffeinate -is uv run firm paper run \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --verdict v-xxxxxxxxxxxxxxxx \
    --market crypto --venue coinbase --instrument BTC-USD --tf 1d
```

It checks the verdict (the deployment gate), opens the paper account the
first time (recorded in the ledger; a restart carries on from it), warms up
on the venue's own closed bars, and then decides on every bar close
(00:00 UTC for daily bars). Every order goes execution → risk → paper venue
over the bus. You get "started" and "stopped" on Telegram, and urgent alerts
for a stale feed, a risk halt, a crash or a lost risk heartbeat.

**7. Watch it** from a third terminal, or check on it any time:

```bash
tail -f state/logs/paper-crypto-coinbase.log   # status line every minute, every decision
uv run firm bus status                          # messages and consumers on the bus
uv run firm ledger show --tail 20               # the latest recorded events
uv run firm journal build && uv run firm journal show   # trades so far
```

**8. Stop it** with Ctrl-C in the paper terminal, then in the bus terminal.
For milestone 2 the run lasts at least 7 closed bars of the timeframe (a
week on daily bars, 7 hours on 1h bars).

Notes for a laptop: keep it plugged in (`caffeinate -is` stops sleep on
power); closing the lid can still sleep it. A Wi-Fi drop shows up as a
"feed stale" alert and no orders until the feed is back. Stopping the bus
during a run is safe: the run says "risk heartbeat lost: no new orders",
and "back" when you start `firm bus up` again.

---

## firm data

The data store: synthetic test data, dataset versions, instrument spellings.
Real downloads are `scripts/download_data.py` ([Scripts](#scripts)).

### firm data synthetic

Writes synthetic captures in a provider's format (no network, no real data),
for trying the firm out or for tests. Never mix with real data in one store.

| Option | Meaning |
| --- | --- |
| `--root` | data store (default `$FIRM_DATA_ROOT` or `./data`) |
| `--instrument` | firm instrument ID |
| `--symbol` | provider symbol |
| `--bars` | how many bars |
| `--seed` | random seed (same seed, same data) |

```bash
export FIRM_DATA_ROOT=./data-demo FIRM_STATE_ROOT=./data-demo/state
uv run firm data synthetic
```

### firm data build-dataset

Normalises captures into the firm's schema, leaves implausible bars out
under the data-quality rule (listed in the manifest), and freezes a
content-addressed **dataset version** that verdicts cite.

| Option | Meaning |
| --- | --- |
| `--root` | data store |
| `--config` | config root (the data-quality rule) |
| `--bars PROVIDER:SYMBOL:TF` | a bar series to include; repeat for several |
| `--trades PROVIDER:SYMBOL` | a trade series to include; repeat |
| `--derive FROM:TO` | also derive TO bars from every FROM series (e.g. `1m:1h`, `1d:1w`; days 00:00 UTC, weeks Monday) |
| `--provider`, `--symbol`, `--tf` | legacy form: one provider, repeated `--symbol`, one timeframe |

```bash
uv run firm data build-dataset --bars coinbase:BTC-USD:1d
uv run firm data build-dataset --bars coinbase:BTC-USD:1d --bars kraken:XBTUSD:1d --derive 1d:1w
```

### firm data add-symbol

Records how another provider (typically a live stream) spells an existing
instrument, as a new version of its record. `firm paper run` reads it to
find the stream symbol.

| Option | Meaning |
| --- | --- |
| `--root` | data store |
| `--venue` | the venue the instrument trades on |
| `--instrument` | firm ID, e.g. `BTC-USD` |
| `--provider` | e.g. `coinbase_ws` |
| `--symbol` | as that provider spells it |

```bash
uv run firm data add-symbol --venue coinbase --instrument BTC-USD \
    --provider coinbase_ws --symbol BTC-USD
```

## firm verify

Runs a strategy against an exact dataset version and returns **pass**,
**fail** or **unverified** (never "pass" for something it couldn't check):
data check, lookahead truncation, costs, parameter sensitivity, trial count
(deflated Sharpe), and the hidden out-of-sample period, opened once per
strategy, instrument and timeframe. The report is written to the state, its
hash is the verdict ID, and the trial is counted in the ledger. Re-verifying
the identical candidate replays the sealed result.

| Option | Meaning |
| --- | --- |
| `--strategy PATH:CLASS` | the strategy file and class |
| `--params` | JSON object overriding the strategy's `default_params` |
| `--dataset` | dataset version (`ds-...`) |
| `--venue`, `--instrument`, `--tf` | what to trade, on which timeframe |
| `--market` | market profile (costs, calendar), e.g. `crypto` |
| `--jurisdiction` | jurisdiction profile (default `uk`) |
| `--config` | config root |
| `--verifier` | verifier thresholds profile |
| `--root` | data store |
| `--state-root` | state (trials, out-of-sample log, reports) |
| `--report` | where to write the report (default `<state>/reports/<verdict>.json`) |
| `--as-of` | only bars closed by then (ISO time with zone): verify over the window an earlier opening used |
| `--sandbox` | run the strategy in the sandbox; always on for a file in the private store |

**Agent-written strategies** live in the private store
(`<data root>/research/strategies/<dossier>/`) and are always run in the
sandbox: a separate process under the OS sandbox (`sandbox-exec` on macOS,
`bubblewrap` on Linux), with no network, none of your files and no keys.
The verdict is the same as running the file directly. If no OS sandbox
works, or the sandbox fails mid-way, nothing more is judged and the result
is **unverified** (exit 2), never pass or fail. Inside another sandbox
(e.g. a Claude Code session on macOS) the OS sandbox can't start: run it
from your own terminal.

```bash
uv run firm verify --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover \
    --dataset ds-xxxxxxxxxxxx --venue coinbase --instrument BTC-USD --tf 1d --market crypto
# planted lookahead, rejected on purpose:
uv run firm verify --strategy examples/strategies/lookahead_ma.py:LookaheadCrossover \
    --dataset ds-xxxxxxxxxxxx --venue coinbase --instrument BTC-USD --tf 1d --market crypto
# an agent-written strategy from the private store (sandboxed automatically):
uv run firm verify \
    --strategy data/research/strategies/d-001/ma_cross-xxxxxxxxxxxx.py:MovingAverageCrossover \
    --dataset ds-xxxxxxxxxxxx --venue coinbase --instrument BTC-USD --tf 1d --market crypto
```

Exit code: 0 pass, 1 fail, 2 unverified (including a sandbox that failed), 3 error.

## firm state

### firm state show

The verifier's state, read-only: trials counted, out-of-sample openings,
reports.

| Option | Meaning |
| --- | --- |
| `--state-root` | state folder |
| `--json` | machine-readable output |

## firm ledger

The append-only, hash-chained record of every proposal, decision, order,
fill, alert and status change (`<state>/ledger.sqlite`).

### firm ledger show

The latest events, one line each.

| Option | Meaning |
| --- | --- |
| `--state-root` | state folder |
| `--tail` | how many (default 20) |

### firm ledger verify

Checks the hash chain end to end. Exit 1 if it is broken.

| Option | Meaning |
| --- | --- |
| `--state-root` | state folder |

### firm ledger import-state

Records existing verifier state (trials, openings, reports from before the
ledger existed) as ledger events. Idempotent: running it twice adds nothing.

| Option | Meaning |
| --- | --- |
| `--state-root` | state folder |

## firm journal

The trade journal: one row per paper trade (flat → long → flat), rebuilt
from the ledger into `<state>/journal.duckdb`. Never edited by hand; every
row carries its verdict, risk decisions and the ledger position it was built
from. Private: keep exports out of the repo.

### firm journal build

Rebuilds the journal from the whole ledger.

| Option | Meaning |
| --- | --- |
| `--state-root` | state folder |

### firm journal show

A summary and the latest trades.

| Option | Meaning |
| --- | --- |
| `--tail` | how many trades |
| `--state-root` | state folder |

### firm journal export

Trades decided in `[--from, --to)` to CSV or Parquet.

| Option | Meaning |
| --- | --- |
| `--from` | first day, `YYYY-MM-DD` (UTC), inclusive |
| `--to` | last day, `YYYY-MM-DD` (UTC), exclusive |
| `--format` | `csv` or `parquet` |
| `--out` | output file (outside the repo) |
| `--state-root` | state folder |

```bash
uv run firm journal build
uv run firm journal export --from 2026-11-01 --to 2026-12-01 --format csv --out ~/trades.csv
```

## firm feed

### firm feed watch

Runs a live trade stream for a while and prints its bars and status (needs
network). Useful to check a venue's stream and spelling before a paper run.

| Option | Meaning |
| --- | --- |
| `--provider` | `kraken_ws`, `coinbase_ws` or `binance_ws` |
| `--symbol` | as the stream spells it (`BTC/USD`, `BTC-USD`, `BTCUSDT`); repeat |
| `--seconds` | how long |
| `--tf` | bars to print (default `1m`) |
| `--stale-after` | seconds without a trade before it is called stale |
| `--root` | data store (captures are written there) |
| `--no-store` | don't write captures |

```bash
uv run firm feed watch --provider coinbase_ws --symbol BTC-USD --seconds 120
```

## firm paper

### firm paper run

Paper-trades one verified strategy on one market against live data, until
Ctrl-C or `--hours`. See the [walkthrough](#walkthrough-verify-a-strategy-then-paper-trade-it-unattended).
The deployment gate refuses (exit 4) anything but a passing, intact report
for the same code, parameters, venue, instrument and timeframe on real data.
Needs the bus (`firm bus up`) unless `--no-bus`.

| Option | Meaning |
| --- | --- |
| `--strategy PATH:CLASS` | the verified file and class |
| `--params` | JSON overrides; must equal the verified parameters |
| `--verdict` | verdict ID (`v-...`) of a passing report |
| `--market`, `--venue`, `--instrument`, `--tf` | exactly what was verified |
| `--lookback` | bars of history for the strategy (default 200) |
| `--hours` | stop after this long (default: until Ctrl-C) |
| `--stream-symbol` | as the live stream spells it, if not on record (`firm data add-symbol`) |
| `--no-notify` | no Telegram alerts |
| `--no-bus` | desks call each other in one process, no NATS |
| `--status-every` | seconds between status lines (default 60; 0 = off) |
| `--sandbox` | run the strategy in the sandbox; always on for a file in the private store. If the sandbox dies mid-run, that market stops (no further orders) |
| `--jurisdiction`, `--config` | profiles |
| `--root` | data store |
| `--state-root` | state and ledger |

The terminal shows each decision and a status line; everything is also
written to `<state>/logs/paper-<market>-<venue>.log` (the run prints the
`tail -f` command) and to the ledger.

### firm paper replay

Runs a verified strategy's history through the paper path (risk desk, paper
venue, ledger) in minutes and compares it with the backtest. Exit 1 if their
signals disagree. Writes to a scratch folder; the real ledger is only read.

| Option | Meaning |
| --- | --- |
| `--strategy`, `--params`, `--verdict` | as for `paper run` |
| `--market`, `--venue`, `--instrument`, `--tf` | as for `paper run` |
| `--lookback` | bars of history (default 200) |
| `--start` | first decided bar, `YYYY-MM-DD` (default: the hidden period) |
| `--gbp-per-quote` | FX rate (default: the paper account's opening rate) |
| `--out` | scratch folder (default `<state>/replays/<verdict>-<time>`) |
| `--jurisdiction`, `--config` | profiles |
| `--root` | data store |
| `--state-root` | reports and the paper ledger (read only) |

## firm bus

The message bus between desks (NATS with JetStream). Settings in
`config/bus.yaml`: loopback host and port, stream, retention, heartbeat and
decision timeouts.

### firm bus up

Starts a local `nats-server` in the foreground (Ctrl-C or SIGTERM stops it).
The first time it generates one login per desk into
`<state>/bus/credentials.json` (mode 600, never printed). Each desk may
publish only its own subjects and read only its own consumers; the server
enforces it. It creates the stream and the declared consumers, refuses to
start if the port is in use, and logs to `<state>/logs/bus.log`.

| Option | Meaning |
| --- | --- |
| `--config` | config root (`bus.yaml`) |
| `--state-root` | state folder (logins, JetStream store, log) |

### firm bus status

The stream (messages, bytes, sequence range) and each consumer (pending,
unacknowledged, redelivered). Says so if the bus isn't running.

| Option | Meaning |
| --- | --- |
| `--config` | config root |
| `--state-root` | state folder |

```bash
uv run firm bus status
```

## firm notify

Urgent alerts to the owner on Telegram: one chat, no commands read.

### firm notify chat-id

Lists the chats that wrote to your bot, to find your own chat ID. Send the
bot any message first. Needs `TELEGRAM_BOT_TOKEN` in `.env`; never prints it.

```bash
uv run firm notify chat-id     # then put TELEGRAM_OWNER_CHAT_ID=<id> in .env
```

### firm notify test

Sends a test urgent alert, prints how long delivery took, and records it in
the ledger.

| Option | Meaning |
| --- | --- |
| `--config` | config root (`notify.yaml`) |
| `--state-root` | ledger |

## firm version

Prints the version (also `firm --version`).

---

## Scripts

Run with `uv run python scripts/<name>` (shell scripts directly).

| Script | What it does |
| --- | --- |
| `download_data.py` | Public market data from a provider into the data store, with a sidecar per download. `--providers` lists them. Options: `--provider`, `--symbol` (as the provider spells it), `--instrument` (firm ID), `--venue`, `--asset-class crypto\|equity`, `--quote`, `--tf`, `--since`, `--until`, `--listed-at` (needed the first time), `--trades-pages N`, `--root`, `--dry-run`. |
| `bus_smoke.py` | Starts a throwaway `nats-server` exactly as `firm bus up` does and checks the bus end to end, including that the server refuses what the permission table forbids. Run after changing `src/firm/bus/`. |
| `check_repo.py` | The public-repo guard: no data, secrets, machine paths, big files, or strategy code outside `examples/strategies/`, `src/` and `tests/` (agent strategies live in the private store). `--staged` (pre-commit hook) or `--all` (CI). |
| `install_paper_service.sh <name>` | Linux only: installs the `firm-paper@<name>` systemd user unit and its config file (see `deploy/README.md`). Starts nothing. |

```bash
uv run python scripts/download_data.py --providers
uv run python scripts/download_data.py --provider kraken --symbol XBTUSD \
    --instrument BTC-USD --tf 1d --dry-run
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success (verify: pass) |
| 1 | failure (verify: fail; ledger verify: broken chain; replay: signals differ; paper run: stopped by a failure, or the bus isn't running) |
| 2 | verify: unverified; other commands: missing input (e.g. no Telegram token, a journal that was never built) |
| 3 | error (bad input, missing file, config problem) |
| 4 | paper run refused by the deployment gate: a supervisor must not retry |

## Where things are written

| Path | What |
| --- | --- |
| `$FIRM_DATA_ROOT` (`./data`) | `raw/`, `normalized/`, `datasets/`, `reference/`, `golden/` |
| `<state>/ledger.sqlite` | the ledger |
| `<state>/reports/` | verifier reports (`v-....json`) |
| `<state>/journal.duckdb` | the trade journal |
| `<state>/bus/` | bus logins, server config, JetStream store |
| `<state>/logs/` | `paper-<market>-<venue>.log`, `bus.log` |
| `<state>/replays/` | `firm paper replay` scratch folders |
