"""What the risk desk is told about the portfolio when it decides.

Execution builds it (from venue accounts and live FX); risk only reads it.
It lives in core because it crosses the desk boundary (golden rule 12).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, Field

from firm.core.base import CurrencyCode, FirmModel, Id

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class Holding(FirmModel):
    market: Id
    venue: Id
    instrument: Id
    quantity: NonNegativeDecimal
    mark_price: PositiveDecimal
    quote_currency: CurrencyCode


class RiskState(FirmModel):
    """What the desk knows when it decides. Supplied by the portfolio/execution side."""

    as_of: AwareDatetime
    equity_gbp: PositiveDecimal  # cash + marked holdings
    peak_equity_gbp: PositiveDecimal
    day_start_equity_gbp: PositiveDecimal  # equity at 00:00 UTC
    holdings: tuple[Holding, ...]
    market_pnl_gbp: dict[Id, Decimal]  # cumulative P&L per market
    market_peak_pnl_gbp: dict[Id, Decimal]
    fx_to_gbp: dict[CurrencyCode, PositiveDecimal]  # 1 unit of currency in GBP


__all__ = ["Holding", "RiskState"]
