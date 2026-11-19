"""The dashboard page and the API it uses (M3 plan, M3a part 2).

- The page and its files are served only to the owner, with a strict
  Content-Security-Policy: no inline or third-party scripts, nothing may
  frame it. Vendored libraries are exactly the reviewed files (SHA-256).
- `/api/commands` is generated from the CLI parser: every command is there.
- Panel layouts are saved per named view, validated.
- The price chart comes from the dataset the run's verdict cites; fills from
  the ledger.
- The demo script builds a store where every panel has something to show.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firm.control.api import WEB, create_app, load_or_create_token
from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger
from verify_helpers import NOW, REPO


@pytest.fixture
def env(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    app = create_app(config_root=REPO / "config", data_root=data, state_root=state)
    client = TestClient(app)
    client.get("/login", params={"token": load_or_create_token(state)})
    return client, data, state


def test_vendored_files_are_the_reviewed_ones() -> None:
    table = (WEB / "vendor" / "VENDOR.md").read_text()
    rows = re.findall(r"^\| `([^`]+)` \|.*\| `([0-9a-f]{64})` \|$", table, re.M)
    assert {name for name, _ in rows} == {
        "lightweight-charts.standalone.production.js",
        "gridstack-all.js",
        "gridstack.min.css",
    }
    for name, sha in rows:
        assert hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest() == sha, name
    assert "TradingView" in (REPO / "NOTICE").read_text()  # the licence's attribution


def test_the_page_has_no_inline_or_outside_scripts(env) -> None:
    client, _, _ = env
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    html = r.text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)  # every script is a file
    assert not re.search(
        r"""(src|href)=["']https?://""", html.replace('href="https://www.tradingview.com/"', "")
    )  # only the attribution link leaves
    assert "Lightweight Charts" in html  # the attribution is on the page


def test_static_files_need_the_owner_and_stay_inside_the_web_folder(env, tmp_path) -> None:
    client, _, _ = env
    r = client.get("/static/js/app.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert (
        client.get("/static/vendor/gridstack.min.css")
        .headers["content-type"]
        .startswith("text/css")
    )
    for bad in ("/static/../api.py", "/static/%2e%2e/api.py", "/static/js/../../api.py",
                "/static/nope.js", "/static/vendor/VENDOR.md"):  # fmt: skip
        assert client.get(bad).status_code == 404, bad
    anon = TestClient(client.app)
    assert anon.get("/static/js/app.js").status_code == 401


def test_meta_lists_markets_and_timeframes(env) -> None:
    client, _, _ = env
    meta = client.get("/api/meta").json()
    assert "crypto" in meta["markets"] and meta["researchers"] == 2
    assert meta["timeframes"][0] == "1m" and meta["timeframes"][-1] == "1w"
    assert meta["views"] == ["research", "trading"]


def test_the_command_reference_is_every_cli_command(env) -> None:
    from test_commands_doc import COMMANDS

    client, _, _ = env
    catalog = {c["command"]: c for c in client.get("/api/commands").json()}
    assert set(catalog) == set(COMMANDS)
    start = catalog["firm research start"]
    market = next(o for o in start["options"] if "--market" in o["flags"])
    assert market["required"] and market["help"]
    show = catalog["firm research show"]
    assert any(o["flags"] == ["DOSSIER"] and o["required"] for o in show["options"])


def test_layouts_are_saved_per_view_and_validated(env) -> None:
    client, _, state = env
    assert client.get("/api/layouts/research").json() == {"items": None}
    items = [{"id": "dossiers", "x": 0, "y": 2, "w": 8, "h": 7}]
    assert client.put("/api/layouts/research", json={"items": items}).status_code == 200
    assert client.get("/api/layouts/research").json() == {"items": items}
    assert (state / "dashboard" / "layouts" / "research.json").is_file()
    assert client.get("/api/layouts/trading").json() == {"items": None}  # per view
    bad = [{"id": "../x", "x": 0, "y": 0, "w": 1, "h": 1}]
    assert client.put("/api/layouts/research", json={"items": bad}).status_code == 422
    too_wide = [{"id": "a", "x": 0, "y": 0, "w": 13, "h": 1}]
    assert client.put("/api/layouts/research", json={"items": too_wide}).status_code == 422
    assert client.put("/api/layouts/elsewhere", json={"items": []}).status_code == 404
    assert client.put("/api/layouts/research", content=b"x").status_code == 415


def test_ledger_pages_backwards(env) -> None:
    client, _, state = env
    led = Ledger(state / "ledger.sqlite")
    for i in range(10):
        led.append(LedgerEventType.FEED_STATUS, f"e{i}", {"status": "fresh"})
    newest = client.get("/api/ledger", params={"limit": 3}).json()
    assert [e["sequence"] for e in newest] == [7, 8, 9]
    older = client.get("/api/ledger", params={"before": 7, "limit": 3}).json()
    assert [e["sequence"] for e in older] == [4, 5, 6]
    assert client.get("/api/ledger", params={"before": 2, "limit": 5}).json()[0]["sequence"] == 0


def test_the_chart_reads_the_runs_verified_dataset_and_its_fills(dataset, tmp_path) -> None:
    import json

    from verify_helpers import run_verify

    root, _version = dataset
    state = tmp_path / "state"
    result = run_verify(dataset, state, "ma_crossover.py", "MovingAverageCrossover")
    (state / "reports").mkdir(parents=True, exist_ok=True)
    verdict = result.verdict.verdict_id
    (state / "reports" / f"{verdict}.json").write_bytes(result.report)
    led = Ledger(state / "ledger.sqlite")
    led.append(LedgerEventType.MARKET_STATUS, "started", {"kind": "started", "market": "crypto",
               "venue": "kraken", "verdict_id": verdict, "at": NOW.isoformat()})  # fmt: skip
    app = create_app(config_root=REPO / "config", data_root=root, state_root=state)
    client = TestClient(app)
    client.get("/login", params={"token": load_or_create_token(state)})
    chart = client.get(
        "/api/paper/chart", params={"market": "crypto", "venue": "kraken", "bars": 50}
    ).json()
    assert chart["instrument"] == "BTC-USD" and chart["timeframe"] == "1d"
    assert len(chart["candles"]) == 50 and chart["fills"] == []
    assert chart["dataset_version"] == json.loads(result.report)["data"]["dataset_version"]
    assert {"time", "open", "high", "low", "close"} <= set(chart["candles"][0])
    missing = client.get("/api/paper/chart", params={"market": "crypto", "venue": "coinbase"})
    assert missing.status_code == 404


def test_the_demo_builds_a_store_where_every_panel_has_data(tmp_path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dashboard_demo", REPO / "scripts" / "dashboard_demo.py"
    )
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    data, state = demo.build(tmp_path / "demo")
    client = TestClient(create_app(config_root=REPO / "config", data_root=data, state_root=state))
    client.get("/login", params={"token": load_or_create_token(state)})
    paper = client.get("/api/paper").json()
    assert paper["runs"][0]["running"] and paper["accounts"][0]["fills"] == 7
    assert paper["trades"]["closed"] == 3 and paper["trades"]["error"] is None
    stages = {d["stage"] for d in client.get("/api/research").json()["dossiers"]}
    assert {"draft", "research", "passed", "failed", "closed"} <= stages
    run = paper["runs"][0]
    chart = client.get(
        "/api/paper/chart", params={"market": run["market"], "venue": run["venue"]}
    ).json()
    assert chart["candles"] and len(chart["fills"]) == 7
    assert isinstance(Path(data), Path)
