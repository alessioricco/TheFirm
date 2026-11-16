"""Data plane: captures, parsers, normalizer, dataset versions, point-in-time reads.

All data here is synthetic (firm.data.synthetic): the repo is public.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from firm.data.dataset import HiddenPeriodError, build_dataset, open_dataset
from firm.data.normalize import normalize_bars
from firm.data.providers import (
    ProviderError,
    parse_coinbase_candles,
    parse_coinbase_trades,
    parse_kraken_ohlc,
    parse_kraken_trades,
)
from firm.data.store import StoreError, iter_captures, partition, path_value, write_capture
from firm.data.synthetic import (
    coinbase_candles_payload,
    kraken_ohlc_payload,
    seed_store,
    synthetic_bars,
)

START = datetime(2019, 1, 1, tzinfo=UTC)
FAR = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    seed_store(tmp_path, n=800)
    return tmp_path


def _dataset(root: Path):
    files = normalize_bars(root, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    return open_dataset(root, build_dataset(root, files).dataset_version)


# --- naming rules


def test_partition_paths_follow_naming_rule() -> None:
    p = partition(provider="binance", kind="bars", symbol="BTC/USDT", tf="1d")
    assert p.as_posix() == "provider=binance/kind=bars/symbol=BTC-USDT/tf=1d"


@pytest.mark.parametrize("bad", ["", "a b", "x=y", "../up", "-lead"])
def test_bad_path_values_refused(bad: str) -> None:
    with pytest.raises(StoreError):
        path_value(bad)


def test_capture_is_write_once_and_has_meta(tmp_path: Path) -> None:
    kw = dict(
        provider="kraken",
        kind="bars",
        provider_symbol="XBTUSD",
        tf="1d",
        fmt="json",
        captured_at=START,
        request={},
        via="test",
        licence="synthetic",
    )
    cap = write_capture(tmp_path, b"[]", **kw)
    assert cap.path.name.startswith("20190101T000000Z_")
    assert cap.path.with_name(cap.path.name.replace(".json", ".meta.json")).is_file()
    write_capture(tmp_path, b"[]", **kw)  # identical: no-op
    assert len(list(iter_captures(tmp_path))) == 1


def test_capture_refuses_naive_time(tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        write_capture(
            tmp_path,
            b"[]",
            provider="kraken",
            kind="bars",
            provider_symbol="X",
            tf="1d",
            fmt="json",
            captured_at=datetime(2020, 1, 1),
            request={},
            via="t",
            licence="s",
        )


# --- parsers: closed bars only


def test_kraken_drops_the_forming_bar() -> None:
    bars = synthetic_bars(5, seed=1, start=START)
    forming_mid = START + timedelta(days=4, hours=12)
    rows = parse_kraken_ohlc(kraken_ohlc_payload(bars), tf="1d", captured_at=forming_mid)
    assert len(rows) == 4
    assert rows[-1]["close_time_ns"] <= pd.Timestamp(forming_mid).value


def test_coinbase_candles_reordered_and_mapped() -> None:
    bars = synthetic_bars(3, seed=2, start=START)
    rows = parse_coinbase_candles(coinbase_candles_payload(bars), tf="1d", captured_at=FAR)
    assert [r["open"] for r in rows] == [b.open for b in bars]  # oldest first, open not low
    assert [r["low"] for r in rows] == [b.low for b in bars]


def test_kraken_error_is_raised() -> None:
    with pytest.raises(ProviderError):
        parse_kraken_ohlc(
            b'{"error":["EQuery:Unknown asset pair"],"result":{}}', tf="1d", captured_at=FAR
        )


def test_inconsistent_ohlc_refused() -> None:
    payload = json.dumps(
        {"error": [], "result": {"X": [[0, "10", "9", "8", "11", "9", "1", 1]], "last": 0}}
    ).encode()
    with pytest.raises(ProviderError):
        parse_kraken_ohlc(payload, tf="1d", captured_at=FAR)


def test_trade_sides_normalised_to_taker() -> None:
    k = json.dumps(
        {"error": [], "result": {"X": [["100.0", "0.5", 1.5, "b", "m", "", 7]], "last": "1"}}
    ).encode()
    assert parse_kraken_trades(k, captured_at=FAR)[0]["taker_side"] == "buy"
    c = json.dumps(
        [{"time": "2020-01-01T00:00:00Z", "trade_id": 1, "price": "1", "size": "2", "side": "buy"}]
    ).encode()
    assert parse_coinbase_trades(c, captured_at=FAR)[0]["taker_side"] == "sell"  # maker bought


# --- normalizer and datasets


def test_overlapping_pages_merge_into_continuous_bars(store: Path) -> None:
    df = _dataset(store).bars("kraken", "BTC-USD", "1d", as_of=FAR)
    assert len(df) == 800
    assert df["open_time"].diff().dropna().eq(pd.Timedelta(days=1)).all()


def test_dataset_version_is_content_addressed_and_reproducible(store: Path) -> None:
    v1 = _dataset(store).version
    v2 = _dataset(store).version
    assert v1 == v2 and v1.startswith("ds-")


def test_dataset_detects_tampering(store: Path) -> None:
    ds = _dataset(store)
    f = store / ds.manifest.files[0].path
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(StoreError, match="checksum"):
        open_dataset(store, ds.version)


def test_legacy_capture_without_time_refused_by_normalizer(tmp_path: Path) -> None:
    seed_store(tmp_path, n=50)
    cap = next(iter_captures(tmp_path))
    meta = cap.path.with_name(cap.path.name.replace(".json", ".meta.json"))
    doc = json.loads(meta.read_text()) | {"captured_at": None}
    meta.write_text(json.dumps(doc))
    with pytest.raises(StoreError, match="capture time unknown"):
        normalize_bars(tmp_path, provider="kraken", provider_symbol="XBTUSD", tf="1d")


# --- point in time


def test_bars_never_after_as_of(store: Path) -> None:
    ds = _dataset(store)
    for days in (10, 100, 333, 799):
        as_of = START + timedelta(days=days, hours=5)
        df = ds.bars("kraken", "BTC-USD", "1d", as_of=as_of)
        assert df["close_time"].max() <= pd.Timestamp(as_of)
        assert len(df) == days  # bar `days` closes after as_of


def test_as_of_must_be_timezone_aware(store: Path) -> None:
    with pytest.raises(ValueError):
        _dataset(store).bars("kraken", "BTC-USD", "1d", as_of=datetime(2020, 1, 1))


def test_capped_view_refuses_hidden_period(store: Path) -> None:
    ds = _dataset(store)
    hidden = START + timedelta(days=600)
    view = ds.capped(hidden)
    assert len(view.bars("kraken", "BTC-USD", "1d", as_of=hidden - timedelta(days=1))) > 0
    with pytest.raises(HiddenPeriodError):
        view.bars("kraken", "BTC-USD", "1d", as_of=hidden)


# --- delisted instruments stay


def test_delisted_instrument_kept_and_status_is_point_in_time(tmp_path: Path) -> None:
    delist = START + timedelta(days=400)
    seed_store(
        tmp_path, n=300, instrument="LUNA-USD", provider_symbol="LUNAUSD", delisted_at=delist
    )
    files = normalize_bars(tmp_path, provider="kraken", provider_symbol="LUNAUSD", tf="1d")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    assert ds.universe("kraken", as_of=START + timedelta(days=100)) == {"LUNA-USD": "listed"}
    assert ds.universe("kraken", as_of=delist + timedelta(days=1)) == {"LUNA-USD": "delisted"}
    assert len(ds.bars("kraken", "LUNA-USD", "1d", as_of=FAR)) == 300


def test_synthetic_and_real_captures_never_mix(tmp_path: Path) -> None:
    seed_store(tmp_path, n=1500)  # several 720-bar pages = several captures
    assert len(list(iter_captures(tmp_path))) > 1
    cap = next(iter_captures(tmp_path))
    meta = cap.path.with_name(cap.path.name.replace(".json", ".meta.json"))
    doc = json.loads(meta.read_text()) | {"capture_method": "api"}
    meta.write_text(json.dumps(doc))  # now one "real" capture among synthetic ones
    with pytest.raises(StoreError, match="mix synthetic and real"):
        normalize_bars(tmp_path, provider="kraken", provider_symbol="XBTUSD", tf="1d")
