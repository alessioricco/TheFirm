# Running a paper strategy unattended (Linux box)

The unattended run (milestone 2, phase H) uses `firm paper run` under a systemd
**user** unit on the always-on Linux box. It runs one verified strategy on
one market until stopped, restarts after a crash or a reboot, and records
everything in that machine's ledger.

Nothing here places a real order: `live_trading` is false and the only
adapter is the paper simulator.

## 1. The checkout

```bash
git clone https://github.com/alessioricco/TheFirm.git firm && cd firm
uv sync --frozen
uv run pytest -q                    # must be green on this machine
```

## 2. The stores

The run needs two stores on the box, both outside the repo:

- **Data store** (`FIRM_DATA_ROOT`). The deployment gate checks that the
  verdict's dataset is all real `api` captures, so the box needs that
  dataset's manifest, its normalised files, the raw `*.meta.json` sidecars
  and `reference/` (whose instrument records must name the live stream's
  symbol, e.g. `coinbase_ws`; `firm data add-symbol` records one, and the
  run refuses to start without it). The simplest way is to copy the whole store:
  `rsync -a <mac-store>/ box:<data-root>/`. While the run is going, the
  live feeds add their own captures to it. Raw captures are write-once and
  content-named, so they can later be copied back with `rsync -a --ignore-existing`.
- **State root** (`FIRM_STATE_ROOT`). Start it **empty** except for the one
  report: `<state-root>/reports/<verdict>.json`. The run creates its own
  `ledger.sqlite` there. Don't copy the Mac's ledger: the box's ledger is
  the paper-trading record, and the research ledger (trials, out-of-sample
  openings) stays where research runs. See DECISIONS.md, 2026-11-17.

The verifier report is self-checking (its hash is the verdict ID), so a copy
that was changed in transit is refused.

## 3. Install

The machine running the paper service also needs the NATS server
(`nats-server`, 2.10 or later): a release binary from
github.com/nats-io/nats-server/releases, on the `PATH`. Check with
`nats-server --version`. `firm paper run` talks through the bus by default
and refuses to start without it; a systemd unit for `firm bus up` is still
to do (TODO.md), so for now start the bus by hand or add `--no-bus`.


```bash
scripts/install_paper_service.sh crypto
```

This writes `~/.config/systemd/user/firm-paper@.service` and, if missing,
`~/.config/firm/paper-crypto.conf` (mode 600) from
`deploy/paper-run.conf.example`. Fill in the stores, the strategy file, the
verdict ID, the market, venue, instrument and timeframe: exactly what the
verifier passed. The gate refuses anything else.

Try it in the foreground first; the install script prints the command
(`--hours 0.1`, six minutes). That run opens the paper account in the box's
ledger, and the service carries on from it.

## 4. Run

```bash
sudo loginctl enable-linger "$USER"          # keep user units running when logged out
systemctl --user enable --now firm-paper@crypto
journalctl --user -u firm-paper@crypto -f    # what it is doing
```

- **Restarts.** On an error (a network outage at boot, a feed failure) the
  unit restarts after 60 seconds, forever. A gate refusal exits with code 4
  and is **not** retried. `systemctl --user status` shows it; fix the
  settings or the stores, then start it again.
- **Restart safety.** Balances and fills are rebuilt from the ledger. On
  start the history is loaded without deciding, so no bar is decided twice;
  but a bar that closed while the run was down is skipped, not decided late
  (for a daily strategy, being down across 00:00 UTC misses that day). Known gaps (TODO.md, "Paper-run
  follow-ups"): orders still open at a stop are lost, and the average entry
  price isn't rebuilt.
- **No timer.** The unit has no `--hours`, because each restart would reset
  it. Stop it by hand when the run is over.

## 5. Stop and check

```bash
systemctl --user stop firm-paper@crypto      # SIGTERM: feeds close, "stopped" recorded
FIRM_STATE_ROOT=<state-root> uv run firm ledger verify   # hash chain intact
FIRM_STATE_ROOT=<state-root> uv run firm ledger show --tail 50
```

Milestone 2's exit check is that every fill traces to a verdict ID. Each
fill names its order, and each order its verdict and risk decision. The
trade journal (phase G) will make this one command.
