"""`firm paper run`: one market on paper against live data (the composition root).

Wires the desks in one process, in-process first (the bus comes later):

- **Gate.** The verifier's report must pass `verdict_for_deployment`: intact,
  passing, the same code and parameters, the same venue/instrument/timeframe,
  real data only.
- **Bars.** The venue's own closed bars from its REST history (the bars the
  verifier judged), fetched into the store at each close; the strategy's
  lookback is warmed up the same way, so it decides on its first live bar.
- **Trades.** The venue's WebSocket trades fill paper orders and tell when
  the feed is stale; a second stream gives the live FX rate to GBP.
- **Account.** The paper account is opened once (its share of paper capital,
  at the live FX rate) and recorded in the ledger; on a restart it is rebuilt
  from that record plus every fill since, so a restart never resets P&L.
- **Rules.** Tick, lot and minimums come from the venue's published rules.

Everything goes to the ledger (`<state>/ledger.sqlite`).
"""

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from firm.core.config import load_config, load_fee_schedule
from firm.core.domain import Fill, LedgerEventType, Side
from firm.core.events import EventSink
from firm.core.heartbeat import HeartbeatMonitor
from firm.core.timeframes import tf_seconds
from firm.data import providers
from firm.data.dataset import capture_methods
from firm.data.fetch import Fetcher, Transport, http_get
from firm.data.providers.base import NS, BarRow
from firm.data.reference import latest_instruments
from firm.data.streams.feed import Feed
from firm.execution.adapter import InstrumentRules, VenueMode
from firm.execution.deploy import DeploymentRefused, verdict_for_deployment
from firm.execution.portfolio import FxBook, Portfolio, starting_cash
from firm.execution.runner import MarketRunner
from firm.execution.simulator import PaperSimulator, SimConfig
from firm.execution.worker import Deployment, MarketWorker
from firm.ledger.sink import LedgerSink, ledger_path
from firm.ledger.store import Ledger
from firm.risk.desk import RiskDesk
from firm.verify.strategy import load_strategy

OPENED = "paper_account_opened"


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- bars


@dataclass
class RestBars:
    """New closed bars from the venue's REST history, fetched into the store.

    Polled every tick; fetches only once a bar is due (its close plus `grace`),
    and retries every `retry` while the venue hasn't published it yet."""

    fetcher: Fetcher
    provider: str
    symbol: str
    tf: str
    grace: timedelta = timedelta(seconds=10)
    retry: timedelta = timedelta(seconds=30)
    last_open_ns: int = 0
    next_due: datetime | None = None

    def fetch(self, now: datetime, bars_back: int) -> list[BarRow]:
        step = timedelta(seconds=tf_seconds(self.tf))
        before = len(self.fetcher.captures)
        self.fetcher.bars(
            self.provider, self.symbol, self.tf, start=now - step * bars_back, end=now
        )
        p = providers.get(self.provider)
        merged: dict[int, BarRow] = {}
        for cap in self.fetcher.captures[before:]:
            assert cap.meta.captured_at is not None
            for b in p.parse_bars(cap.read(), tf=self.tf, captured_at=cap.meta.captured_at):
                merged[b["open_time_ns"]] = b
        return [merged[k] for k in sorted(merged)]

    def _schedule(self, now: datetime) -> None:
        step = tf_seconds(self.tf) * NS
        next_close_ns = self.last_open_ns + 2 * step  # close of the bar after the last one
        self.next_due = max(datetime.fromtimestamp(next_close_ns / NS, tz=UTC) + self.grace, now)

    def warm_up(self, now: datetime, bars: int) -> list[BarRow]:
        history = self.fetch(now, bars + 2)
        if history:
            self.last_open_ns = history[-1]["open_time_ns"]
        self._schedule(now)
        return history[-bars:]

    async def __call__(self, now: datetime) -> list[BarRow]:
        if self.next_due is not None and now < self.next_due:
            return []
        step_ns = tf_seconds(self.tf) * NS
        since_last = (int(now.timestamp() * NS) - self.last_open_ns) // step_ns
        bars = await asyncio.to_thread(self.fetch, now, int(since_last) + 2)  # all missed bars
        new = [b for b in bars if b["open_time_ns"] > self.last_open_ns]
        if not new:
            self.next_due = now + self.retry  # not published yet
            return []
        self.last_open_ns = new[-1]["open_time_ns"]
        self._schedule(now)
        return new


