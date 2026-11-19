# The dashboard

The firm's web dashboard: watch and steer the research floor, follow the
paper run, read the ledger, and look up every command, from one page in
your browser. This guide covers turning it on, logging in, what each view
does, and how it is kept safe. Every command and option is also in the
[command reference](COMMANDS.md#firm-dashboard).

> **Paper money only.** The dashboard can't place, size or cancel an order,
> and can't read or change risk limits, config or budgets. It reads the
> ledger the paper run writes and never talks to the run itself.

## Contents

1. [Start it in one minute](#1-start-it-in-one-minute)
2. [Try it on demo data first](#2-try-it-on-demo-data-first)
3. [Logging in](#3-logging-in)
4. [Using it next to a paper run](#4-using-it-next-to-a-paper-run)
5. [The four views](#5-the-four-views)
6. [Arranging the panels](#6-arranging-the-panels)
7. [How it is kept safe](#7-how-it-is-kept-safe)
8. [From another machine](#8-from-another-machine)
9. [Troubleshooting](#9-troubleshooting)

## 1. Start it in one minute

From the repository folder (after `uv sync --extra dev`, see the
[user guide](USER_GUIDE.md#4-install)):

```bash
uv run firm dashboard serve        # terminal 1: serves http://127.0.0.1:8765 (Ctrl-C stops it)
uv run firm dashboard url          # terminal 2: prints your login link
```

Open the printed link (`http://127.0.0.1:8765/login?token=...`) in your
browser. You land on the **Research** view, logged in. Keep the first
terminal open while you use it; Ctrl-C there stops the dashboard (it
doesn't stop anything else).

It needs no extra install (FastAPI and uvicorn come with `uv sync`), no
internet connection and no CDN: every file the page uses is in the repo.
NATS isn't needed either: the dashboard reads the ledger and the data
store directly.

## 2. Try it on demo data first

To see every panel filled in before you have a paper run or any dossier,
build the demo: synthetic data only (made-up prices, a real verification of
the example strategy on them, a paper-run history with fills, and dossiers
in every stage).

```bash
uv run python scripts/dashboard_demo.py               # writes ./data-demo-dashboard/
uv run firm dashboard serve --root data-demo-dashboard/data \
    --state-root data-demo-dashboard/state
uv run firm dashboard url --state-root data-demo-dashboard/state   # in another terminal
```

Everything you do there (start, pause, close dossiers, set the floor brief)
changes only the demo folder. It is git-ignored; delete it when you're done
(`rm -rf data-demo-dashboard`), or rebuild it with `--force`.

## 3. Logging in

- The first time you run `serve` or `url`, a random token is created in
  `<state>/dashboard/token` (readable only by you, mode 600). There are no
  accounts or passwords.
- `firm dashboard url` prints a link that contains the token. Opening it
  stores the token in a browser cookie (HttpOnly, so page scripts can't read
  it; SameSite=Strict, so other sites can't send it) and opens the page.
  You stay logged in until you clear the browser's cookies or the token
  changes.
- **Treat the link like a password**: open it in your own browser, don't
  paste it into chats, tickets or documents.
- **New token** (for example if the link leaked): stop `serve`, delete
  `<state>/dashboard/token`, start `serve` again and run `firm dashboard url`.
  Every browser logged in with the old token is logged out.
- Scripts can call the API with `Authorization: Bearer <token>` instead of
  the cookie.

If the page says **"Please log in again"**, the browser's cookie is missing
or the token changed: run `uv run firm dashboard url` and open the link.

## 4. Using it next to a paper run

Point the dashboard at the same folders as the run, so it reads that run's
ledger:

```bash
uv run firm dashboard serve --root <data root> --state-root <state root>
```

With the defaults (`./data` and `./state`, or `FIRM_DATA_ROOT` and
`FIRM_STATE_ROOT`), plain `uv run firm dashboard serve` already does. The
dashboard only reads the ledger (SQLite allows that while the run writes),
so starting, stopping or reloading it never disturbs the run. The same
status is available in the terminal: `uv run firm paper status`.

## 5. The four views

Switch views with the tabs at the top (or the left/right arrow keys on a
focused tab). The top bar also shows the **Live** indicator (new ledger
events arrive over a live connection; it says "Reconnecting" if the server
went away and recovers by itself), the time in UTC, **Edit layout**, and the
light/dark switch (remembered in this browser).

### Research

The research floor: researchers, their dossiers, budgets and the steering
commands. A **dossier** is one researcher's record for one strategy, from
brief to verdict.

| Panel | Shows |
| --- | --- |
| Floor | dossiers running (of the researchers allowed at once), waiting, paused, passed, failed, closed; trials used in total |
| Dossiers | every dossier, oldest first: stage, place in the queue, brief, and its budget used (trials, tokens, hours; cloud spend when it isn't zero). Click a row for its details |
| Floor brief | the standing direction for every researcher |
| Stages | how many dossiers are in each stage |
| Research activity | dossier, trial, verdict, owner-command and model-call events, live |

Actions, each the same as the matching `firm research` command and each
recorded in the ledger as an owner command "via dashboard":

- **New dossier** (top right): a brief, the market, instruments and
  timeframes (and, under *Advanced*, a dossier ID). It joins the queue.
- **Pause** / **Resume** (row buttons or the details panel): a paused
  dossier gives its place to the next in the queue.
- **Redirect**: a new brief. A draft just takes it; a dossier whose
  research has started is closed and a new, linked one opens (its
  hypothesis is locked once research starts).
- **Close**: final, with a reason you must give.
- **Floor brief**: set, change or clear it.

The **details panel** (click a dossier) shows everything: brief, scope,
budget used against budget, the hypothesis and why, code by hash, trials,
verdicts, notes and why it closed. Escape closes it.

Researchers themselves don't run yet: the researcher agent comes next in
milestone 3. Until then the floor holds what you start and steer here.

### Trading

The paper run, read from its ledger.

| Panel | Shows |
| --- | --- |
| Paper run | running or stopped (a run killed before it could record its stop still shows as running), its verdict, when it started, the risk heartbeat |
| Account | balances now (opening balance plus every fill), number of fills, the last one |
| Trades & P&L | closed and open trades and net P&L of closed trades, rebuilt the same way as the trade journal |
| Price | candles for the run's instrument and timeframe, from the dataset its verdict cites, with the run's fills marked (Buy / Sell). The history ends where that dataset ends: the dashboard never contacts the venue |
| Feeds | the latest status of each live stream (fresh, connected, stale, disconnected) |
| Risk | the risk heartbeat, risk halts recorded (kill switch, drawdown, daily loss), and the kill switch |
| Trading activity | fills, orders, risk decisions and halts, feed and market status, alerts, live |

**Kill switch:** not in the dashboard yet. It comes in the next release,
with a confirmation, through the bus and the risk desk. Until then stop a
run from its terminal with Ctrl-C, or `systemctl --user stop
firm-paper@<name>` for the Linux service.

### Ledger

Every recorded event, newest first: filter by type, search the reasons,
click an event to see its full payload, **Load older events** to page back.
New events appear at the top as they're written. The ledger is append-only;
nothing here can change it. (`uv run firm ledger verify` checks its hash
chain.)

### Commands

Every `firm` command, generated from the CLI itself, so it is always
complete and current: what it does, its options (required ones marked),
and an example with a **copy** button to paste into a terminal. Commands
the dashboard can do itself are tagged with where to find them. Search
filters commands and options. Run them from the repository folder.

## 6. Arranging the panels

The Research and Trading views are grids of panels:

1. Click **Edit layout** (top right). Panels get dashed outlines.
2. Drag a panel by its title bar; resize it from its bottom-right corner.
3. Click **Done**. Each view's layout is saved on the server
   (`<state>/dashboard/layouts/`), so it follows you to any browser using
   that state folder.
4. **Reset layout** (shown while editing) puts that view back to the
   default.

On a narrow window or a phone the panels stack in one column.

## 7. How it is kept safe

- **Loopback only.** `serve` listens on 127.0.0.1 and refuses any other
  address. Nothing on your network can reach it unless you tunnel to it
  yourself ([section 8](#8-from-another-machine)).
- **Owner only.** Every page, file and API call needs the token. Responses
  never contain it. There is no interactive API explorer.
- **No cross-site tricks.** Changes must be JSON requests, which another
  website can't send from your browser; the cookie is SameSite=Strict; a
  strict Content-Security-Policy allows only the page's own scripts (no
  inline or third-party code) and forbids framing the page.
- **Read-only for trading.** No endpoint can place, size or cancel an order
  or touch risk, config or budgets; a test checks the route names. Research
  steering goes through the same rules as the CLI (a hypothesis stays
  locked, closing needs a reason, a closed dossier takes no changes).
- **Everything recorded.** Each change you make is an OWNER_COMMAND event in
  the ledger, marked "via dashboard".
- **Pinned, reviewed libraries.** The chart and panel libraries are vendored
  at fixed versions; a test checks their SHA-256 against
  `src/firm/control/web/vendor/VENDOR.md`.

## 8. From another machine

The dashboard only listens on the machine's own loopback address. To use it
from another computer (for example the Mac, while the firm runs on another
machine you can reach over SSH), forward the port over SSH:

```bash
ssh -N -L 8765:127.0.0.1:8765 you@firm-host     # on your computer; leave it running
```

then run `firm dashboard url` on the firm's machine and open the link on
your computer (it points at `127.0.0.1:8765`, which the tunnel forwards).
Access over a private network such as Tailscale is planned; never open the
port to the internet.

## 9. Troubleshooting

| Symptom | What to do |
| --- | --- |
| The page says "Please log in again" | Run `uv run firm dashboard url` and open the link. If you deleted the token, restart `serve` first. |
| The browser can't connect | Is `firm dashboard serve` still running? Same port in the link (`--port` on both `serve` and `url`)? |
| `refusing to serve on <host>: loopback only` | By design. Use the default host and an SSH tunnel for another machine. |
| Port already in use (uvicorn error at start) | Another program, or another dashboard, uses 8765: `--port 8766` on both `serve` and `url`. |
| Trading shows "No paper run yet" | The dashboard reads another state folder than the run: pass the run's `--state-root` (and `--root`). |
| The price chart says the report or dataset isn't readable | The run's verifier report must be in `<state>/reports/` and its dataset in the data store you passed with `--root`. |
| The chart's history stops before today | Expected: it shows the dataset the run was verified on, never live prices. The run's own decisions are in the Trading activity and the log. |
| "Trades can't be rebuilt from this ledger" | The trade rebuild found an incomplete order chain (for example a ledger copied part-way). The other panels still work; `uv run firm journal build` says more. |
| Live indicator stays on "Reconnecting" | The server stopped or restarted; it reconnects by itself once `serve` is running again. |
| A panel is too small or in the wrong place | **Edit layout**, then drag or resize, or **Reset layout**. |
