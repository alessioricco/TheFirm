"""Timeframes: intraday end to end, provider alignment, derived timeframes.

Synthetic data only (public repo, no network).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from firm.control.cli import main
from firm.core.timeframes import alignment_for, bucket_start, is_aligned
from firm.data.dataset import build_dataset, open_dataset
from firm.data.normalize import normalize_bars
from firm.data.resample import aggregate, check_derivable, derive_bars
from firm.data.store import StoreError, write_capture
from firm.data.synthetic import seed_store, synthetic_bars, yahoo_chart_payload

MONDAY = datetime(2024, 1, 1, tzinfo=UTC)  # 2024-01-01 was a Monday
THURSDAY = datetime(2024, 1, 4, tzinfo=UTC)
FAR = datetime(2100, 1, 1, tzinfo=UTC)


def _dataset(root: Path, files):
    return open_dataset(root, build_dataset(root, files).dataset_version)


# --- the canonical grid


def test_grid_helpers() -> None:
    t = int(datetime(2024, 1, 3, 15, 42, tzinfo=UTC).timestamp())  # a Wednesday
    assert bucket_start(t, "1h") == int(datetime(2024, 1, 3, 15, tzinfo=UTC).timestamp())
    assert bucket_start(t, "4h") == int(datetime(2024, 1, 3, 12, tzinfo=UTC).timestamp())
    assert bucket_start(t, "1d") == int(datetime(2024, 1, 3, tzinfo=UTC).timestamp())
    assert bucket_start(t, "1w") == int(MONDAY.timestamp())
    assert bucket_start(t, "1w", week_start="thursday") == int(
        datetime(2023, 12, 28, tzinfo=UTC).timestamp()
    )
    assert is_aligned(int(THURSDAY.timestamp()), "1w", "week:thursday")
    assert not is_aligned(int(THURSDAY.timestamp()), "1w", "week:monday")
    assert alignment_for("1d", day_boundary="session", week_start="monday") == "session"


# --- intraday end to end


@pytest.mark.parametrize(("tf", "n"), [("1m", 2_000), ("15m", 900), ("4h", 600)])
def test_intraday_series_end_to_end(tmp_path: Path, tf: str, n: int) -> None:
    seed_store(
        tmp_path,
        venue="binance",
        provider_symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf=tf,
        n=n,
        start=MONDAY,
        chunk=1000,
    )
    files = normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf=tf)
    ds = _dataset(tmp_path, files)
    df = ds.bars("binance", "BTC-USDT", tf, as_of=FAR)
    step = pd.Timedelta(seconds={"1m": 60, "15m": 900, "4h": 14_400}[tf])
    assert len(df) == n and df["open_time"].diff().dropna().eq(step).all()
    assert ds.alignment("binance", "BTC-USDT", tf) == "utc"
    # point in time: halfway through bar k, only bars 0..k-1 are known
    k = n // 2
    as_of = df["open_time"][k] + step / 2
    assert len(ds.bars("binance", "BTC-USDT", tf, as_of=as_of.to_pydatetime())) == k


def test_several_timeframes_of_one_instrument_stay_apart(tmp_path: Path) -> None:
    for tf, n in (("1m", 1_500), ("1h", 300), ("1d", 50)):
        seed_store(
            tmp_path,
            venue="binance",
            provider_symbol="BTCUSDT",
            instrument="BTC-USDT",
            tf=tf,
            n=n,
            start=MONDAY,
            chunk=1000,
        )
    files = [
        f
        for tf in ("1m", "1h", "1d")
        for f in normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf=tf)
    ]
    ds = _dataset(tmp_path, files)
    assert [len(ds.bars("binance", "BTC-USDT", tf, as_of=FAR)) for tf in ("1m", "1h", "1d")] == [
        1_500,
        300,
        50,
    ]


# --- each provider's weekly alignment is checked and recorded


def test_weekly_bars_carry_their_providers_alignment(tmp_path: Path) -> None:
    seed_store(
        tmp_path,
        venue="kraken",
        provider_symbol="XBTUSD",
        instrument="BTC-USD",
        tf="1w",
        n=60,
        start=THURSDAY,
    )
    seed_store(
        tmp_path,
        venue="binance",
        provider_symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf="1w",
        n=60,
        start=MONDAY,
    )
    files = normalize_bars(tmp_path, provider="kraken", provider_symbol="XBTUSD", tf="1w")
    files += normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf="1w")
    ds = _dataset(tmp_path, files)
    assert ds.alignment("kraken", "BTC-USD", "1w") == "week:thursday"
    assert ds.alignment("binance", "BTC-USDT", "1w") == "week:monday"


def test_misaligned_bars_are_refused_not_mixed(tmp_path: Path) -> None:
    # Binance weeks start Monday; bars starting Thursday must not slip in
    seed_store(
        tmp_path,
        venue="binance",
        provider_symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf="1w",
        n=10,
        start=THURSDAY,
    )
    with pytest.raises(StoreError, match="alignment"):
        normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf="1w")


# --- derived timeframes


def test_aggregate_matches_a_hand_computed_bar() -> None:
    rows = [
        {
            "open_time_ns": (3600 * 10 + 60 * i) * 10**9,
            "open": 10.0 + i,
            "high": 20.0 + i,
            "low": 5.0 - i * 0.01,
            "close": 11.0 + i,
            "volume": 1.0,
        }
        for i in range(60)
    ]
    (bar,), incomplete = aggregate(rows, "1m", "1h")
    assert incomplete == 0
    assert bar["open_time_ns"] == 36_000 * 10**9 and bar["close_time_ns"] == 39_600 * 10**9
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]) == (
        10.0,
        79.0,
        5.0 - 59 * 0.01,
        70.0,
        60.0,
    )


def test_incomplete_buckets_are_left_out(tmp_path: Path) -> None:
    rows = [
        {
            "open_time_ns": 60 * i * 10**9,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
        }
        for i in range(150)
        if i != 75
    ]  # gap in hour 2
    bars, incomplete = aggregate(rows, "1m", "1h")
    assert [b["open_time_ns"] // 10**9 for b in bars] == [0]  # hour 0 only
    assert incomplete == 2  # hour 1 has a gap, hour 2 is partial (30 of 60)


@pytest.mark.parametrize(("from_tf", "to_tf"), [("1d", "4h"), ("1h", "1h"), ("5m", "7m")])
def test_impossible_derivations_are_refused(from_tf: str, to_tf: str) -> None:
    with pytest.raises(ValueError):
        check_derivable(from_tf, to_tf)


def test_derived_series_end_to_end_and_point_in_time(tmp_path: Path) -> None:
    seed_store(
        tmp_path,
        venue="binance",
        provider_symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf="1m",
        n=1_500,
        start=MONDAY,
        chunk=1000,
    )  # 25 complete hours
    native = normalize_bars(tmp_path, provider="binance", provider_symbol="BTCUSDT", tf="1m")
    derived = derive_bars(tmp_path, native, to_tf="1h")
    assert all("derived_from=1m" in f.path and f.alignment == "utc" for f in derived)
    ds = _dataset(tmp_path, native + derived)
    minutes = ds.bars("binance", "BTC-USDT", "1m", as_of=FAR)
    hours = ds.bars("binance", "BTC-USDT", "1h", as_of=FAR, derived_from="1m")
    assert len(hours) == 25
    first = minutes.iloc[:60]
    assert hours.iloc[0][["open", "high", "low", "close"]].tolist() == [
        first["open"].iloc[0],
        first["high"].max(),
        first["low"].min(),
        first["close"].iloc[-1],
    ]
    assert hours.iloc[0]["volume"] == pytest.approx(first["volume"].sum())
    with pytest.raises(LookupError):  # no NATIVE 1h series: derived ones are asked for by name
        ds.bars("binance", "BTC-USDT", "1h", as_of=FAR)
    mid = MONDAY + timedelta(hours=3, minutes=30)
    assert len(ds.bars("binance", "BTC-USDT", "1h", as_of=mid, derived_from="1m")) == 3


def test_derived_weeks_line_up_across_providers(tmp_path: Path) -> None:
    """Kraken's native weeks start Thursday; weeks derived from daily bars start Monday
    for every provider, so they can be compared."""
    for venue, sym, inst in (("kraken", "XBTUSD", "BTC-USD"), ("binance", "BTCUSDT", "BTC-USDT")):
        seed_store(
            tmp_path,
            venue=venue,
            provider_symbol=sym,
            instrument=inst,
            tf="1d",
            n=70,
            start=THURSDAY,
        )
    weeks = []
    for venue, sym, inst in (("kraken", "XBTUSD", "BTC-USD"), ("binance", "BTCUSDT", "BTC-USDT")):
        daily = normalize_bars(tmp_path, provider=venue, provider_symbol=sym, tf="1d")
        ds = _dataset(tmp_path, daily + derive_bars(tmp_path, daily, to_tf="1w"))
        weeks.append(ds.bars(venue, inst, "1w", as_of=FAR, derived_from="1d")["open_time"])
    assert weeks[0].tolist() == weeks[1].tolist()
    assert all(t.weekday() == 0 for t in weeks[0])  # Mondays
    assert weeks[0].iloc[0] == pd.Timestamp("2024-01-08", tz="UTC")  # first FULL week


def test_session_aligned_series_cannot_be_derived_yet(tmp_path: Path) -> None:
    from firm.data.fetch import ensure_instrument

    bars = synthetic_bars(30, seed=1, start=MONDAY)
    ensure_instrument(
        tmp_path,
        provider="yahoo",
        symbol="SYNTH",
        venue="xnas",
        instrument="SYNTH.XNAS",
        asset_class="equity",
        listed_at=MONDAY,
        quote="USD",
    )
    write_capture(
        tmp_path,
        yahoo_chart_payload(bars),
        provider="yahoo",
        kind="bars",
        provider_symbol="SYNTH",
        tf="1d",
        fmt="json",
        captured_at=FAR,
        request={},
        via="test",
        licence="synthetic",
    )
    daily = normalize_bars(tmp_path, provider="yahoo", provider_symbol="SYNTH", tf="1d")
    assert daily[0].alignment == "session"
    with pytest.raises(StoreError, match="exchange calendar"):
        derive_bars(tmp_path, daily, to_tf="1w")


def test_cli_derive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_store(
        tmp_path,
        venue="binance",
        provider_symbol="BTCUSDT",
        instrument="BTC-USDT",
        tf="1m",
        n=600,
        start=MONDAY,
        chunk=1000,
    )
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--root",
                str(tmp_path),
                "--bars",
                "binance:BTCUSDT:1m",
                "--derive",
                "1m:1h",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "derived_from=1m" in out and "aligned=utc" in out
    version = next(w for w in out.split() if w.startswith("ds-"))
    manifest = json.loads((tmp_path / "datasets" / f"{version}.json").read_text())
    assert {(f["tf"], f["derived_from"]) for f in manifest["files"]} == {("1m", None), ("1h", "1m")}
    assert (
        main(
            [
                "data",
                "build-dataset",
                "--root",
                str(tmp_path),
                "--bars",
                "binance:BTCUSDT:1m",
                "--derive",
                "1h:1d",
            ]
        )
        == 3
    )  # no native 1h