# --------------------------------------------------------------------------- account


def paper_account(
    ledger: Ledger, market: str, venue: str
) -> tuple[dict[str, Decimal] | None, list[Fill]]:
    """The account's opening balances (None if never opened) and every fill since."""
    opening: dict[str, Decimal] | None = None
    fills: list[Fill] = []
    for e in ledger.read():
        p = e.payload
        if (
            e.event_type is LedgerEventType.MARKET_STATUS
            and p.get("kind") == OPENED
            and (p.get("market"), p.get("venue")) == (market, venue)
        ):
            opening = {c: Decimal(v) for c, v in p["balances"].items()}
            fills = []  # an account opened again starts over
        elif e.event_type is LedgerEventType.FILL and p.get("venue") == venue:
            fills.append(Fill.model_validate(p))
    return opening, fills


def balances_after(
    opening: dict[str, Decimal], fills: list[Fill], rules: dict[str, InstrumentRules]
) -> dict[str, Decimal]:
    out = dict(opening)
    for f in fills:
        r = rules.get(f.instrument)
        if r is None:
            raise ValueError(f"a fill on {f.instrument}, which this run doesn't trade")
        value = f.quantity * f.price
        sign = 1 if f.side is Side.BUY else -1
        out[r.base_currency] = out.get(r.base_currency, Decimal(0)) + sign * f.quantity
        out[r.quote_currency] = out.get(r.quote_currency, Decimal(0)) - sign * value
        out[f.fee_currency] = out.get(f.fee_currency, Decimal(0)) - f.fee
    if any(v < 0 for v in out.values()):
        raise ValueError(f"rebuilt balances went negative: {out}")
    return out


# --------------------------------------------------------------------------- the run


