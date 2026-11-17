"""Implausible bars: point-in-time, quarantined as gaps, listed in the manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from firm.core.config import DataQualityConfig, load_data_quality
from firm.data.quality import implausible_bars

RULE = DataQualityConfig(quality="default", rule_version=1, max_price_ratio=3.0)
REPO = Path(__file__).resolve().parents[2]


def bar(t: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"open_time_ns": t, "open": o, "high": h, "low": lo, "close": c}


def test_the_rule_is_configuration() -> None:
    rule = load_data_quality(REPO / "config")
    assert rule.max_price_ratio > 1 and rule.rule_id.startswith("default-v")


def test_a_corrupt_open_or_low_is_caught_and_real_crashes_are_not() -> None:
    bars = [
        bar(0, 100, 101, 99, 100),
        bar(1, 0.06, 101, 0.06, 100),  # opened at ~0: corrupt
        bar(2, 100, 100, 0.01, 99),  # a ~0 low: corrupt
        bar(3, 99, 99, 60, 70),  # a 30% crash: real
        bar(4, 70, 75, 50, 52),
    ]
    found = implausible_bars(bars, RULE)
    assert [f.open_time_ns for f in found] == [1, 2]
    assert "open 0.06" in found[0].reason and "rule default-v1" in found[0].reason


def test_only_the_past_decides_and_a_bad_bar_is_never_a_reference() -> None:
    bars = [bar(0, 100, 100, 100, 100), bar(1, 100, 1000, 100, 1000), bar(2, 100, 101, 99, 100)]
    # bar 1 closes at 1000 (implausible); bar 2 is judged against bar 0's close, not 1000
    assert [f.open_time_ns for f in implausible_bars(bars, RULE)] == [1]
    # and appending future bars never changes the verdict on earlier ones
    more = [*bars, bar(3, 5, 5, 5, 5)]
    assert [f.open_time_ns for f in implausible_bars(more, RULE)][:1] == [1]


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_prices_are_implausible(bad: float) -> None:
    assert implausible_bars([bar(0, 100, 100, bad, 100)], RULE)


def test_the_normaliser_quarantines_and_the_manifest_lists_it(tmp_path: Path) -> None:
    import json
    from datetime import UTC, datetime

    from firm.data.dataset import build_dataset, open_dataset
    from firm.data.normalize import normalize_bars
    from firm.data.store import write_capture
    from firm.data.synthetic import kraken_ohlc_payload, seed_store, synthetic_bars

    seed_store(tmp_path, n=50)  # clean synthetic series + instrument record
    start = datetime(2019, 1, 1, tzinfo=UTC)
    bars = synthetic_bars(51, seed=7, start=start, tf="1d")
    b = bars[20]
    bars[20] = type(b)(
        open_time=b.open_time,
        open=b.open * 0.0001,
        high=b.high,
        low=b.open * 0.0001,
        close=b.close,
        volume=b.volume,
    )  # corrupt open
    write_capture(
        tmp_path,
        kraken_ohlc_payload(bars),
        provider="kraken",
        kind="bars",
        provider_symbol="XBTUSD",
        tf="1d",
        fmt="json",
        captured_at=datetime(2019, 3, 1, tzinfo=UTC),
        request={"t": "corrupt"},
        via="test",
        licence="synthetic",
        capture_method="synthetic",
    )
    files = normalize_bars(
        tmp_path, provider="kraken", provider_symbol="XBTUSD", tf="1d", quality=RULE
    )
    (q,) = [x for f in files for x in f.quarantined]
    assert q["open_time"].startswith("2019-01-21") and "open" in q["reason"]
    assert all(f.quality_rule == RULE.rule_id for f in files)
    version = build_dataset(tmp_path, files).dataset_version
    df = open_dataset(tmp_path, version).bars(
        "kraken", "BTC-USD", "1d", as_of=datetime(2100, 1, 1, tzinfo=UTC)
    )
    days = set(df["open_time"].dt.strftime("%Y-%m-%d"))
    assert "2019-01-21" not in days and "2019-01-20" in days and "2019-01-22" in days
    assert df["open"].min() > 1  # left out as a gap, nothing invented
    manifest = json.loads((tmp_path / "datasets" / f"{version}.json").read_text())
    assert any(f["quarantined"] for f in manifest["files"])
