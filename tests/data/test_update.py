"""Listing the store and updating series: only new bars, no request when up to
date, synthetic and legacy series left alone (fake network)."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from firm.core.config import load_data_quality
from firm.data.fetch import Fetcher
from firm.data.store import write_capture
from firm.data.synthetic import seed_store
from firm.data.update import (
    OVERLAP_BARS,
    age,
    build_series,
    fetch_series,
    gaps,
    plan_update,
    stored_series,
    update_series,
)
from market_fakes import DAY, START, Market


def _fetcher(root, market: Market) -> Fetcher:
    return Fetcher(
        root, transport=market, clock=lambda: market.now, sleep=lambda s: None, log=lambda m: None
    )


def _first(root, market: Market) -> None:
    fetch_series(
        _fetcher(root, market),
        provider="binance",
        symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf="1d",
        start=START,
        end=market.now,
        listed_at=START,
    )


def test_list_reports_the_last_closed_bar(tmp_path) -> None:
    m = Market(START + 100 * DAY + timedelta(hours=5))  # bar 100 is forming
    _first(tmp_path, m)
    (s,) = stored_series(tmp_path)
    assert s.key == "binance:BTCUSDT:1d"
    assert (s.venue, s.instrument) == ("binance", "BTC-USDT")
    assert s.methods == ("api",) and s.updatable
    assert s.first_open == START
    assert s.last_close == START + 100 * DAY  # forming bar not counted
    assert age(s.last_close, m.now) == "5h"


def test_update_fetches_only_the_new_bars_with_an_overlap(tmp_path) -> None:
    m = Market(START + 100 * DAY + timedelta(hours=5))
    _first(tmp_path, m)
    m.now = START + 130 * DAY + timedelta(hours=1)
    m.urls.clear()
    (s,) = stored_series(tmp_path)
    p = update_series(_fetcher(tmp_path, m), s, m.now)
    assert p.start == s.last_close - OVERLAP_BARS * DAY
    (url,) = m.urls
    assert int(parse_qs(urlparse(url).query)["startTime"][0]) == int(p.start.timestamp()) * 1000
    (fresh,) = stored_series(tmp_path)
    assert fresh.last_close == START + 130 * DAY
    assert fresh.captures == 2
    manifest, files = build_series(tmp_path, fresh, load_data_quality(_config()))
    assert sum(f.rows for f in files) == 130
    assert gaps(tmp_path, files) == 0
    assert sum(f.revisions for f in files) == 0  # the overlap agreed
    assert manifest.dataset_version.startswith("ds-")


def test_up_to_date_series_makes_no_request(tmp_path) -> None:
    m = Market(START + 100 * DAY + timedelta(hours=5))
    _first(tmp_path, m)
    m.urls.clear()
    (s,) = stored_series(tmp_path)
    p = update_series(_fetcher(tmp_path, m), s, m.now)  # bar 100 closes at day 101
    assert p.start is None and "up to date" in p.note
    assert m.urls == []


def test_synthetic_and_legacy_series_are_never_updated(tmp_path) -> None:
    seed_store(tmp_path, n=50)  # synthetic kraken captures
    write_capture(
        tmp_path,
        b"date,open\n",
        provider="binance",
        kind="bars",
        provider_symbol="ETHUSDT",
        tf="1d",
        fmt="csv",
        captured_at=START,
        request={},
        via="legacy",
        licence="personal",
        capture_method="legacy_import",
    )
    now = START + 1000 * DAY
    plans = {s.key: plan_update(s, now) for s in stored_series(tmp_path)}
    assert set(plans) == {"kraken:XBTUSD:1d", "binance:ETHUSDT:1d"}
    for p in plans.values():
        assert p.start is None and p.requests == []
        assert p.note.startswith("not updated")


def _config():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "config"
