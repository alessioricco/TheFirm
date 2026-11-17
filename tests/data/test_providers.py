"""The provider layer: registry, each source's exact format, request plans.

Synthetic payloads only (public repo, no network).
"""

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from firm.data import providers
from firm.data.providers import Provider, ProviderError, register
from firm.data.providers.yahoo import split_factors
from firm.data.synthetic import (
    binance_agg_trades_payload,
    binance_klines_payload,
    coinbase_candles_payload,
    kraken_ohlc_payload,
    synthetic_bars,
    synthetic_trades,
    yahoo_chart_payload,
)

START = datetime(2020, 1, 1, tzinfo=UTC)
FAR = datetime(2100, 1, 1, tzinfo=UTC)
NS = 1_000_000_000


# --- registry


def test_builtin_providers_are_registered() -> None:
    assert providers.available() == ["binance", "coinbase", "kraken", "yahoo"]
    for name in providers.available():
        p = providers.get(name)
        assert isinstance(p, Provider) and p.name == name
        assert p.licence and p.description and p.min_interval_s > 0


def test_unknown_provider_names_the_alternatives() -> None:
    with pytest.raises(ValueError, match="available"):
        providers.get("nope")


def test_a_name_can_only_be_registered_once() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register(providers.get("kraken"))


def test_capabilities_are_enforced() -> None:
    with pytest.raises(ProviderError, match="4h"):
        providers.get("coinbase").bar_requests("BTC-USD", "4h", start=None, end=FAR)
    with pytest.raises(ProviderError, match="no trades"):
        providers.get("yahoo").supports("trades")


# --- every crypto provider: same bars in, same rows out


PAYLOADS = {
    "kraken": lambda bars: kraken_ohlc_payload(bars),
    "coinbase": lambda bars: coinbase_candles_payload(bars),
    "binance": lambda bars: binance_klines_payload(bars, "1d"),
}


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_crypto_parsers_agree_and_drop_the_forming_bar(name: str) -> None:
    bars = synthetic_bars(6, seed=3, start=START)
    forming = START + timedelta(days=5, hours=12)  # the 6th bar is still open
    rows = providers.get(name).parse_bars(PAYLOADS[name](bars), tf="1d", captured_at=forming)
    assert [r["open_time_ns"] for r in rows] == [b.open_time * NS for b in bars[:5]]
    assert [(r["open"], r["high"], r["low"], r["close"]) for r in rows] == [
        (b.open, b.high, b.low, b.close) for b in bars[:5]
    ]
    assert all(r["close_time_ns"] - r["open_time_ns"] == 86_400 * NS for r in rows)


def test_binance_error_payload_raises() -> None:
    with pytest.raises(ProviderError, match="-1121"):
        providers.get("binance").parse_bars(
            b'{"code":-1121,"msg":"Invalid symbol."}', tf="1d", captured_at=FAR
        )


def test_binance_trades_taker_side_and_cursor() -> None:
    trades = synthetic_trades(50, seed=4, start=START)
    payload = binance_agg_trades_payload(trades)
    b = providers.get("binance")
    rows = b.parse_trades(payload, captured_at=FAR)
    assert [r["taker_side"] for r in rows] == [t.taker_side for t in trades]
    assert [r["trade_id"] for r in rows] == [str(t.trade_id) for t in trades]
    assert b.next_trade_cursor(payload) is None  # short page: caught up
    full = synthetic_trades(1000, seed=5, start=START)
    assert b.next_trade_cursor(binance_agg_trades_payload(full)) == str(full[-1].trade_id + 1)
    assert b.next_trade_cursor(b"[]") is None


# --- Yahoo: split-adjusted on the wire, true prices in the store


