"""Trades end to end: provider format -> capture -> normalize -> dataset -> as_of read.

Synthetic trades only (public repo), in Kraken's and Coinbase's exact formats.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars, normalize_trades
from firm.data.store import iter_captures
from firm.data.synthetic import seed_store, seed_trades

START = datetime(2019, 1, 1, tzinfo=UTC)
FAR = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(("venue", "symbol"), [("kraken", "XBTUSD"), ("coinbase", "BTC-USD")])
def test_trades_round_trip_through_the_store(tmp_path: Path, venue: str, symbol: str) -> None:
    trades = seed_trades(tmp_path, venue=venue, provider_symbol=symbol, n=2_500)
    assert len(list(iter_captures(tmp_path, kind="trades"))) > 1  # overlapping pages
    files = normalize_trades(tmp_path, provider=venue, provider_symbol=symbol)
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    df = ds.trades(venue, "BTC-USD", as_of=FAR)

    assert len(df) == len(trades)  # overlaps de-duplicated by trade id
    assert df["trade_id"].tolist() == [str(t.trade_id) for t in trades]
    assert df["price"].tolist() == [t.price for t in trades]
    assert df["size"].tolist() == [t.size for t in trades]
    # taker side normalised (Coinbase reports the maker's side)
    assert df["taker_side"].tolist() == [t.taker_side for t in trades]
    expected = pd.to_datetime([t.time_us for t in trades], unit="us", utc=True)
    assert (df["time"].reset_index(drop=True) == pd.Series(expected)).all()


def test_trades_never_after_as_of(tmp_path: Path) -> None:
    trades = seed_trades(tmp_path, n=1_500)
    files = normalize_trades(tmp_path, provider="kraken", provider_symbol="XBTUSD")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    for k in (0, 10, 777, 1_499):
        as_of = datetime.fromtimestamp(trades[k].time_us / 1_000_000, tz=UTC)
        df = ds.trades("kraken", "BTC-USD", as_of=as_of)
        assert len(df) == k + 1
        assert df["time"].max() <= pd.Timestamp(as_of)
    start = datetime.fromtimestamp(trades[100].time_us / 1_000_000, tz=UTC)
    window = ds.trades("kraken", "BTC-USD", as_of=FAR, start=start)
    assert len(window) == 1_400


def test_capped_view_refuses_hidden_trades(tmp_path: Path) -> None:
    from firm.data.dataset import HiddenPeriodError

    seed_trades(tmp_path, n=500)
    files = normalize_trades(tmp_path, provider="kraken", provider_symbol="XBTUSD")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    hidden = START + timedelta(hours=2)
    with pytest.raises(HiddenPeriodError):
        ds.capped(hidden).trades("kraken", "BTC-USD", as_of=hidden)


def test_bars_and_trades_in_one_dataset(tmp_path: Path) -> None:
    seed_store(tmp_path, n=300)
    seed_trades(tmp_path, n=800)
    files = normalize_bars(tmp_path, provider="kraken", provider_symbol="XBTUSD", tf="1d")
    files += normalize_trades(tmp_path, provider="kraken", provider_symbol="XBTUSD")
    ds = open_dataset(tmp_path, build_dataset(tmp_path, files).dataset_version)
    assert len(ds.bars("kraken", "BTC-USD", "1d", as_of=FAR)) == 300
    assert len(ds.trades("kraken", "BTC-USD", as_of=FAR)) == 800
    assert {f.kind for f in ds.manifest.files} == {"bars", "trades"}
