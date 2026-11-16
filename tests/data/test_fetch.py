"""The fetch engine with a fake network: retries, plans, captures, instruments."""

import http.client
import urllib.error
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from firm.data.dataset import build_dataset, open_dataset
from firm.data.fetch import Fetcher, ensure_instrument, http_get
from firm.data.normalize import normalize_bars, normalize_trades
from firm.data.store import iter_captures
from firm.data.synthetic import (
    binance_agg_trades_payload,
    binance_klines_payload,
    synthetic_bars,
    synthetic_trades,
    yahoo_chart_payload,
)

START = datetime(2020, 1, 1, tzinfo=UTC)


# --- http_get retry rules (transport level)


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _opener(script: list):
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        step = script[len(calls) - 1]
        if isinstance(step, Exception):
            raise step
        return _Resp(step)

    return opener, calls


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", hdrs=None, fp=None)


@pytest.mark.parametrize(
    "transient",
    [
        http.client.IncompleteRead(b""),
        urllib.error.URLError("reset"),
        TimeoutError(),
        _http(429),
        _http(503),
    ],
)
def test_http_get_retries_transient_errors(transient) -> None:
    opener, calls = _opener([transient, transient, b"ok"])
    assert http_get("https://x", opener=opener, sleep=lambda s: None) == b"ok"
    assert len(calls) == 3


@pytest.mark.parametrize("code", [400, 403, 404])
def test_http_get_does_not_retry_client_errors(code: int) -> None:
    opener, calls = _opener([_http(code), b"never"])
    with pytest.raises(urllib.error.HTTPError):
        http_get("https://x", opener=opener, sleep=lambda s: None)
    assert len(calls) == 1


def test_http_get_gives_up() -> None:
    opener, calls = _opener([http.client.IncompleteRead(b"")] * 3)
    with pytest.raises(http.client.IncompleteRead):
        http_get("https://x", attempts=3, opener=opener, sleep=lambda s: None)
    assert len(calls) == 3


# --- end to end per provider: plan -> fake transport -> captures -> dataset


def _fetcher(tmp_path, transport, at):
    return Fetcher(
        tmp_path, transport=transport, clock=lambda: at, sleep=lambda s: None, log=lambda m: None
    )


def test_binance_bars_and_trades_end_to_end(tmp_path) -> None:
    bars = {b.open_time: b for b in synthetic_bars(2_300, seed=5, start=START)}
    end = START + timedelta(days=2_300)

    def transport(url: str) -> bytes:
        q = parse_qs(urlparse(url).query)
        if "/klines" in url:
            lo, hi = int(q["startTime"][0]) // 1000, (int(q["endTime"][0]) + 1) // 1000
            return binance_klines_payload([b for t, b in sorted(bars.items()) if lo <= t < hi])
        first = int(q["fromId"][0]) if "fromId" in q else 0
        return binance_agg_trades_payload(trades[first : first + 1000])

    trades = synthetic_trades(2_500, seed=6, start=START)
    trades = [
        t.__class__(t.time_us, k, t.price, t.size, t.taker_side) for k, t in enumerate(trades)
    ]  # ids 0.. so the cursor maps to pages
    ensure_instrument(
        tmp_path,
        provider="binance",
        symbol="BTCUSDT",
        venue="binance",
        instrument="BTC-USDT",
        asset_class="crypto",
        listed_at=START,
    )
    f = _fetcher(tmp_path, transport, end + timedelta(hours=1))
    assert f.bars("binance", "BTCUSDT", "1d", start=START, end=end) == 3
    assert f.trades("binance", "BTCUSDT", pages=5) == 3  # 1000 + 1000 + 500, then no cursor
    files = normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf="1d")
    files += normalize_trades(tmp_path, provider="binance", provider_symbol="BTCUSDT")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    df = ds.bars("binance", "BTC-USDT", "1d", as_of=end)
    assert len(df) == 2_300
    assert df["open_time"].diff().dropna().eq(pd.Timedelta(days=1)).all()
    assert len(ds.trades("binance", "BTC-USDT", as_of=end + timedelta(days=99))) == 2_500


def test_yahoo_equity_end_to_end_with_splits(tmp_path) -> None:
    bars = synthetic_bars(500, seed=9, start=START, start_price=300.0)
    split = bars[250].open_time

    def transport(url: str) -> bytes:
        return yahoo_chart_payload(bars, splits=[(split, 4, 1)])

    ensure_instrument(
        tmp_path,
        provider="yahoo",
        symbol="SYNTH",
        venue="xnas",
        instrument="SYNTH.XNAS",
        asset_class="equity",
        listed_at=START,
        quote="USD",
    )
    end = START + timedelta(days=500)
    f = _fetcher(tmp_path, transport, end + timedelta(hours=1))
    assert f.bars("yahoo", "SYNTH", "1d", start=START, end=end) == 1
    (cap,) = list(iter_captures(tmp_path, provider="yahoo"))
    assert cap.meta.price_adjustment == "split_adjusted"  # the raw bytes, honestly labelled
    files = normalize_bars(tmp_path, provider="yahoo", provider_symbol="SYNTH", tf="1d")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    df = ds.bars("xnas", "SYNTH.XNAS", "1d", as_of=end)
    assert df["close"].tolist() == pytest.approx([b.close for b in bars], rel=1e-12)


# --- instrument records


def test_instrument_needs_listing_date_first_time(tmp_path) -> None:
    with pytest.raises(ValueError, match="listing date"):
        ensure_instrument(
            tmp_path,
            provider="binance",
            symbol="BTCUSDT",
            venue="binance",
            instrument="BTC-USDT",
            asset_class="crypto",
            listed_at=None,
        )


def test_equity_needs_quote_currency(tmp_path) -> None:
    with pytest.raises(ValueError, match="quote currency"):
        ensure_instrument(
            tmp_path,
            provider="yahoo",
            symbol="NVDA",
            venue="xnas",
            instrument="NVDA.XNAS",
            asset_class="equity",
            listed_at=START,
        )


def test_second_provider_adds_its_symbol_and_conflicts_are_refused(tmp_path) -> None:
    kw = dict(venue="coinbase", instrument="BTC-USD", asset_class="crypto", listed_at=START)
    ensure_instrument(tmp_path, provider="coinbase", symbol="BTC-USD", **kw)
    rec = ensure_instrument(tmp_path, provider="other", symbol="XBT-USD", **kw)
    assert rec.provider_symbols == {"coinbase": "BTC-USD", "other": "XBT-USD"}
    with pytest.raises(ValueError, match="already maps"):
        ensure_instrument(tmp_path, provider="coinbase", symbol="BTCUSD", **kw)