class Tee:
    """Every event to the ledger; the interesting ones to the terminal too."""

    SHOW = frozenset(
        {
            LedgerEventType.ORDER_PROPOSAL,
            LedgerEventType.RISK_DECISION,
            LedgerEventType.RISK_HALT,
            LedgerEventType.ORDER_INTENT,
            LedgerEventType.FILL,
            LedgerEventType.FEED_STATUS,
            LedgerEventType.MARKET_STATUS,
        }
    )

    def __init__(self, sink: EventSink, log: Callable[[str], None]) -> None:
        self.sink, self.log = sink, log

    def emit(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        self.sink.emit(event_type, reason, payload, event_id=event_id)
        if event_type in self.SHOW:
            self.log(f"{_now():%Y-%m-%d %H:%M:%S}  [{event_type.value}] {reason}")


@dataclass
class PaperRun:
    config_root: Path
    jurisdiction: str
    market: str
    venue: str
    instrument: str
    tf: str
    strategy_path: Path
    class_name: str
    params: dict[str, Any] | None
    verdict_id: str
    data_root: Path
    state_root: Path
    lookback: int
    seconds: float | None = None
    stream_symbol: str | None = None
    transport: Transport = http_get
    log: Callable[[str], None] = print
    stop: asyncio.Event = field(default_factory=asyncio.Event)

    def _report(self) -> bytes:
        path = self.state_root / "reports" / f"{self.verdict_id}.json"
        if not path.is_file():
            raise DeploymentRefused(f"no verifier report {path}")
        return path.read_bytes()

    async def run(self) -> int:
        cfg = load_config(self.config_root, self.jurisdiction, [self.market])
        mp = cfg.markets[self.market]
        fees = load_fee_schedule(self.config_root, mp.costs.fee_schedule_for(self.venue))
        loaded = load_strategy(self.strategy_path, self.class_name)
        params = dict(loaded.strategy.default_params) | dict(self.params or {})

        report = self._report()
        dataset_version = json.loads(report)["data"]["dataset_version"]
        verdict = verdict_for_deployment(
            report,
            code_sha256=loaded.code_sha256,
            params=params,
            venue=self.venue,
            instrument=self.instrument,
            tf=self.tf,
            capture_methods=capture_methods(self.data_root, dataset_version),
            issued_at=_now(),
        )
        rec = latest_instruments(self.data_root)[(self.venue, self.instrument)][1]
        rest_symbol = rec.provider_symbols[self.venue]
        stream_provider = f"{self.venue}_ws"
        stream_symbol = self.stream_symbol or rec.provider_symbols.get(stream_provider)
        if stream_symbol is None:
            raise ValueError(
                f"no {stream_provider} symbol for {self.instrument}: record it once with "
                f"`firm data add-symbol --venue {self.venue} --instrument {self.instrument} "
                f"--provider {stream_provider} --symbol <as the stream spells it>`, "
                "or pass --stream-symbol"
            )
        published = providers.get(self.venue).parse_rules(
            await asyncio.to_thread(
                self.transport, providers.get(self.venue).rules_url(rest_symbol)
            ),
            rest_symbol,
        )
        if not published["tradable"]:
            raise ValueError(f"{self.venue} says {rest_symbol} isn't trading now")
        rules = InstrumentRules(
            instrument=self.instrument,
            base_currency=rec.base,
            quote_currency=rec.quote,
            tick_size=Decimal(published["tick_size"]),
            lot_size=Decimal(published["lot_size"]),
            min_quantity=Decimal(published["min_quantity"]),
            min_notional=Decimal(published["min_notional"]),
        )

        ledger = Ledger(ledger_path(self.state_root))
        sink = Tee(LedgerSink(ledger), self.log)
        fx_pairs = mp.execution.fx
        max_age = min(p.max_age_seconds for p in fx_pairs)
        fx = FxBook(max_age=timedelta(seconds=max_age))
        pair_of = {(p.provider, p.symbol): (p.base, p.quote) for p in fx_pairs}
        tasks: list[asyncio.Task[None]] = []
        for provider in sorted({p.provider for p in fx_pairs}):

            def on_fx(symbol: str, trade: Any, provider: str = provider) -> None:
                base, quote = pair_of[(provider, symbol)]
                fx.update(
                    base,
                    quote,
                    Decimal(str(trade["price"])),
                    datetime.fromtimestamp(trade["time_ns"] / NS, tz=UTC),
                )

            fx_feed = Feed(
                provider,
                [p.symbol for p in fx_pairs if p.provider == provider],
                root=self.data_root,
                sink=sink,
                on_trade=on_fx,
                stale_after=timedelta(seconds=max_age),
            )
            tasks.append(asyncio.create_task(fx_feed.run(self.stop)))
        fx_names = ", ".join(p.symbol for p in fx_pairs)

        allocation = cfg.risk.markets[self.market].paper_allocation_pct
        opening, fills = paper_account(ledger, self.market, self.venue)
        if opening is None:
            deadline = _now() + timedelta(seconds=120)
            while (rate := fx.to_gbp(rec.quote, _now())) is None:
                if _now() > deadline or any(t.done() for t in tasks):
                    self.stop.set()
                    raise RuntimeError(f"no live {rec.quote}->GBP rate ({fx_names}) in 2 minutes")
                await asyncio.sleep(0.5)
            opening = {rec.quote: starting_cash(cfg.risk.paper_capital_gbp, allocation, rate)}
            sink.emit(
                LedgerEventType.MARKET_STATUS,
                f"paper account opened on {self.venue}: {opening[rec.quote]} {rec.quote} "
                f"({allocation}% of £{cfg.risk.paper_capital_gbp} at {rate:.6f} GBP/{rec.quote})",
                {
                    "kind": OPENED,
                    "market": self.market,
                    "venue": self.venue,
                    "balances": {c: str(v) for c, v in opening.items()},
                    "fx_to_gbp": str(rate),
                    "at": _now().isoformat(),
                },
                event_id=f"paper-open:{self.market}:{self.venue}",
            )
        balances = balances_after(opening, fills, {self.instrument: rules})

        sim = PaperSimulator(
            VenueMode.PAPER,
            live_trading=cfg.jurisdiction.live_trading,
            config=SimConfig(
                venue=self.venue,
                rules={self.instrument: rules},
                maker_bps=Decimal(str(fees.maker_bps)),
                taker_bps=Decimal(str(fees.taker_bps)),
                spread_bps=Decimal(str(mp.costs.spread_bps)),
                slippage_bps=Decimal(str(mp.costs.slippage_bps)),
                participation=mp.execution.participation,
                balances=balances,
            ),
        )
        desk = RiskDesk(cfg.risk, sink)
        portfolio = Portfolio(
            capital_gbp=cfg.risk.paper_capital_gbp, allocations_pct={self.market: allocation}
        )
        buffer = Decimal(str(fees.taker_bps + mp.costs.spread_bps / 2 + mp.costs.slippage_bps))
        worker: MarketWorker

        async def risk_state(now: datetime):  # type: ignore[no-untyped-def]
            return portfolio.risk_state(now, [await worker.account()], fx)

        worker = MarketWorker(
            Deployment(
                verdict=verdict,
                strategy_id=verdict.strategy_id,
                params=params,
                market=self.market,
                venue=self.venue,
                instrument=self.instrument,
                tf=self.tf,
                quote_currency=rec.quote,
                lookback_bars=self.lookback,
                rebalance_threshold_pct=mp.execution.rebalance_threshold_pct,
                cost_buffer_bps=buffer,
            ),
            loaded.strategy,
            sim,
            desk,
            risk_state,
            HeartbeatMonitor(timedelta(seconds=mp.execution.heartbeat_timeout_seconds)),
            sink=sink,
            clock=_now,
        )

        bars = RestBars(
            Fetcher(self.data_root, transport=self.transport, log=lambda _: None),
            self.venue,
            rest_symbol,
            self.tf,
        )
        history = await asyncio.to_thread(bars.warm_up, _now(), self.lookback)
        worker.warm_up(history)  # also the last price, until trades arrive

        runner = MarketRunner(
            worker=worker,
            builder=None,
            symbol=stream_symbol,
            fx=fx,
            fx_pair=None,  # FX arrives on its own streams (on_fx above)
            risk_heartbeat=desk.heartbeat,
            stale=lambda now: {},
            bar_source=bars,
            sink=sink,
        )
        trades = Feed(
            stream_provider,
            [stream_symbol],
            root=self.data_root,
            sink=sink,
            on_trade=runner.on_trade,
            on_gap=runner.on_gap,
            stale_after=timedelta(seconds=mp.data.stale_after_seconds),
        )
        runner.stale = trades.check
        sink.emit(
            LedgerEventType.MARKET_STATUS,
            f"{self.market} started: {verdict.strategy_id} on {self.venue} {self.instrument} "
            f"{self.tf}, verdict {verdict.verdict_id}; {len(history)} bars of history, "
            f"{len(fills)} past fills; balances {balances}",
            {
                "kind": "started",
                "market": self.market,
                "venue": self.venue,
                "verdict_id": verdict.verdict_id,
                "balances": {c: str(v) for c, v in balances.items()},
                "at": _now().isoformat(),
            },
        )
        tasks += [
            asyncio.create_task(trades.run(self.stop)),
            asyncio.create_task(runner.run(self.stop)),
        ]
        if self.seconds is not None:
            asyncio.get_running_loop().call_later(self.seconds, self.stop.set)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop.set)
        failed: BaseException | None = None
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            failed = next((t.exception() for t in done if t.exception()), None)
        finally:
            self.stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        final = {b.currency: str(b.total) for b in await sim.balances()}
        sink.emit(
            LedgerEventType.MARKET_STATUS,
            f"{self.market} stopped"
            + (f" by a failure: {failed!r}" if failed else "")
            + f"; balances {final}; {len(sim.fills())} fills this session",
            {
                "kind": "stopped",
                "market": self.market,
                "venue": self.venue,
                "failure": repr(failed) if failed else None,
                "balances": final,
                "at": _now().isoformat(),
            },
        )
        return 1 if failed else 0
