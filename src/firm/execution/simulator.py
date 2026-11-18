"""The paper simulator: the first `VenueAdapter` (paper mode only).

Fills orders against market data with the venue's fees, the spread,
slippage and partial fills, and never at a price that wasn't available:

- An order fills only on market data that arrives AFTER it was placed
  (trades with a later time; bars that open at or after the order).
- Market orders (and triggered stops) take the next trade price or bar open,
  moved against us by half the spread plus slippage, rounded to the tick
  against us; on bars the price is clipped to the bar's range. Taker fee.
- Resting limit orders fill at their limit when the market trades through
  it. Maker fee. A triggered stop-limit becomes a limit.
- Stops rest at the venue and trigger when price crosses them; a gap fills
  at the gap price, never at the better stop price.
- Each market event lets our orders take at most `participation` of its
  volume (shared across our orders, in submission order): partial fills.
- IOC: fill what the first eligible event allows, cancel the rest. FOK: all
  or nothing on that event. DAY: expires at the next 00:00 UTC.
- Before anything is accepted: venue, instrument, tick, lot, minimum
  quantity and value, and funds (cash for open buys and coins for open sells
  are reserved, so open orders can't over-commit the account).
- Every fill carries its order's verdict ID and risk decision ID.

Fees, spread, slippage and participation come from configuration (the
venue's fee schedule and the market profile), passed in as `SimConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Annotated

from pydantic import AwareDatetime, Field

from firm.core.base import CurrencyCode, FirmModel, Id
from firm.core.domain import (
    ORDER_TRANSITIONS,
    TERMINAL_STATUSES,
    Fill,
    OrderIntent,
    OrderStatus,
    OrderType,
    OrderUpdate,
    Side,
    TimeInForce,
)
from firm.execution.adapter import (
    Balance,
    InstrumentRules,
    Position,
    RateLimitPolicy,
    ReconciliationSnapshot,
    VenueAdapter,
    VenueCapabilities,
    VenueMode,
)
from firm.execution.ratelimit import TokenBucket

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
BPS = Decimal(10_000)
RATE_LIMIT = RateLimitPolicy(max_requests=1_000, per_seconds=1.0)


class SimConfig(FirmModel):
    venue: Id
    rules: dict[Id, InstrumentRules]
    maker_bps: NonNegativeDecimal
    taker_bps: NonNegativeDecimal
    spread_bps: NonNegativeDecimal
    slippage_bps: NonNegativeDecimal
    participation: Annotated[Decimal, Field(gt=0, le=1)]
    balances: dict[CurrencyCode, NonNegativeDecimal]


class MarketTrade(FirmModel):
    instrument: Id
    time: AwareDatetime
    price: PositiveDecimal
    size: PositiveDecimal


class MarketBar(FirmModel):
    instrument: Id
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal


MarketEvent = MarketTrade | MarketBar


@dataclass
class _Order:
    intent: OrderIntent
    venue_order_id: str
    accepted_at: datetime
    status: OrderStatus
    filled: Decimal = Decimal(0)
    notional: Decimal = Decimal(0)
    reserve_unit: Decimal = Decimal(0)  # quote per unit reserved (buys) / 1 (sells, in base)
    triggered: bool = False
    seen_event: bool = False  # IOC/FOK: only the first eligible event counts
    fills: int = 0
    history: list[OrderUpdate] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.intent.quantity - self.filled

    @property
    def open(self) -> bool:
        return self.status not in TERMINAL_STATUSES


def _round(x: Decimal, step: Decimal, *, up: bool) -> Decimal:
    q = (x / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return q * step


class PaperSimulator(VenueAdapter):
    """Paper mode only. It has no sandbox or live counterpart."""

    def __init__(
        self,
        mode: VenueMode,
        *,
        live_trading: bool,
        config: SimConfig | None = None,
        limiter: TokenBucket | None = None,
    ) -> None:
        super().__init__(mode, live_trading=live_trading)  # refuses live first
        if config is None:
            raise ValueError("the paper simulator needs a config (SimConfig: fees, costs, rules)")
        self.config = config
        self._limiter = limiter or TokenBucket(RATE_LIMIT)
        self._orders: dict[str, _Order] = {}
        self._fills: list[Fill] = []
        self._cash: dict[str, Decimal] = dict(config.balances)
        self._cost_basis: dict[str, Decimal] = {}
        self._last_price: dict[str, Decimal] = {}
        self._now: datetime | None = None
        self._seq = 0

    def capabilities(self) -> VenueCapabilities:
        venue = getattr(self, "config", None)
        return VenueCapabilities(
            venue=venue.venue if venue is not None else "simulator",
            modes=frozenset({VenueMode.PAPER}),
            order_types=frozenset(OrderType),
            time_in_force=frozenset(TimeInForce),
            venue_side_stops=True,
            partial_fills=True,
            rate_limit=RATE_LIMIT,
        )

    # ------------------------------------------------------------------ helpers

    def _update(self, o: _Order, status: OrderStatus, reason: str, at: datetime) -> OrderUpdate:
        if o.history and status != o.status and status not in ORDER_TRANSITIONS[o.status]:
            raise AssertionError(f"illegal transition {o.status} -> {status}")
        o.status = status
        up = OrderUpdate(
            client_order_id=o.intent.client_order_id,
            venue_order_id=o.venue_order_id,
            venue=self.config.venue,
            status=status,
            filled_quantity=o.filled,
            average_fill_price=(o.notional / o.filled) if o.filled > 0 else None,
            reason=reason,
            updated_at=at,
        )
        o.history.append(up)
        return up

    def _reserved(self, currency: str) -> Decimal:
        total = Decimal(0)
        for o in self._orders.values():
            if not o.open:
                continue
            rules = self.config.rules[o.intent.instrument]
            if o.intent.side is Side.BUY and currency == rules.quote_currency:
                total += o.remaining * o.reserve_unit
            if o.intent.side is Side.SELL and currency == rules.base_currency:
                total += o.remaining
        return total

    def _available(self, currency: str) -> Decimal:
        return self._cash.get(currency, Decimal(0)) - self._reserved(currency)

    def taker_buffer_bps(self) -> Decimal:
        """What a market buy reserves above the last price, in bps: half the
        spread plus slippage, then the taker fee on top (compounded, as submit
        reserves it). Size buys with this, or a 100% target never fits the cash."""
        return ((1 + self._costs()) * (1 + self.config.taker_bps / BPS) - 1) * BPS

    def _costs(self) -> Decimal:
        return (self.config.spread_bps / 2 + self.config.slippage_bps) / BPS

    def _reject(self, intent: OrderIntent, reason: str) -> OrderUpdate:
        o = _Order(
            intent=intent,
            venue_order_id=self._next_id(),
            accepted_at=intent.created_at,
            status=OrderStatus.PENDING_NEW,
        )
        self._orders[intent.client_order_id] = o
        return self._update(o, OrderStatus.REJECTED, reason, intent.created_at)

    def _next_id(self) -> str:
        self._seq += 1
        return f"sim-{self._seq}"

    # ------------------------------------------------------------------ orders

    async def instrument_rules(self, instrument: str) -> InstrumentRules:
        await self._limiter.acquire()
        try:
            return self.config.rules[instrument]
        except KeyError:
            raise LookupError(f"unknown instrument {instrument}") from None

    async def submit(self, intent: OrderIntent) -> OrderUpdate:
        await self._limiter.acquire()
        existing = self._orders.get(intent.client_order_id)
        if existing is not None:
            if existing.intent != intent:
                raise ValueError(
                    f"client order id {intent.client_order_id} already used by a different order"
                )
            return existing.history[-1]  # idempotent
        if intent.venue != self.config.venue:
            return self._reject(intent, f"wrong venue {intent.venue}; this is {self.config.venue}")
        rules = self.config.rules.get(intent.instrument)
        if rules is None or not rules.tradable:
            return self._reject(intent, f"instrument {intent.instrument} not tradable here")
        q = intent.quantity
        if q < rules.min_quantity:
            return self._reject(intent, f"below minimum quantity {rules.min_quantity}")
        if q % rules.lot_size != 0:
            return self._reject(intent, f"quantity not a multiple of lot size {rules.lot_size}")
        for price in (intent.limit_price, intent.stop_price):
            if price is not None and price % rules.tick_size != 0:
                return self._reject(intent, f"price {price} not on tick size {rules.tick_size}")
        ref = intent.limit_price or intent.stop_price or self._last_price.get(intent.instrument)
        if ref is None:
            return self._reject(intent, "no market price yet to value the order")
        if q * ref < rules.min_notional:
            return self._reject(intent, f"below minimum value {rules.min_notional}")
        resting_limit = intent.order_type is OrderType.LIMIT
        fee = (self.config.maker_bps if resting_limit else self.config.taker_bps) / BPS
        unit = ref * (1 + (Decimal(0) if resting_limit else self._costs())) * (1 + fee)
        if intent.side is Side.BUY:
            if q * unit > self._available(rules.quote_currency):
                return self._reject(intent, f"insufficient {rules.quote_currency} for this buy")
        elif q > self._available(rules.base_currency):
            return self._reject(intent, f"insufficient {rules.base_currency} to sell")
        o = _Order(
            intent=intent,
            venue_order_id=self._next_id(),
            accepted_at=intent.created_at,
            status=OrderStatus.PENDING_NEW,
            reserve_unit=unit if intent.side is Side.BUY else Decimal(1),
        )
        self._orders[intent.client_order_id] = o
        self._now = max(self._now or intent.created_at, intent.created_at)
        return self._update(o, OrderStatus.ACCEPTED, "accepted", intent.created_at)

    async def cancel(self, client_order_id: str) -> OrderUpdate:
        await self._limiter.acquire()
        o = self._get(client_order_id)
        if not o.open:
            return o.history[-1]
        return self._update(
            o, OrderStatus.CANCELLED, "cancelled by request", self._now or o.accepted_at
        )

    def _get(self, client_order_id: str) -> _Order:
        try:
            return self._orders[client_order_id]
        except KeyError:
            raise LookupError(f"unknown order {client_order_id}") from None

    async def order_status(self, client_order_id: str) -> OrderUpdate:
        await self._limiter.acquire()
        return self._get(client_order_id).history[-1]

    async def open_orders(self) -> tuple[OrderUpdate, ...]:
        await self._limiter.acquire()
        return tuple(o.history[-1] for o in self._orders.values() if o.open)

    async def positions(self) -> tuple[Position, ...]:
        await self._limiter.acquire()
        return self._positions()

    def _positions(self) -> tuple[Position, ...]:
        out = []
        for inst, rules in sorted(self.config.rules.items()):
            qty = self._cash.get(rules.base_currency, Decimal(0))
            if qty > 0:
                basis = self._cost_basis.get(inst)
                out.append(
                    Position(
                        instrument=inst,
                        side=Side.BUY,
                        quantity=qty,
                        average_price=basis / qty if basis else None,
                    )
                )
        return tuple(out)

    async def balances(self) -> tuple[Balance, ...]:
        await self._limiter.acquire()
        return self._balances()

    def _balances(self) -> tuple[Balance, ...]:
        return tuple(
            Balance(currency=c, total=t, available=max(Decimal(0), t - self._reserved(c)))
            for c, t in sorted(self._cash.items())
        )

    async def reconcile(self) -> ReconciliationSnapshot:
        await self._limiter.acquire()
        taken = self._now or min((o.accepted_at for o in self._orders.values()), default=None)
        return ReconciliationSnapshot(
            venue=self.config.venue,
            taken_at=taken or datetime.now().astimezone(),
            positions=self._positions(),
            balances=self._balances(),
            open_orders=tuple(o.history[-1] for o in self._orders.values() if o.open),
        )

    def fills(self) -> list[Fill]:
        return list(self._fills)

    def history(self, client_order_id: str) -> list[OrderUpdate]:
        return list(self._get(client_order_id).history)

    # ------------------------------------------------------------------ market data

    def on_market(self, event: MarketEvent) -> list[OrderUpdate | Fill]:
        """Process one market event; returns the order updates and fills it caused."""
        out: list[OrderUpdate | Fill] = []
        at = event.time if isinstance(event, MarketTrade) else event.open_time
        self._now = max(self._now or at, at)
        self._expire_day_orders(at, out)
        budget = self.config.participation * (
            event.size if isinstance(event, MarketTrade) else event.volume
        )
        for o in list(self._orders.values()):
            if not o.open or o.intent.instrument != event.instrument:
                continue
            if not self._eligible(o, event):
                continue
            budget = self._work(o, event, budget, out)
        self._last_price[event.instrument] = (
            event.price if isinstance(event, MarketTrade) else event.close
        )
        return out

    def _eligible(self, o: _Order, e: MarketEvent) -> bool:
        if isinstance(e, MarketTrade):
            return e.time > o.accepted_at
        return e.open_time >= o.accepted_at

    def _expire_day_orders(self, at: datetime, out: list[OrderUpdate | Fill]) -> None:
        for o in self._orders.values():
            if o.open and o.intent.time_in_force is TimeInForce.DAY:
                end = (o.accepted_at + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if at >= end:
                    out.append(self._update(o, OrderStatus.EXPIRED, "day order expired", end))

    def _work(
        self, o: _Order, e: MarketEvent, budget: Decimal, out: list[OrderUpdate | Fill]
    ) -> Decimal:
        i, rules = o.intent, self.config.rules[o.intent.instrument]
        buy = i.side is Side.BUY
        lo, hi, first = (
            (e.price, e.price, e.price) if isinstance(e, MarketTrade) else (e.low, e.high, e.open)
        )
        price: Decimal | None = None
        maker = False
        if i.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and not o.triggered:
            stop = i.stop_price
            assert stop is not None
            if (buy and hi >= stop) or (not buy and lo <= stop):
                o.triggered = True
            else:
                return budget
        if i.order_type is OrderType.MARKET or (i.order_type is OrderType.STOP and o.triggered):
            ref = first
            if i.order_type is OrderType.STOP:
                stop = i.stop_price
                assert stop is not None
                ref = max(stop, first) if buy else min(stop, first)
            raw = ref * (1 + self._costs()) if buy else ref * (1 - self._costs())
            price = _round(raw, rules.tick_size, up=buy)
            if isinstance(e, MarketBar):  # a bar has a real range: never fill outside it
                price = min(max(price, lo), hi)
        else:  # LIMIT, or triggered STOP_LIMIT
            limit = i.limit_price
            assert limit is not None
            if (buy and lo <= limit) or (not buy and hi >= limit):
                price, maker = limit, i.order_type is OrderType.LIMIT
        tif = i.time_in_force
        if price is None:
            return self._after_event(o, out, budget)
        qty = min(o.remaining, budget)
        qty = (qty / rules.lot_size).to_integral_value(rounding=ROUND_DOWN) * rules.lot_size
        if tif is TimeInForce.FOK and qty < o.remaining:
            o.seen_event = True
            out.append(
                self._update(
                    o,
                    OrderStatus.CANCELLED,
                    "fill-or-kill: not fully fillable",
                    self._now or o.accepted_at,
                )
            )
            return budget
        fee_rate = (self.config.maker_bps if maker else self.config.taker_bps) / BPS
        if buy and qty > 0:
            cash = self._cash.get(rules.quote_currency, Decimal(0)) - (
                self._reserved(rules.quote_currency) - o.remaining * o.reserve_unit
            )
            affordable = (cash / (price * (1 + fee_rate)) / rules.lot_size).to_integral_value(
                rounding=ROUND_DOWN
            ) * rules.lot_size
            if affordable < qty:
                qty = max(Decimal(0), affordable)
                if qty == 0:
                    out.append(
                        self._update(
                            o,
                            OrderStatus.CANCELLED,
                            "insufficient funds at fill time",
                            self._now or o.accepted_at,
                        )
                    )
                    return budget
        if qty > 0 and (qty >= rules.min_quantity or qty == o.remaining):
            self._apply_fill(o, rules, qty, price, fee_rate, out)
            budget -= qty
        return self._after_event(o, out, budget)

    def _after_event(self, o: _Order, out: list[OrderUpdate | Fill], budget: Decimal) -> Decimal:
        if o.open and o.intent.time_in_force is TimeInForce.IOC:
            out.append(
                self._update(
                    o,
                    OrderStatus.CANCELLED,
                    "immediate-or-cancel: remainder cancelled",
                    self._now or o.accepted_at,
                )
            )
        o.seen_event = True
        return budget

    def _apply_fill(
        self,
        o: _Order,
        rules: InstrumentRules,
        qty: Decimal,
        price: Decimal,
        fee_rate: Decimal,
        out: list[OrderUpdate | Fill],
    ) -> None:
        i = o.intent
        value = qty * price
        fee = value * fee_rate
        base, quote = rules.base_currency, rules.quote_currency
        if i.side is Side.BUY:
            self._cash[quote] = self._cash.get(quote, Decimal(0)) - value - fee
            self._cash[base] = self._cash.get(base, Decimal(0)) + qty
            self._cost_basis[i.instrument] = self._cost_basis.get(i.instrument, Decimal(0)) + value
        else:
            held = self._cash.get(base, Decimal(0))
            basis = self._cost_basis.get(i.instrument, Decimal(0))
            if held > 0:
                self._cost_basis[i.instrument] = basis * (held - qty) / held
            self._cash[base] = held - qty
            self._cash[quote] = self._cash.get(quote, Decimal(0)) + value - fee
        o.filled += qty
        o.notional += value
        o.fills += 1
        at = self._now or o.accepted_at
        fill = Fill(
            fill_id=f"{i.client_order_id}-f{o.fills}",  # unique across restarts
            client_order_id=i.client_order_id,
            intent_id=i.intent_id,
            verdict_id=i.verdict_id,
            risk_decision_id=i.risk_decision_id,
            venue=self.config.venue,
            instrument=i.instrument,
            side=i.side,
            quantity=qty,
            price=price,
            fee=fee,
            fee_currency=quote,
            filled_at=at,
        )
        self._fills.append(fill)
        out.append(fill)
        status = OrderStatus.FILLED if o.remaining == 0 else OrderStatus.PARTIALLY_FILLED
        out.append(self._update(o, status, f"filled {qty} at {price}", at))
