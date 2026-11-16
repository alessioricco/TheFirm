"""The owner-run download script: listing, dry runs, and wiring (fake fetcher)."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from firm.data.fetch import Fetcher
from firm.data.store import iter_captures
from firm.data.synthetic import binance_klines_payload, synthetic_bars

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_data.py"


@pytest.fixture
def dl():
    spec = importlib.util.spec_from_file_location("download_data", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lists_providers(dl, capsys) -> None:
    assert dl.main(["--providers"]) == 0
    out = capsys.readouterr().out
    for name in ("binance", "coinbase", "kraken", "yahoo"):
        assert name in out


def test_dry_run_prints_requests_and_writes_nothing(dl, tmp_path, capsys) -> None:
    assert (
        dl.main(
            [
                "--provider",
                "binance",
                "--symbol",
                "BTCUSDT",
                "--instrument",
                "BTC-USDT",
                "--since",
                "2020-01-01",
                "--until",
                "2022-12-31",
                "--dry-run",
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.count("GET https://data-api.binance.vision") == 2
    assert not any(tmp_path.iterdir())


def test_real_run_uses_the_fetcher(dl, tmp_path) -> None:
    bars = synthetic_bars(400, seed=1, start=datetime(2020, 1, 1, tzinfo=UTC))
    f = Fetcher(
        tmp_path,
        transport=lambda url: binance_klines_payload(bars),
        clock=lambda: datetime(2030, 1, 1, tzinfo=UTC),
        sleep=lambda s: None,
        log=lambda m: None,
    )
    assert (
        dl.main(
            [
                "--provider",
                "binance",
                "--symbol",
                "BTCUSDT",
                "--instrument",
                "BTC-USDT",
                "--since",
                "2020-01-01",
                "--until",
                "2021-02-04",
                "--listed-at",
                "2017-08-17",
                "--root",
                str(tmp_path),
            ],
            fetcher=f,
        )
        == 0
    )
    assert len(list(iter_captures(tmp_path, provider="binance"))) == 1


def test_required_args(dl) -> None:
    with pytest.raises(SystemExit):
        dl.main(["--provider", "binance"])
