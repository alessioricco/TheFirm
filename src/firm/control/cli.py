"""The `firm` command line. Grows into a client of the control API (milestone 4).

Exit codes of `firm verify`: 0 pass, 1 fail, 2 unverified, 3 error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from firm import __version__

EXIT_ERROR = 3
EXIT_REFUSED = 4  # the deployment gate said no: a supervisor must not retry


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"firm {__version__}")
    return 0


def _cmd_synthetic(args: argparse.Namespace) -> int:
    from firm.data.store import data_root, iter_captures
    from firm.data.synthetic import seed_store

    root = data_root(args.root)
    real = [
        c
        for c in iter_captures(root, provider="kraken", provider_symbol=args.symbol)
        if c.meta.capture_method != "synthetic"
    ]
    if real:
        print(
            f"refusing: {root} holds real captures for kraken:{args.symbol}; "
            "use a separate --root for synthetic data",
            file=sys.stderr,
        )
        return EXIT_ERROR
    seed_store(
        root,
        venue="kraken",
        instrument=args.instrument,
        provider_symbol=args.symbol,
        n=args.bars,
        seed=args.seed,
    )
    print(f"synthetic captures for kraken:{args.symbol} written under {root}")
    return 0


def _series(args: argparse.Namespace) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Parse `--bars P:S:TF`, `--trades P:S` and the legacy `--provider/--symbol/--tf`."""
    bars: list[tuple[str, str, str]] = []
    trades: list[tuple[str, str]] = []
    for spec in args.bars or []:
        parts = spec.split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"--bars expects PROVIDER:SYMBOL:TF, got {spec!r}")
        bars.append((parts[0], parts[1], parts[2]))
    for spec in args.trades or []:
        parts = spec.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"--trades expects PROVIDER:SYMBOL, got {spec!r}")
        trades.append((parts[0], parts[1]))
    if args.symbol or args.provider or args.tf:
        if not (args.symbol and args.provider and args.tf):
            raise ValueError("--provider, --symbol and --tf go together")
        bars += [(args.provider, s, args.tf) for s in args.symbol]
    if not bars and not trades:
        raise ValueError("nothing to build: give --bars and/or --trades")
    return bars, trades


def _cmd_add_symbol(args: argparse.Namespace) -> int:
    """Record how one more provider spells an existing instrument (a new version)."""
    from firm.data import providers, streams
    from firm.data.fetch import ensure_instrument
    from firm.data.reference import latest_instruments
    from firm.data.store import data_root

    known = sorted(set(providers.available()) | set(streams.available()))
    if args.provider not in known:
        print(f"error: unknown provider {args.provider!r}; available: {known}", file=sys.stderr)
        return EXIT_ERROR
    root = data_root(args.root)
    existing = latest_instruments(root).get((args.venue, args.instrument))
    if existing is None:
        print(f"error: no instrument record {args.venue}:{args.instrument}", file=sys.stderr)
        return EXIT_ERROR
    rec = ensure_instrument(
        root,
        provider=args.provider,
        symbol=args.symbol,
        venue=args.venue,
        instrument=args.instrument,
        asset_class=existing[1].asset_class,
        listed_at=None,
    )
    print(f"{args.venue}:{args.instrument} provider symbols: {rec.provider_symbols}")
    return 0


