# The Firm

A one-person trading firm that runs on local models. AI agents research and
train strategies; deterministic code verifies, risk-checks, sizes and
executes them. **Version 1 trades paper money only.**

> **Not financial advice.** This is a personal research and engineering
> project. It gives no signals or advice to anyone, it does not trade real
> money, and nothing in it is a recommendation to buy or sell anything.
> Backtests, including the ones this project calls "verified", can and do
> fail on future data. Use at your own risk.

**New here? Start with the [user guide](docs/USER_GUIDE.md)**: install, a
safe first run on synthetic data, real data, verification, paper trading,
configuration and troubleshooting. Every command and option is in the
[command reference](docs/COMMANDS.md).

## The idea in one paragraph

Language models are good at proposing ideas and bad at being trusted with
money. So in the firm, **agents propose and code decides**: a strategy
written by an agent (or by hand) only counts once a deterministic verifier
has checked that its backtest is trustworthy, and no model output can ever
reach an order except through a verdict and a risk decision. Everything is
point-in-time, everything is recorded, and live trading stays off until the
owner turns it on by hand.

## Status

| Milestone | State |
|---|---|
| 0. Repository skeleton: typed domain objects, bus message rules, config profiles, venue adapter interface | done |
| 1. Point-in-time data plane and strategy verifier (crypto) | done |
| 2. Paper simulator, risk desk, ledger, trade journal | next |
| 3. Research agents on [Certo](https://github.com/alessioricco/certo) | planned |
| 4. Control API, CLI, web UI, notifications | planned |
| 5. Local decision model ("judge") as features | planned |
| 6. US shares | planned |

The design is in [`docs/SPEC.md`](docs/SPEC.md); what's left is in
[`TODO.md`](TODO.md), why things are the way they are is in
[`DECISIONS.md`](DECISIONS.md), and how each module works is in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## What the verifier checks

A strategy is a small Python class that returns a target exposure per bar.
`firm verify` runs it against an exact, checksummed dataset version and
returns **pass**, **fail** or **unverified** (never "pass" for something it
couldn't check), with a report whose hash is the verdict ID:

1. **Lookahead truncation**: re-run with the data cut at many decision
   times; if any past signal changes, the strategy was peeking at the future.
2. **Cost realism**: must survive the market profile's fees, spread and
   slippage, and not lose money with costs doubled.
3. **Parameter sensitivity**: nudging each parameter must not collapse it.
4. **Trial count**: the Sharpe ratio is deflated for how many strategies
   were tried in total (Bailey & López de Prado, 2014).
5. **Hidden out-of-sample**: a final period no author sees, opened once
   per strategy, and only after the other checks pass.

All thresholds live in [`config/verify/default.yaml`](config/verify/default.yaml)
and are still placeholders.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Tested on macOS
and Linux arm64; no GPU needed.

**The [user guide](docs/USER_GUIDE.md) walks through everything below in
order. Every command, its options and worked examples (including an
unattended paper run from download to stop) are in
[docs/COMMANDS.md](docs/COMMANDS.md).**

The desks talk over a message bus, so running the firm (not the tests) also
needs the [NATS server](https://nats.io) (`nats-server`, 2.10 or later, with
JetStream):

```bash
brew install nats-server          # macOS
# Linux: a release binary from github.com/nats-io/nats-server/releases
nats-server --version
```

The test suite never needs it: tests use an in-memory bus. To start the bus
(foreground; Ctrl-C stops it) and check it:

```bash
uv run firm bus up        # local server from config/bus.yaml, one login per desk
uv run firm bus status    # in another terminal: the stream and its consumers
```

`firm paper run` needs the bus running (it says so if not); `--no-bus` runs
the desks in one process without it.

```bash
uv sync --extra dev
git config core.hooksPath .githooks    # enable the pre-commit guard (once per clone)

# try it with synthetic data in a separate demo store (no network, no real data)
export FIRM_DATA_ROOT=./data-demo FIRM_STATE_ROOT=./data-demo/state
uv run firm data synthetic
uv run firm data build-dataset --bars kraken:XBTUSD:1d
#   -> prints a dataset version, e.g. ds-xxxxxxxxxxxx
uv run firm verify --dataset ds-xxxxxxxxxxxx --venue kraken --instrument BTC-USD \
    --tf 1d --market crypto \
    --strategy examples/strategies/ma_crossover.py:MovingAverageCrossover
uv run firm verify ... --strategy examples/strategies/lookahead_ma.py:LookaheadCrossover
```

The second example has lookahead planted on purpose; the verifier rejects it.
Exit codes: 0 pass, 1 fail, 2 unverified, 3 error.

### Real market data

`scripts/download_data.py` fetches public market data (no API keys) from any
registered provider into your local data store; you run it yourself, tests
never do. Built in: **kraken**, **coinbase**, **binance** (crypto) and
**yahoo** (shares; unofficial endpoint, prices stored un-split-adjusted).
Check each provider's terms of use; the data is for your own use. Using a
source for research or paper trading says nothing about trading there with
real money: that is decided by the jurisdiction profile and is off in v1.

```bash
uv run python scripts/download_data.py --providers        # what's available
uv run python scripts/download_data.py --provider coinbase --symbol BTC-USD \
    --instrument BTC-USD --tf 1d --since 2017-01-01 --listed-at 2015-01-26
uv run python scripts/download_data.py --provider binance --symbol BTCUSDT \
    --instrument BTC-USDT --tf 1d --since 2017-08-17 --listed-at 2017-08-17
uv run python scripts/download_data.py --provider yahoo --symbol NVDA \
    --instrument NVDA.XNAS --venue xnas --asset-class equity --quote USD \
    --tf 1d --since 2015-01-01 --listed-at 1999-01-22
uv run firm data build-dataset --bars coinbase:BTC-USD:1d
# several venues and kinds in one dataset version:
uv run firm data build-dataset --bars coinbase:BTC-USD:1d --bars kraken:XBTUSD:1d \
    --trades kraken:XBTUSD
uv run firm state show          # trials, out-of-sample openings, reports
uv run firm ledger show         # the append-only, hash-chained ledger
uv run firm ledger verify       # check the hash chain
```

## Data never goes in this repo

This repository is public. Market data, derived prices, datasets, verifier
state, models and secrets are **never** committed:

- Data lives in a git-ignored store, `$FIRM_DATA_ROOT` (default `./data/`),
  with an immutable layout: raw downloads exactly as received, normalised
  Parquet, content-addressed dataset versions, an instrument master that
  keeps delisted instruments. See ARCHITECTURE.md "Data store".
- Tests use only synthetic data generated in code, and cannot use the
  network.
- `scripts/check_repo.py` blocks data files, large files, secrets and
  machine-specific paths: in the pre-commit hook, in the tests, and in CI.

## Configuration, not code

Country, markets, venues, costs, verifier thresholds and model choices are
YAML profiles under [`config/`](config/), validated strictly (unknown keys
are an error; `live_trading: true` is refused in v1). Hosts and keys go in
`.env` (see [`.env.example`](.env.example)); it is git-ignored.

## Development

```bash
uv run pytest                    # all tests: no network, no model
uv run pytest tests/invariants   # one test per firm rule
uv run ruff check . && uv run ruff format --check .
uv run mypy
python3 scripts/check_repo.py --all
```

CI runs all of this on Linux and macOS arm64, Python 3.12 and the latest
release, plus a secret scan of the full history. Each firm rule in the SPEC
has a test in `tests/invariants/`; rules not yet enforced are listed as
expected failures, so the whole list shows in every run.

Built with [Claude Code](https://claude.com/claude-code) and Certo. Working
notes for coding agents are in [`CLAUDE.md`](CLAUDE.md).

## Licence

MIT, see [LICENSE](LICENSE). Code ported from other projects keeps its
original licence and notices (noted where it applies).
