"""The owner-run downloader, with a fake network: retries, paging, cursors.

Nothing here touches the network (the suite blocks sockets); `urlopen` and
`fetch` are replaced, and payloads are synthetic in the providers' formats.
"""

import http.client
import importlib.util
import itertools
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars
from firm.data.store import iter_captures
from firm.data.synthetic import (
    coinbase_candles_payload,
    kraken_trades_payload,
    synthetic_bars,
    synthetic_trades,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_crypto.py"
START = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def dl(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("download_crypto", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)  # no real waiting
    return mod


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _scripted_urlopen(script: list):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        step = script[len(calls) - 1]
        if isinstance(step, Exception):
            raise step
        return FakeResponse(step)

    return urlopen, calls


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", hdrs=None, fp=None)


# --- fetch: retry rules


@pytest.mark.parametrize(
    "transient",
    [
        http.client.IncompleteRead(b""),
        urllib.error.URLError("reset"),
        TimeoutError(),
        _http_error(429),
        _http_error(503),
    ],
)
def test_fetch_retries_transient_errors(dl, monkeypatch, transient) -> None:
    urlopen, calls = _scripted_urlopen([transient, transient, b"ok"])
    monkeypatch.setattr(dl.urllib.request, "urlopen", urlopen)
    body, at = dl.fetch("https://api.example/x")
    assert body == b"ok" and len(calls) == 3 and at.tzinfo is not None


@pytest.mark.parametrize("code", [400, 403, 404])
def test_fetch_does_not_retry_client_errors(dl, monkeypatch, code: int) -> None:
    urlopen, calls = _scripted_urlopen([_http_error(code), b"never"])
    monkeypatch.setattr(dl.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        dl.fetch("https://api.example/x")
    assert len(calls) == 1


def test_fetch_gives_up_after_attempts(dl, monkeypatch) -> None:
    urlopen, calls = _scripted_urlopen([http.client.IncompleteRead(b"")] * 3)
    monkeypatch.setattr(dl.urllib.request, "urlopen", urlopen)
    with pytest.raises(http.client.IncompleteRead):
        dl.fetch("https://api.example/x", attempts=3)
    assert len(calls) == 3


# --- Coinbase: 300-candle windows cover the range with no gaps


def test_coinbase_pages_cover_the_range_without_gaps(dl, monkeypatch, tmp_path) -> None:
    bars = {b.open_time: b for b in synthetic_bars(1_000, seed=5, start=START)}
    seen = []

    def fake_fetch(url: str):
        q = parse_qs(urlparse(url).query)
        lo = int(pd.Timestamp(q["start"][0]).timestamp())
        hi = int(pd.Timestamp(q["end"][0]).timestamp())
        seen.append((lo, hi))
        window = [b for t, b in sorted(bars.items()) if lo <= t < hi]
        return coinbase_candles_payload(window), datetime.fromtimestamp(hi, tz=UTC)

    monkeypatch.setattr(dl, "fetch", fake_fetch)
    until = START + timedelta(days=1_000)
    assert (
        dl.main(
            [
                "--venue",
                "coinbase",
                "--instrument",
                "BTC-USD",
                "--symbol",
                "BTC-USD",
                "--tf",
                "1d",
                "--since",
                "2020-01-01",
                "--until",
                until.date().isoformat(),
                "--listed-at",
                "2019-01-01",
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert len(seen) == 4  # ceil(1000 / 300)
    assert all(b[0] == a[1] for a, b in itertools.pairwise(seen))  # contiguous
    files = normalize_bars(tmp_path, provider="coinbase", provider_symbol="BTC-USD", tf="1d")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    df = ds.bars("coinbase", "BTC-USD", "1d", as_of=until)
    assert len(df) == 1_000
    assert df["open_time"].diff().dropna().eq(pd.Timedelta(days=1)).all()


def test_first_download_needs_listing_date(dl, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dl, "fetch", lambda url: pytest.fail("must not fetch"))
    with pytest.raises(SystemExit, match="listed-at"):
        dl.main(
            [
                "--venue",
                "kraken",
                "--instrument",
                "BTC-USD",
                "--symbol",
                "XBTUSD",
                "--root",
                str(tmp_path),
            ]
        )


# --- Kraken: trades follow the `last` cursor


def test_kraken_trades_follow_the_cursor(dl, monkeypatch, tmp_path) -> None:
    trades = synthetic_trades(300, seed=9, start=START)
    pages = [trades[:100], trades[100:200], trades[200:]]
    urls = []

    def fake_fetch(url: str):
        urls.append(url)
        if "/OHLC" in url:
            body = json.dumps({"error": [], "result": {"XXBTZUSD": [], "last": 0}}).encode()
            return body, START
        page = pages[len([u for u in urls if "/Trades" in u]) - 1]
        return kraken_trades_payload(page), START + timedelta(days=30)

    monkeypatch.setattr(dl, "fetch", fake_fetch)
    assert (
        dl.main(
            [
                "--venue",
                "kraken",
                "--instrument",
                "BTC-USD",
                "--symbol",
                "XBTUSD",
                "--listed-at",
                "2013-09-10",
                "--trades-pages",
                "3",
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    trade_urls = [u for u in urls if "/Trades" in u]
    assert "since" not in trade_urls[0]
    for url, prev in zip(trade_urls[1:], pages, strict=False):
        assert parse_qs(urlparse(url).query)["since"] == [str(prev[-1].time_us * 1_000)]
    assert len(list(iter_captures(tmp_path, kind="trades"))) == 3


def test_dry_run_prints_requests_and_writes_nothing(dl, monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(dl, "fetch", lambda url: pytest.fail("dry run must not fetch"))
    assert (
        dl.main(
            [
                "--venue",
                "coinbase",
                "--instrument",
                "BTC-USD",
                "--symbol",
                "BTC-USD",
                "--since",
                "2020-01-01",
                "--until",
                "2020-12-31",
                "--dry-run",
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.count("GET https://api.exchange.coinbase.com") == 2
    assert not any(tmp_path.iterdir())
