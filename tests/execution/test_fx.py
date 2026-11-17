"""Live FX to GBP: direct or chained through one currency; stale means no rate."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from firm.execution.portfolio import FxBook

T = datetime(2026, 11, 17, 12, tzinfo=UTC)
D = Decimal


def test_direct_and_inverse_pairs() -> None:
    fx = FxBook(max_age=timedelta(hours=1))
    fx.update("GBP", "USD", D("1.25"), T)
    assert fx.to_gbp("USD", T) == D("0.8") and fx.to_gbp("GBP", T) == 1


def test_usdt_chains_through_usd() -> None:
    fx = FxBook(max_age=timedelta(hours=1))
    fx.update("GBP", "USD", D("1.25"), T)
    fx.update("USDT", "USD", D("0.999"), T)
    assert fx.to_gbp("USDT", T) == D("0.999") * D("0.8")


def test_any_stale_link_means_no_rate_never_a_guess() -> None:
    fx = FxBook(max_age=timedelta(hours=1))
    fx.update("GBP", "USD", D("1.25"), T - timedelta(hours=2))  # stale
    fx.update("USDT", "USD", D("1"), T)
    assert fx.to_gbp("USDT", T) is None and fx.to_gbp("USD", T) is None
    assert fx.to_gbp("EUR", T) is None  # never seen


def test_bad_prices_are_refused() -> None:
    fx = FxBook(max_age=timedelta(hours=1))
    with pytest.raises(ValueError):
        fx.update("GBP", "USD", D(0), T)
    with pytest.raises(ValueError):
        fx.update("USD", "USD", D(1), T)
