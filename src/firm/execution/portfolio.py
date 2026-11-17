"""The paper portfolio across markets, as the risk desk needs to see it.

Each market worker reports its venue account (balances, positions, marks);
`Portfolio.risk_state` turns them into one `RiskState` in GBP: equity, peak
equity, equity at the start of the UTC day, and P&L per market against the
capital that market started with. Rates to GBP come from `FxBook` (live
market prices, never typed-in numbers); if any currency has no fresh rate,
there is no risk state, and nothing new is ordered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from firm.core.domain import Side
from firm.core.risk_state import Holding, RiskState
from firm.execution.adapter import Balance, InstrumentRules, Position

BASE = "GBP"


@dataclass
class FxBook:
    """Latest live price per currency pair, chained to GBP.

    A currency converts directly (USD via GBP/USD) or through one other
    currency (USDT via USDT/USD, then GBP/USD). Every price in the chain must
    be younger than `max_age`; otherwise there is no rate, never a guess."""

    max_age: timedelta
    _pairs: dict[tuple[str, str], tuple[Decimal, datetime]] = field(default_factory=dict)

    def update(self, base: str, quote: str, price: Decimal, at: datetime) -> None:
        """A trade on base/quote at `price` (units of quote per 1 base)."""
        if price <= 0:
            raise ValueError("FX price must be positive")
        if base == quote:
            raise ValueError("a pair needs two currencies")
        self._pairs[(base, quote)] = (price, at)

    def _direct(self, a: str, b: str, now: datetime) -> Decimal | None:
        """Units of b per 1 a, from a fresh a/b or b/a price."""
        for (base, quote), (price, at) in self._pairs.items():
            if now - at > self.max_age:
                continue
            if (base, quote) == (a, b):
                return price
            if (base, quote) == (b, a):
                return Decimal(1) / price
        return None

    def to_gbp(self, currency: str, now: datetime) -> Decimal | None:
        if currency == BASE:
            return Decimal(1)
        direct = self._direct(currency, BASE, now)
        if direct is not None:
            return direct
        via = {c for pair in self._pairs for c in pair} - {currency, BASE}
        for x in sorted(via):
            leg, rest = self._direct(currency, x, now), self._direct(x, BASE, now)
            if leg is not None and rest is not None:
                return leg * rest
        return None


@dataclass(frozen=True)
class MarketAccount:
    """One market's venue account, as last reconciled, plus marks."""

    market: str
    venue: str
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    marks: dict[str, Decimal]  # instrument -> last price (quote currency)
    rules: dict[str, InstrumentRules]  # instrument -> base/quote currencies


def starting_cash(capital_gbp: Decimal, allocation_pct: float, gbp_per_unit: Decimal) -> Decimal:
    """A market's paper starting cash in its own currency, from its share of capital."""
    return (capital_gbp * Decimal(str(allocation_pct)) / 100 / gbp_per_unit).quantize(
        Decimal("0.01")
    )


@dataclass
class Portfolio:
    capital_gbp: Decimal
    allocations_pct: dict[str, float]  # market -> share of capital it started with
    _peak: Decimal | None = None
    _day: date | None = None
    _day_start: Decimal | None = None
    _market_peak: dict[str, Decimal] = field(default_factory=dict)

    def _equity(
        self, acct: MarketAccount, fx: FxBook, now: datetime
    ) -> tuple[Decimal, list[Holding]] | None:
        bases = {r.base_currency for r in acct.rules.values()}
        total = Decimal(0)
        holdings: list[Holding] = []
        for b in acct.balances:
            if b.currency in bases:
                continue  # coins are valued through their positions below
            rate = fx.to_gbp(b.currency, now)
            if rate is None:
                return None
            total += b.total * rate
        for p in acct.positions:
            if p.side is not Side.BUY or p.quantity <= 0:
                continue
            rules = acct.rules.get(p.instrument)
            mark = acct.marks.get(p.instrument)
            if rules is None or mark is None:
                return None  # can't value a holding: no state rather than a wrong one
            rate = fx.to_gbp(rules.quote_currency, now)
            if rate is None:
                return None
            total += p.quantity * mark * rate
            holdings.append(
                Holding(
                    market=acct.market,
                    venue=acct.venue,
                    instrument=p.instrument,
                    quantity=p.quantity,
                    mark_price=mark,
                    quote_currency=rules.quote_currency,
                )
            )
        return total, holdings

    def risk_state(
        self, now: datetime, accounts: list[MarketAccount], fx: FxBook
    ) -> RiskState | None:
        equity = Decimal(0)
        holdings: list[Holding] = []
        pnl: dict[str, Decimal] = {}
        rates: dict[str, Decimal] = {}
        for acct in accounts:
            valued = self._equity(acct, fx, now)
            if valued is None:
                return None
            market_equity, market_holdings = valued
            equity += market_equity
            holdings += market_holdings
            start = self.capital_gbp * Decimal(str(self.allocations_pct.get(acct.market, 0))) / 100
            pnl[acct.market] = pnl.get(acct.market, Decimal(0)) + market_equity - start
            for h in market_holdings:
                rate = fx.to_gbp(h.quote_currency, now)
                assert rate is not None  # valued above
                rates[h.quote_currency] = rate
            for b in acct.balances:
                rate = fx.to_gbp(b.currency, now)
                if rate is not None:
                    rates[b.currency] = rate
        # Capital not allocated to any running market is cash in GBP.
        running = {a.market for a in accounts}
        unallocated = 100 - sum(v for k, v in self.allocations_pct.items() if k in running)
        equity += self.capital_gbp * Decimal(str(unallocated)) / 100
        if equity <= 0:
            return None
        self._peak = equity if self._peak is None else max(self._peak, equity)
        if self._day != now.date():
            self._day, self._day_start = now.date(), equity
        assert self._day_start is not None
        for m, v in pnl.items():
            self._market_peak[m] = max(self._market_peak.get(m, Decimal(0)), v)
        return RiskState(
            as_of=now,
            equity_gbp=equity,
            peak_equity_gbp=self._peak,
            day_start_equity_gbp=self._day_start,
            holdings=tuple(holdings),
            market_pnl_gbp=pnl,
            market_peak_pnl_gbp=dict(self._market_peak),
            fx_to_gbp={c: r for c, r in rates.items() if c != BASE},
        )
