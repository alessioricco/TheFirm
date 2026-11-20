"""The dashboard's Data view: list the store, update it, download a series
(fake network, jobs run inline), each start an OWNER_COMMAND."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from firm.control.api import create_app, load_or_create_token
from firm.core.domain import LedgerEventType
from firm.data.fetch import Fetcher
from firm.ledger.sink import ledger_path
from firm.ledger.store import Ledger
from market_fakes import DAY, START, Market
from verify_helpers import REPO

FETCH = {
    "provider": "binance",
    "symbol": "BTCUSDT",
    "instrument": "BTC-USDT",
    "tf": "1d",
    "since": "2020-01-01",
    "listed_at": "2020-01-01",
}


@pytest.fixture
def env(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    market = Market(START + 100 * DAY + timedelta(hours=5))

    def fetcher(root):
        return Fetcher(root, transport=market, clock=lambda: market.now, sleep=lambda s: None)

    app = create_app(
        config_root=REPO / "config",
        data_root=data,
        state_root=state,
        data_fetcher=fetcher,
        data_background=False,
    )
    import firm.control.data_api as data_api

    key = load_or_create_token(state)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {key}"
    return client, market, state, data_api


def _commands(state) -> list[dict]:
    return [
        e.payload
        for e in Ledger(ledger_path(state)).read()
        if e.event_type is LedgerEventType.OWNER_COMMAND
    ]


def test_needs_the_owner_and_a_json_body(env) -> None:
    client, *_ = env
    anon = TestClient(client.app)
    assert anon.get("/api/data").status_code == 401
    assert anon.post("/api/data/update", json={}).status_code == 401
    assert client.post("/api/data/update", content=b"x").status_code == 415


def test_fetch_then_list_then_update(env) -> None:
    client, market, state, _data_api = env
    empty = client.get("/api/data").json()
    assert empty["series"] == [] and {p["name"] for p in empty["providers"]} >= {"binance", "yahoo"}

    job = client.post("/api/data/fetch", json=FETCH).json()
    assert job["status"] == "done", job
    assert job["summary"].startswith("binance:BTCUSDT:1d: dataset ds-")
    assert any("100 bars" in line["text"] for line in job["lines"])

    (s,) = client.get("/api/data").json()["series"]
    assert s["series"] == "binance:BTCUSDT:1d" and s["updatable"]
    assert s["last_close"].startswith("2020-04-10")

    market.now += 10 * DAY
    job = client.post("/api/data/update", json={}).json()
    assert job["status"] == "done", job
    assert any("last close 2020-04-20" in line["text"] for line in job["lines"])
    assert client.get(f"/api/data/jobs/{job['job_id']}").json()["lines"] == job["lines"]

    cmds = _commands(state)
    assert [c["command"] for c in cmds] == ["data fetch", "data update"]
    assert all(c["via"] == "dashboard" for c in cmds)
    assert cmds[0]["symbol"] == "BTCUSDT"


def test_bad_requests_are_refused(env) -> None:
    client, *_ = env
    bad_symbol = FETCH | {"symbol": "../../x"}
    assert client.post("/api/data/fetch", json=bad_symbol).status_code == 422
    assert client.post("/api/data/fetch", json=FETCH | {"tf": "3h"}).status_code == 400
    equity = FETCH | {"provider": "yahoo", "asset_class": "equity", "instrument": "NVDA.XNAS"}
    assert client.post("/api/data/fetch", json=equity).status_code == 400  # no quote
    assert client.post("/api/data/update", json={}).status_code == 404  # nothing stored
    assert client.get("/api/data/jobs/j-nope").status_code == 404


def test_one_download_at_a_time(env) -> None:
    _client, _, _, data_api = env
    jobs = data_api.DataJobs()
    import threading

    gate = threading.Event()
    jobs.start("update", {}, lambda job: gate.wait(5) and "ok")
    with pytest.raises(data_api.JobBusy):
        jobs.start("update", {}, lambda job: "no")
    gate.set()


def test_a_failed_series_marks_the_job_failed(env) -> None:
    client, market, _state, _ = env
    client.post("/api/data/fetch", json=FETCH)
    market.now += 10 * DAY

    def down(url):
        raise OSError("offline")

    market.__class__ = type("Down", (type(market),), {"__call__": lambda self, url: down(url)})
    job = client.post("/api/data/update", json={}).json()
    assert job["status"] == "failed"
    assert "1 of 1 series failed" in job["summary"]
    assert any(line["level"] == "error" and "FAILED" in line["text"] for line in job["lines"])