def _cmd_build_dataset(args: argparse.Namespace) -> int:
    from firm.data.dataset import build_dataset
    from firm.data.normalize import normalize_bars, normalize_trades
    from firm.data.resample import derive_bars
    from firm.data.store import data_root

    root = data_root(args.root)
    bars, trades = _series(args)
    from pathlib import Path

    from firm.core.config import load_data_quality

    quality = load_data_quality(Path(args.config))
    files = []
    for provider, symbol, tf in bars:
        files += normalize_bars(
            root, provider=provider, provider_symbol=symbol, tf=tf, quality=quality
        )
    for provider, symbol in trades:
        files += normalize_trades(root, provider=provider, provider_symbol=symbol)
    for spec in args.derive or []:
        from_tf, _, to_tf = spec.partition(":")
        if not from_tf or not to_tf:
            raise ValueError(f"--derive expects FROM:TO, got {spec!r}")
        series: dict[tuple[str, str], list] = {}
        for f in files:
            if f.kind == "bars" and f.tf == from_tf and f.derived_from is None:
                series.setdefault((f.venue, f.instrument), []).append(f)
        if not series:
            raise ValueError(f"--derive {spec}: no native {from_tf} bars in this build")
        for key in sorted(series):
            files += derive_bars(root, series[key], to_tf=to_tf)
    manifest = build_dataset(root, files)
    rows = sum(f.rows for f in manifest.files)
    print(f"{manifest.dataset_version}  ({len(manifest.files)} files, {rows} rows)")
    series: dict[tuple, list] = {}
    for f in manifest.files:
        key = (f.kind, f.venue, f.instrument, f.tf, f.alignment, f.derived_from)
        series.setdefault(key, []).append(f)
    for (kind, venue, instrument, tf, alignment, derived_from), group in sorted(
        series.items(), key=lambda kv: tuple(str(x) for x in kv[0])
    ):
        revisions = sum(f.revisions for f in group)
        print(
            f"  {kind:<6} {venue}:{instrument} {tf or '':<3} {sum(f.rows for f in group):>8} rows"
            f" in {len(group)} file(s)"
            f"{f'  aligned={alignment}' if alignment else ''}"
            f"{f'  derived_from={derived_from}' if derived_from else ''}"
            f"{f'  revisions={revisions}' if revisions else ''}"
            f"{f'  quarantined={n}' if (n := sum(len(f.quarantined) for f in group)) else ''}"
        )
        for f in group:
            for q in f.quarantined:
                print(f"      quarantined {q['open_time']}: {q['reason']}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from firm.sandbox.client import SandboxError
    from firm.verify.state import state_root

    path_str, _, class_name = args.strategy.rpartition(":")
    if not class_name:
        print("--strategy must be PATH:CLASS", file=sys.stderr)
        return EXIT_ERROR
    state = state_root(args.state_root)
    try:
        result = _verify_file(args, path_str, class_name, state)
    except SandboxError as exc:
        print(f"UNVERIFIED  sandbox failed before anything was judged: {exc}", file=sys.stderr)
        return 2
    out = (
        Path(args.report)
        if args.report
        else state / "reports" / f"{result.verdict.verdict_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.report)
    v = result.verdict
    print(f"{v.verdict_id}  {v.outcome.value.upper()}  ({v.strategy_id} on {v.dataset_version})")
    for c in v.checks:
        print(f"  {c.status.value:<8} {c.name}: {c.detail}")
    print(f"report: {out}")
    return result.exit_code


def _verify_file(  # type: ignore[no-untyped-def]
    args: argparse.Namespace, path_str: str, class_name: str, state: Path
):
    from firm.data.store import data_root
    from firm.ledger.sink import LedgerSink, ledger_path
    from firm.ledger.store import Ledger
    from firm.verify.runner import verify_strategy_file
    from firm.verify.state import state_root

    return verify_strategy_file(
        strategy_path=Path(path_str),
        class_name=class_name,
        params=json.loads(args.params) if args.params else None,
        data_root=data_root(args.root),
        dataset_version=args.dataset,
        venue=args.venue,
        instrument=args.instrument,
        tf=args.tf,
        config_root=Path(args.config),
        jurisdiction=args.jurisdiction,
        market=args.market,
        state=state,
        now=datetime.now(UTC),
        as_of=datetime.fromisoformat(args.as_of).astimezone(UTC) if args.as_of else None,
        verifier_name=args.verifier,
        sink=LedgerSink(Ledger(ledger_path(state_root(args.state_root)))),
        sandbox=args.sandbox,
    )


def _cmd_state_show(args: argparse.Namespace) -> int:
    from firm.verify.state import state_root, state_summary

    root = state_root(args.state_root)
    if not root.exists():
        print(f"no verifier state at {root}")
        return 0
    s = state_summary(root)
    if args.json:
        print(json.dumps(s, indent=1, sort_keys=True))
        return 0
    print(f"state: {s['root']}  (schema {s['schema_version'] or 'unversioned'})")
    print(f"trials: {s['trials_total']} (global count used by the verifier)")
    for exp, n in sorted(s["trials_by_experiment"].items()):
        print(f"  {exp}: {n}")
    print("out-of-sample vault:")
    for strategy, counts in sorted(s["oos_vault"].items()):
        print(f"  {strategy}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"reports: {len(s['reports'])}")
    for r in s["reports"]:
        print(
            f"  {r['verdict_id']}  {str(r['outcome']).upper():<10} "
            f"{r['strategy_id']} on {r['dataset_version']}"
        )
    return 0


def _ledger(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from firm.ledger.sink import ledger_path
    from firm.ledger.store import Ledger
    from firm.verify.state import state_root

    return Ledger(ledger_path(state_root(args.state_root)))


def _cmd_ledger_show(args: argparse.Namespace) -> int:
    ledger = _ledger(args)
    events = list(ledger.read(max(0, ledger.count() - args.tail)))
    for e in events:
        when = f"{e.recorded_at:%Y-%m-%d %H:%M:%S}"
        print(f"{e.sequence:>6}  {when}  {e.event_type.value:<14} {e.reason}")
    print(f"{ledger.count()} events")
    return 0


def _journal_summary(db: Path) -> list[str]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        closed, wins, net, fees, slip = con.execute(
            "SELECT count(*), count(*) FILTER (net_pnl > 0), sum(net_pnl), sum(fees), "
            "avg(entry_slippage_bps) FROM trades WHERE status = 'closed'"
        ).fetchall()[0]
        open_ = con.execute("SELECT count(*) FROM trades WHERE status = 'open'").fetchall()[0][0]
        seq, built = con.execute(
            "SELECT ledger_sequence, strftime(built_at, '%Y-%m-%d %H:%M') FROM journal_meta"
        ).fetchall()[0]
        quotes = [
            r[0] for r in con.execute("SELECT DISTINCT quote_currency FROM trades").fetchall()
        ]
    finally:
        con.close()
    q = quotes[0] if len(quotes) == 1 else "(mixed currencies)"
    lines = [f"journal {db}: built from ledger up to event {seq} ({built} UTC)"]
    lines.append(f"  {closed} closed trades, {open_} open")
    if closed:
        lines.append(
            f"  closed: {wins}/{closed} won; net P&L {net:.2f} {q} after {fees:.2f} {q} fees; "
            f"entry slippage {slip:.1f} bps on average"
        )
    return lines


def _cmd_journal_build(args: argparse.Namespace) -> int:
    from firm.ledger.journal import build_journal, journal_path
    from firm.verify.state import state_root

    db = journal_path(state_root(args.state_root))
    build_journal(_ledger(args), db)
    print("\n".join(_journal_summary(db)))
    return 0


def _cmd_journal_show(args: argparse.Namespace) -> int:
    import duckdb

    from firm.ledger.journal import journal_path
    from firm.verify.state import state_root

    db = journal_path(state_root(args.state_root))
    if not db.is_file():
        print(f"no journal at {db}: run `firm journal build` first", file=sys.stderr)
        return 2
    print("\n".join(_journal_summary(db)))
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    try:
        rows = con.execute(
            "SELECT * FROM (SELECT strftime(decision_time, '%Y-%m-%d %H:%M') AS t, "
            "instrument, quantity, entry_price, exit_price, net_pnl, net_return, verdict_id "
            "FROM trades "
            "ORDER BY decision_time DESC LIMIT ?) ORDER BY t",
            [args.tail],
        ).fetchall()
    finally:
        con.close()
    for at, inst, qty, entry, exit_, net, ret, verdict in rows:
        result = f"net {net:+.2f} ({ret:+.2%})" if net is not None else "open"
        price = f"{entry:.2f} -> {exit_:.2f}" if exit_ is not None else f"{entry:.2f}"
        print(f"  {at}  {inst} {qty.normalize()} at {price}  {result}  {verdict}")
    return 0


def _cmd_journal_export(args: argparse.Namespace) -> int:
    from firm.ledger.journal import export_journal, journal_path
    from firm.verify.state import state_root

    def day(s: str | None) -> datetime | None:
        return datetime.fromisoformat(s).replace(tzinfo=UTC) if s else None

    db = journal_path(state_root(args.state_root))
    if not db.is_file():
        print(f"no journal at {db}: run `firm journal build` first", file=sys.stderr)
        return 2
    n = export_journal(db, Path(args.out), fmt=args.format, start=day(args.from_), end=day(args.to))
    print(f"wrote {n} trades to {args.out}")
    return 0


def _cmd_ledger_verify(args: argparse.Namespace) -> int:
    report = _ledger(args).verify_chain()
    if report.ok:
        print(f"OK: hash chain intact over {report.events} events")
        return 0
    print(f"BROKEN at sequence {report.first_bad_sequence}: {report.detail}")
    return 1


def _cmd_ledger_import(args: argparse.Namespace) -> int:
    from firm.ledger.sink import LedgerSink, state_history
    from firm.verify.state import emit_trial, emit_trial_alias, emit_vault_event, state_root
    from firm.verify.verifier import emit_verdict

    state = state_root(args.state_root)
    ledger = _ledger(args)
    before = ledger.count()
    sink = LedgerSink(ledger)
    emitters = {
        "trial": emit_trial,
        "trial_alias": emit_trial_alias,
        "vault": emit_vault_event,
        "report": emit_verdict,
    }
    for _, _, kind, record in state_history(state):
        emitters[kind](sink, record)
    print(
        f"imported {ledger.count() - before} new events ({ledger.count()} in the ledger); "
        "already-recorded facts were skipped"
    )
    return 0


def _cmd_feed_watch(args: argparse.Namespace) -> int:
    """Run one live stream for a while: print closed 1m bars and feed status changes."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from firm.data.store import data_root
    from firm.data.streams.bars import BarBuilder
    from firm.data.streams.feed import Feed

    started = int(datetime.now(UTC).timestamp() * 1e9)
    builders = {s: BarBuilder(args.tf, started_ns=started) for s in args.symbol}

    def on_gap(at: datetime) -> None:
        for b in builders.values():
            b.mark_gap(int(at.timestamp() * 1e9))

    def bar_line(symbol: str, bar) -> None:  # type: ignore[no-untyped-def]
        t = datetime.fromtimestamp(bar["open_time_ns"] / 1e9, tz=UTC)
        print(
            f"{symbol:<10} {t:%H:%M} O {bar['open']} H {bar['high']} L {bar['low']} "
            f"C {bar['close']} V {bar['volume']:.6g}"
        )

    def on_trade(symbol: str, trade) -> None:  # type: ignore[no-untyped-def]
        for bar in builders[symbol].add(trade):
            bar_line(symbol, bar)

    class Printer:
        def emit(self, event_type, reason, payload, event_id=None):  # type: ignore[no-untyped-def]
            print(f"[{event_type.value}] {reason}")

    feed = Feed(
        args.provider,
        args.symbol,
        root=None if args.no_store else data_root(args.root),
        sink=Printer(),
        on_trade=on_trade,
        on_gap=on_gap,
        stale_after=timedelta(seconds=args.stale_after),
    )

    async def go() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))
        end = asyncio.get_running_loop().time() + args.seconds
        while asyncio.get_running_loop().time() < end and not task.done():
            await asyncio.sleep(1)
            feed.check()
            now_ns = int(datetime.now(UTC).timestamp() * 1e9)
            for symbol, b in builders.items():
                for bar in b.close_due(now_ns):
                    bar_line(symbol, bar)
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except TimeoutError:
            task.cancel()
            feed.flush()

    asyncio.run(go())
    stored = "not stored" if args.no_store else "stored as raw captures"
    partial = sum(b.incomplete for b in builders.values())
    print(
        f"{feed.trades} trades over {feed.connections} connection(s); {stored}; "
        f"{partial} partly-watched bar(s) dropped"
    )
    return 0


def _cmd_paper_run(args: argparse.Namespace) -> int:
    """Paper-trade one verified strategy on one market against live data."""
    import asyncio
    import json
    from pathlib import Path

    from firm.control.paper import PaperRun
    from firm.data.store import data_root
    from firm.execution.deploy import DeploymentRefused
    from firm.verify.state import state_root

    path, _, cls = args.strategy.rpartition(":")
    run = PaperRun(
        config_root=Path(args.config),
        jurisdiction=args.jurisdiction,
        market=args.market,
        venue=args.venue,
        instrument=args.instrument,
        tf=args.tf,
        strategy_path=Path(path),
        class_name=cls,
        params=json.loads(args.params) if args.params else None,
        verdict_id=args.verdict,
        data_root=data_root(args.root),
        state_root=state_root(args.state_root),
        lookback=args.lookback,
        seconds=args.hours * 3600 if args.hours else None,
        stream_symbol=args.stream_symbol,
        status_seconds=args.status_every,
        notifier=_owner_notifier(args.no_notify),
        bus=_paper_bus(args),
        log=_tee_log(state_root(args.state_root), f"paper-{args.market}-{args.venue}.log"),
        sandbox=args.sandbox,
    )
    from firm.bus.server import BusDown
    from firm.sandbox.client import SandboxError

    try:
        return asyncio.run(run.run())
    except DeploymentRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except SandboxError as exc:  # can't start: nothing ran, no order was placed
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BusDown as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _paper_bus(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """A login per service on the local bus (None with --no-bus). Connecting
    raises `BusDown`, with how to start it, if the bus isn't running."""
    from firm.bus.nats import NatsBus
    from firm.bus.server import BusDown, listening
    from firm.core.config import load_bus
    from firm.verify.state import state_root

    if args.no_bus:
        print("bus: off (--no-bus): the desks call each other in-process", flush=True)
        return None
    cfg = load_bus(Path(args.config))
    state = state_root(args.state_root)
    print(f"bus: {cfg.url}", flush=True)

    async def connect(service):  # type: ignore[no-untyped-def]
        if not listening(cfg.host, cfg.port):
            raise BusDown(
                f"the bus is not running at {cfg.url}: start it in another terminal with "
                "`uv run firm bus up` (or run with --no-bus)"
            )
        return await NatsBus.for_service(cfg, state, service)

    return connect


def _owner_notifier(off: bool):  # type: ignore[no-untyped-def]
    """The owner's Telegram notifier from the environment, or None (and why, printed)."""
    import os

    from firm.notify.telegram import telegram_from_env

    if off:
        print("notifications: off (--no-notify)", flush=True)
        return None
    notifier, why = telegram_from_env(os.environ)
    print(
        "notifications: Telegram, to the owner's chat"
        if notifier
        else f"notifications: OFF ({why})",
        flush=True,
    )
    return notifier


def _cmd_notify_chat_id(args: argparse.Namespace) -> int:
    import os

    from firm.notify.telegram import TOKEN_ENV, TelegramError, chat_ids

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(f"{TOKEN_ENV} is not set (put it in .env)", file=sys.stderr)
        return 2
    try:
        chats = chat_ids(token)
    except TelegramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not chats:
        print("no messages yet: open your bot in Telegram, send it any message, run this again")
        return 1
    for cid, kind, name in chats:
        print(f"{cid}  {kind}  {name}")
    print("put your own private chat's ID in .env as TELEGRAM_OWNER_CHAT_ID=<id>")
    return 0


def _cmd_notify_test(args: argparse.Namespace) -> int:
    import asyncio
    import os
    import uuid

    from firm.core.config import load_notify
    from firm.core.domain import Notification, NotificationLevel
    from firm.ledger.sink import LedgerSink
    from firm.notify.alerts import Outbox
    from firm.notify.telegram import telegram_from_env

    notifier, why = telegram_from_env(os.environ)
    if notifier is None:
        print(f"notifications are off: {why}", file=sys.stderr)
        return 2
    sink = LedgerSink(_ledger(args))
    outbox = Outbox(notifier, sink, load_notify(Path(args.config)))
    outbox.put(
        Notification(
            notification_id=f"n-test-{uuid.uuid4().hex[:12]}",
            level=NotificationLevel.URGENT,
            text="test alert from the firm: if you can read this, urgent alerts reach you",
            created_at=datetime.now(UTC),
        )
    )
    asyncio.run(outbox.drain())
    last = _ledger(args).head()
    payload = last.payload if last else {}
    if payload.get("delivered"):
        print(f"delivered in {payload['latency_seconds']}s (recorded in the ledger)")
        return 0
    print(f"NOT delivered: {payload.get('error')}", file=sys.stderr)
    return 1


def _cmd_bus_up(args: argparse.Namespace) -> int:
    """Start the local message bus in the foreground (Ctrl-C stops it)."""
    import asyncio
    import signal

    from firm.bus.auth import ADMIN
    from firm.bus.nats import NatsBus
    from firm.bus.server import BusServerError, start
    from firm.core.config import load_bus
    from firm.verify.state import state_root

    cfg = load_bus(Path(args.config))
    state = state_root(args.state_root)
    log_file = state / "logs" / "bus.log"
    try:
        proc = start(cfg, state, log_file)
    except BusServerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    async def ready() -> int:
        bus = await NatsBus.for_service(cfg, state, ADMIN)
        try:
            info = await bus.ensure_stream()
            created = await bus.ensure_consumers()
            if created:
                print(f"created bus consumers: {', '.join(created)}")
            return len(info.config.subjects or [])
        finally:
            await bus.close()

    def stop_on_term(signum: int, frame: object) -> None:
        raise KeyboardInterrupt  # SIGTERM (kill, systemd) stops the server too

    signal.signal(signal.SIGTERM, stop_on_term)
    try:
        n = asyncio.run(ready())
        print(f"bus up: {cfg.url}, stream {cfg.stream} ({n} subjects), one login per service")
        print(f"log: {log_file}  (follow it with: tail -f {log_file})")
        print("Ctrl-C stops it; `firm bus status` shows the stream and its consumers", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)
        print("bus stopped", flush=True)
    return 0 if proc.returncode in (0, -15) else 1


def _cmd_bus_status(args: argparse.Namespace) -> int:
    import asyncio

    from firm.bus.auth import ADMIN
    from firm.bus.nats import NatsBus
    from firm.core.config import load_bus
    from firm.verify.state import state_root

    cfg = load_bus(Path(args.config))
    state = state_root(args.state_root)

    async def show() -> int:
        try:
            bus = await asyncio.wait_for(NatsBus.for_service(cfg, state, ADMIN), timeout=3)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except Exception:
            print(f"bus is not running at {cfg.url}: start it with `firm bus up`", file=sys.stderr)
            return 1
        try:
            info = await bus.js.stream_info(cfg.stream)
            s = info.state
            print(f"bus: {cfg.url}, stream {cfg.stream}")
            print(f"  messages {s.messages}, bytes {s.bytes}, sequence {s.first_seq}..{s.last_seq}")
            consumers = await bus.js.consumers_info(cfg.stream)
            print(f"  consumers {len(consumers)}")
            for c in sorted(consumers, key=lambda c: c.name):
                filt = c.config.filter_subject or ">"
                print(
                    f"    {c.name:<24} {filt:<20} pending {c.num_pending}, "
                    f"unacknowledged {c.num_ack_pending}, redelivered {c.num_redelivered}"
                )
            return 0
        finally:
            await bus.close()

    return asyncio.run(show())


def _tee_log(state: Path, name: str) -> Callable[[str], None]:
    """Print a line and append it to `<state>/logs/<name>` (follow with `tail -f`)."""
    path = state / "logs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"log: {path}  (follow it with: tail -f {path})", flush=True)

    def log(line: str) -> None:
        print(line, flush=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    return log


def _cmd_paper_replay(args: argparse.Namespace) -> int:
    """Replay a verified strategy's history through the paper path; compare with the backtest."""
    import asyncio
    import json
    from decimal import Decimal
    from pathlib import Path

    from firm.control.replay import ReplayRun
    from firm.data.store import data_root
    from firm.execution.deploy import DeploymentRefused
    from firm.verify.state import state_root

    path, _, cls = args.strategy.rpartition(":")
    state = state_root(args.state_root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else state / "replays" / f"{args.verdict}-{stamp}"
    run = ReplayRun(
        config_root=Path(args.config),
        jurisdiction=args.jurisdiction,
        market=args.market,
        venue=args.venue,
        instrument=args.instrument,
        tf=args.tf,
        strategy_path=Path(path),
        class_name=cls,
        params=json.loads(args.params) if args.params else None,
        verdict_id=args.verdict,
        data_root=data_root(args.root),
        state_root=state,
        out=out,
        lookback=args.lookback,
        start=datetime.fromisoformat(args.start).replace(tzinfo=UTC) if args.start else None,
        gbp_per_quote=Decimal(args.gbp_per_quote) if args.gbp_per_quote else None,
    )
    try:
        result = asyncio.run(run.run())
    except DeploymentRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    s = result.summary()
    q = result.quote
    print(
        f"replayed {s['bars_decided']} bars, {result.bars[0].close_time:%Y-%m-%d} to "
        f"{result.bars[-1].close_time:%Y-%m-%d}; scratch ledger and summary in {out}"
    )
    print(f"  signals:   {s['signal_mismatches']} bars where paper and backtest disagree")
    print(
        f"  trades:    backtest {s['backtest_trades']}, paper orders {s['paper_orders']} "
        f"({s['fills']} fills); {s['missed']} backtest trades without a paper order "
        f"{s['missed_reasons'] or ''}; {s['extra_orders']} extra paper orders"
    )
    print(
        f"  return:    paper {s['paper_return']:+.1%} (from {result.start_cash} {q}), "
        f"backtest {s['backtest_return']:+.1%}"
    )
    print(
        f"  exposure:  paper {s['paper_exposure']:.0%} on average, "
        f"backtest {s['backtest_exposure']:.0%}"
    )
    from firm.ledger.journal import build_journal, journal_path
    from firm.ledger.sink import ledger_path
    from firm.ledger.store import Ledger

    build_journal(Ledger(ledger_path(out)), journal_path(out))
    print("\n".join(_journal_summary(journal_path(out))))
    return 0 if s["signal_mismatches"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="firm", description="The firm: paper trading, v1.")
    parser.add_argument("--version", action="version", version=f"firm {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the version").set_defaults(func=_cmd_version)

    data = sub.add_parser("data", help="data store commands").add_subparsers(dest="data_cmd")
    syn = data.add_parser("synthetic", help="write synthetic captures (no network, no real data)")
    syn.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    syn.add_argument("--instrument", default="BTC-USD")
    syn.add_argument("--symbol", default="XBTUSD")
    syn.add_argument("--bars", type=int, default=2000)
    syn.add_argument("--seed", type=int, default=7)
    syn.set_defaults(func=_cmd_synthetic)
    bld = data.add_parser("build-dataset", help="normalize captures and freeze a dataset version")
    bld.add_argument("--root")
    bld.add_argument("--config", default="config", help="config root (data quality rule)")
    bld.add_argument(
        "--bars",
        action="append",
        metavar="PROVIDER:SYMBOL:TF",
        help="bar series to include; repeat for several venues/symbols",
    )
    bld.add_argument(
        "--trades",
        action="append",
        metavar="PROVIDER:SYMBOL",
        help="trade series to include; repeat",
    )
    bld.add_argument(
        "--derive",
        action="append",
        metavar="FROM:TO",
        help="also derive TO bars from every native FROM bar series (e.g. 1m:1h, 1d:1w); "
        "canonical grid: days 00:00 UTC, weeks Monday",
    )
    bld.add_argument("--provider", help="legacy: one provider for all --symbol")
    bld.add_argument("--symbol", action="append", help="legacy: provider symbol; repeat")
    bld.add_argument("--tf", help="legacy: timeframe for --symbol")
    bld.set_defaults(func=_cmd_build_dataset)

    sym = data.add_parser(
        "add-symbol",
        help="record how a provider (e.g. a live stream) spells an existing instrument",
    )
    sym.add_argument("--root")
    sym.add_argument("--venue", required=True)
    sym.add_argument("--instrument", required=True, help="firm ID, e.g. BTC-USD")
    sym.add_argument("--provider", required=True, help="e.g. coinbase_ws")
    sym.add_argument("--symbol", required=True, help="as that provider spells it")
    sym.set_defaults(func=_cmd_add_symbol)

    state = sub.add_parser("state", help="verifier state (read-only)").add_subparsers(
        dest="state_cmd"
    )
    show = state.add_parser("show", help="trials, out-of-sample openings, reports")
    show.add_argument("--state-root", help="default $FIRM_STATE_ROOT or ./state")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_state_show)

    led = sub.add_parser("ledger", help="the append-only ledger").add_subparsers(dest="ledger_cmd")
    for name, func, text in (
        ("show", _cmd_ledger_show, "the latest events"),
        ("verify", _cmd_ledger_verify, "check the hash chain (exit 1 if broken)"),
        ("import-state", _cmd_ledger_import, "record existing verifier state (idempotent)"),
    ):
        cmd = led.add_parser(name, help=text)
        cmd.add_argument("--state-root", help="default $FIRM_STATE_ROOT or ./state")
        if name == "show":
            cmd.add_argument("--tail", type=int, default=20)
        cmd.set_defaults(func=func)

    nt = sub.add_parser("notify", help="alerts to the owner (Telegram)").add_subparsers(
        dest="notify_cmd"
    )
    ncid = nt.add_parser("chat-id", help="list chats that wrote to the bot (to find yours)")
    ncid.set_defaults(func=_cmd_notify_chat_id)
    ntest = nt.add_parser("test", help="send a test urgent alert; prints its delay")
    ntest.add_argument("--config", default="config")
    ntest.add_argument("--state-root", help="ledger (default $FIRM_STATE_ROOT or ./state)")
    ntest.set_defaults(func=_cmd_notify_test)

    bs = sub.add_parser("bus", help="the message bus between desks (NATS)").add_subparsers(
        dest="bus_cmd"
    )
    for name, func, text in (
        ("up", _cmd_bus_up, "start the local bus in the foreground (Ctrl-C stops it)"),
        ("status", _cmd_bus_status, "the stream and its consumers"),
    ):
        cmd = bs.add_parser(name, help=text)
        cmd.add_argument("--config", default="config")
        cmd.add_argument("--state-root", help="default $FIRM_STATE_ROOT or ./state")
        cmd.set_defaults(func=func)

    jr = sub.add_parser(
        "journal", help="trade journal: one row per paper trade, rebuilt from the ledger"
    ).add_subparsers(dest="journal_cmd")
    jb = jr.add_parser("build", help="rebuild the journal from the whole ledger")
    js = jr.add_parser("show", help="summary and the latest trades")
    js.add_argument("--tail", type=int, default=20)
    je = jr.add_parser("export", help="trades decided in [--from, --to) to CSV or Parquet")
    je.add_argument("--from", dest="from_", help="YYYY-MM-DD (UTC), inclusive")
    je.add_argument("--to", help="YYYY-MM-DD (UTC), exclusive")
    je.add_argument("--format", choices=["csv", "parquet"], default="csv")
    je.add_argument("--out", required=True, help="output file (keep it out of the repo)")
    for cmd, func in ((jb, _cmd_journal_build), (js, _cmd_journal_show), (je, _cmd_journal_export)):
        cmd.add_argument("--state-root", help="default $FIRM_STATE_ROOT or ./state")
        cmd.set_defaults(func=func)

    feed = sub.add_parser("feed", help="live market data streams").add_subparsers(dest="feed_cmd")
    watch = feed.add_parser("watch", help="run a live stream for a while (needs network)")
    watch.add_argument("--provider", required=True, help="kraken_ws | coinbase_ws | binance_ws")
    watch.add_argument(
        "--symbol",
        action="append",
        required=True,
        help="as the stream spells it (BTC/USD, BTC-USD, BTCUSDT); repeat",
    )
    watch.add_argument("--seconds", type=int, default=120)
    watch.add_argument("--tf", default="1m", help="bars to print (default 1m)")
    watch.add_argument("--stale-after", type=int, default=60, help="seconds without a trade")
    watch.add_argument("--root", help="data store (default $FIRM_DATA_ROOT or ./data)")
    watch.add_argument("--no-store", action="store_true", help="don't write captures")
    watch.set_defaults(func=_cmd_feed_watch)

    paper = sub.add_parser("paper", help="paper trading").add_subparsers(dest="paper_cmd")
    prun = paper.add_parser(
        "run", help="paper-trade a verified strategy on live data (needs network; Ctrl-C stops)"
    )
    prun.add_argument("--strategy", required=True, help="PATH:CLASS (the verified file)")
    prun.add_argument(
        "--sandbox",
        action="store_true",
        help="run the strategy in the sandbox (always on for the private store)",
    )
    prun.add_argument("--params", help="JSON overrides; must equal the verified parameters")
    prun.add_argument("--verdict", required=True, help="verdict ID (v-...) of a passing report")
    prun.add_argument("--market", required=True)
    prun.add_argument("--venue", required=True)
    prun.add_argument("--instrument", required=True)
    prun.add_argument("--tf", required=True)
    prun.add_argument("--lookback", type=int, default=200, help="bars of history (default 200)")
    prun.add_argument("--hours", type=float, help="stop after this long (default: until Ctrl-C)")
    prun.add_argument("--stream-symbol", help="as the live stream spells it, if not on record")
    prun.add_argument("--no-notify", action="store_true", help="no alerts to the owner")
    prun.add_argument(
        "--no-bus", action="store_true", help="desks call each other in-process (no NATS)"
    )
    prun.add_argument(
        "--status-every",
        type=float,
        default=60,
        help="seconds between status lines (default 60; 0 = off)",
    )
    prun.add_argument("--jurisdiction", default="uk")
    prun.add_argument("--config", default="config")
    prun.add_argument("--root", help="data store")
    prun.add_argument("--state-root", help="state and ledger (default $FIRM_STATE_ROOT or ./state)")
    prun.set_defaults(func=_cmd_paper_run)

    prep = paper.add_parser(
        "replay",
        help="run a verified strategy's history through the paper path, in minutes, "
        "and compare with the backtest (exit 1 if their signals disagree)",
    )
    prep.add_argument("--strategy", required=True, help="PATH:CLASS (the verified file)")
    prep.add_argument("--params", help="JSON overrides; must equal the verified parameters")
    prep.add_argument("--verdict", required=True, help="verdict ID (v-...) of a passing report")
    prep.add_argument("--market", required=True)
    prep.add_argument("--venue", required=True)
    prep.add_argument("--instrument", required=True)
    prep.add_argument("--tf", required=True)
    prep.add_argument("--lookback", type=int, default=200, help="bars of history (default 200)")
    prep.add_argument("--start", help="first decided bar, YYYY-MM-DD (default: the hidden period)")
    prep.add_argument("--gbp-per-quote", help="FX rate (default: the paper account's opening rate)")
    prep.add_argument("--out", help="scratch folder (default <state>/replays/<verdict>-<time>)")
    prep.add_argument("--jurisdiction", default="uk")
    prep.add_argument("--config", default="config")
    prep.add_argument("--root", help="data store")
    prep.add_argument("--state-root", help="reports and the paper ledger (read only)")
    prep.set_defaults(func=_cmd_paper_replay)

    ver = sub.add_parser("verify", help="verify a strategy against a dataset version")
    ver.add_argument("--strategy", required=True, help="PATH:CLASS")
    ver.add_argument(
        "--sandbox",
        action="store_true",
        help="run the strategy in the sandbox (always on for the private store)",
    )
    ver.add_argument("--params", help="JSON object overriding default_params")
    ver.add_argument("--dataset", required=True, help="dataset version (ds-...)")
    ver.add_argument("--venue", required=True)
    ver.add_argument("--instrument", required=True)
    ver.add_argument("--tf", required=True)
    ver.add_argument("--market", required=True, help="market profile name")
    ver.add_argument("--jurisdiction", default="uk")
    ver.add_argument("--config", default="config")
    ver.add_argument("--verifier", default="default")
    ver.add_argument("--root", help="data store")
    ver.add_argument("--state-root", help="verifier state (default $FIRM_STATE_ROOT or ./state)")
    ver.add_argument("--report", help="report path (default <state>/reports/<verdict>.json)")
    ver.add_argument(
        "--as-of",
        help="only bars closed by then, ISO time with zone, e.g. 2026-11-16T00:00:00+00:00 "
        "(to verify over the window an earlier opening used)",
    )
    ver.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from firm.core.env import load_dotenv_unless_disabled

    load_dotenv_unless_disabled()  # fills only unset keys; never printed
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        if args.command in ("data", "state", "ledger", "journal", "feed", "paper", "notify", "bus"):
            parser.parse_args([args.command, "--help"])
        print(f"firm {__version__}")
        return 0
    try:
        return int(func(args))
    except (LookupError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
