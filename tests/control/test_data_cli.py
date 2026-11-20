"""`firm data list | fetch | update` wiring, with a fake network."""

import json
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest
from tests.data.test_update import DAY, START, Market

from firm.control import data_cli
from firm.control.cli import main
from firm.data.fetch import Fetcher

CONFIG = str(Path(__file__).resolve().parents[2] / "config")


@pytest.fixture
def market(monkeypatch):
    m = Market(START + 100 * DAY + timedelta(hours=5))

    def fetcher(root):
        return Fetcher(
            root, transport=m, clock=lambda: m.now, sleep=lambda s: None, log=lambda x: None
        )

    monkeypatch.setattr(data_cli, "_fetcher", fetcher)
    monkeypatch.setattr(data_cli, "_now", lambda: m.now)
    return m


def _fetch(root, *extra: str) -> int:
    return main(
        [
            "data", "fetch", "--root", str(root), "--config", CONFIG,
            "--provider", "binance", "--symbol", "BTCUSDT", "--instrument", "BTC-USDT",
            "--since", "2020-01-01", "--listed-at", "2020-01-01", *extra,
        ]
    )  # fmt: skip


def test_fetch_stores_and_builds_a_dataset(tmp_path, market, capsys) -> None:
    assert _fetch(tmp_path) == 0
    out = capsys.readouterr().out
    assert "binance:BTCUSDT:1d: 1 request(s) stored" in out
    assert "dataset ds-" in out and "100 bars" in out and "gaps=0" in out


def test_list_shows_series_and_json(tmp_path, market, capsys) -> None:
    assert main(["data", "list", "--root", str(tmp_path)]) == 0
    assert "no bar series" in capsys.readouterr().out
    _fetch(tmp_path, "--no-build")
    capsys.readouterr()
    assert main(["data", "list", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "binance:BTCUSDT:1d" in out and "binance:BTC-USDT" in out and "api" in out
    assert main(["data", "list", "--root", str(tmp_path), "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["series"] == "binance:BTCUSDT:1d" and row["updatable"] is True
    assert row["last_close"].startswith("2020-04-10")


def test_update_dry_run_then_real(tmp_path, market, capsys) -> None:
    _fetch(tmp_path, "--no-build")
    market.now += 10 * DAY
    market.urls.clear()
    capsys.readouterr()
    assert main(["data", "update", "--root", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "~10 new bar(s)" in out and out.count("GET ") == 1
    assert market.urls == []  # dry run: no request
    assert main(["data", "update", "--root", str(tmp_path), "--config", CONFIG]) == 0
    out = capsys.readouterr().out
    assert "last close 2020-04-20 00:00" in out and "110 bars" in out
    assert main(["data", "update", "--root", str(tmp_path), "--config", CONFIG]) == 0
    assert "up to date" in capsys.readouterr().out


def test_update_reports_a_failed_series_and_exits_3(tmp_path, market, capsys) -> None:
    _fetch(tmp_path, "--no-build")
    market.now += 10 * DAY

    def down(url):
        raise urllib.error.URLError("offline")

    market.__class__ = type("Down", (Market,), {"__call__": lambda self, url: down(url)})
    assert main(["data", "update", "--root", str(tmp_path), "--config", CONFIG]) == 3
    err = capsys.readouterr().err
    assert "binance:BTCUSDT:1d" in err and "FAILED" in err and "safe to repeat" in err