def test_yahoo_undoes_split_adjustment() -> None:
    bars = synthetic_bars(40, seed=8, start=START, start_price=400.0)
    split_1 = bars[10].open_time  # 4:1 split before bar 10
    split_2 = bars[30].open_time  # 10:1 split before bar 30
    payload = yahoo_chart_payload(bars, splits=[(split_1, 4, 1), (split_2, 10, 1)])
    raw = json.loads(payload)["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    assert raw[0] == pytest.approx(bars[0].close / 40)  # what Yahoo serves: adjusted
    rows = providers.get("yahoo").parse_bars(payload, tf="1d", captured_at=FAR)
    assert [r["close"] for r in rows] == pytest.approx([b.close for b in bars], rel=1e-12)
    # Yahoo volumes are whole (adjusted) shares, so they round-trip to share precision
    assert [r["volume"] for r in rows] == pytest.approx([b.volume for b in bars], abs=1.0)


def test_split_factors_multiply_across_later_splits() -> None:
    splits = {
        "a": {"date": 100, "numerator": 4, "denominator": 1},
        "b": {"date": 200, "numerator": 10, "denominator": 1},
    }
    assert split_factors([50, 150, 250], splits) == [40.0, 10.0, 1.0]


def test_yahoo_skips_empty_rows_and_drops_todays_bar() -> None:
    bars = synthetic_bars(5, seed=2, start=START)
    doc = json.loads(yahoo_chart_payload(bars))
    q = doc["chart"]["result"][0]["indicators"]["quote"][0]
    for k in ("open", "high", "low", "close"):
        q[k][1] = None  # a halted day
    rows = providers.get("yahoo").parse_bars(
        json.dumps(doc).encode(), tf="1d", captured_at=START + timedelta(days=4, hours=1)
    )
    assert [r["open_time_ns"] // NS for r in rows] == [bars[i].open_time for i in (0, 2, 3)]


def test_yahoo_error_raises() -> None:
    err = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data"}}}'
    with pytest.raises(ProviderError, match="Not Found"):
        providers.get("yahoo").parse_bars(err, tf="1d", captured_at=FAR)


# --- request plans


@pytest.mark.parametrize(
    ("name", "symbol", "per_page"), [("coinbase", "BTC-USD", 300), ("binance", "BTCUSDT", 1000)]
)
def test_paged_plans_are_contiguous_and_cover_the_range(name, symbol, per_page) -> None:
    end = START + timedelta(days=2_500)
    plan = providers.get(name).bar_requests(symbol, "1d", start=START, end=end)
    assert len(plan) == -(-2_500 // per_page)
    spans = []
    for req in plan:
        q = parse_qs(urlparse(req.url).query)
        if name == "binance":
            spans.append((int(q["startTime"][0]), int(q["endTime"][0]) + 1))
        else:
            spans.append(
                (
                    int(pd.Timestamp(q["start"][0]).value // 1_000_000),
                    int(pd.Timestamp(q["end"][0]).value // 1_000_000),
                )
            )
    assert spans[0][0] == int(START.timestamp() * 1000)
    assert spans[-1][1] == int(end.timestamp() * 1000)
    assert all(a[1] == b[0] for a, b in pairwise(spans))


def test_single_request_sources() -> None:
    assert len(providers.get("kraken").bar_requests("XBTUSD", "1d", start=START, end=FAR)) == 1
    end = START + timedelta(days=31)
    (req,) = providers.get("yahoo").bar_requests("NVDA", "1d", start=START, end=end)
    q = parse_qs(urlparse(req.url).query)
    assert q["interval"] == ["1d"] and q["events"] == ["div,split"]
    assert int(q["period1"][0]) == int(START.timestamp())
    # Yahoo only returns split events inside the window, and adjusts relative to
    # today: the plan must run to the far future to see every later split.
    assert int(q["period2"][0]) >= int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())


def test_instrument_rules_in_each_venues_published_format() -> None:
    import json as _json

    from firm.data import providers as _p

    kraken = _p.get("kraken")
    assert kraken.rules_url("XBTUSD").endswith("/AssetPairs?pair=XBTUSD")
    k = kraken.parse_rules(
        _json.dumps(
            {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "altname": "XBTUSD",
                        "tick_size": "0.1",
                        "lot_decimals": 8,
                        "ordermin": "0.00006",
                        "costmin": "0.5",
                        "status": "online",
                    }
                },
            }
        ).encode(),
        "XBTUSD",
    )
    assert k == {
        "tick_size": "0.1",
        "lot_size": "1e-8",
        "min_quantity": "0.00006",
        "min_notional": "0.5",
        "tradable": True,
    }

    coinbase = _p.get("coinbase")
    c = coinbase.parse_rules(
        _json.dumps(
            {
                "id": "BTC-USD",
                "quote_increment": "0.01",
                "base_increment": "0.00000001",
                "min_market_funds": "1",
                "status": "online",
                "trading_disabled": True,
            }
        ).encode(),
        "BTC-USD",
    )
    assert c["lot_size"] == "0.00000001" and c["tradable"] is False  # trading disabled
    with pytest.raises(_p.ProviderError):
        coinbase.parse_rules(b'{"message":"NotFound"}', "NOPE-USD")
    with pytest.raises(_p.ProviderError, match="no instrument rules"):
        _p.get("yahoo").rules_url("AAPL")
