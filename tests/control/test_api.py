"""The control API behind the dashboard (M3 plan, M3a).

- Everything under /api needs the owner's token (cookie or bearer); the
  token lives in the state folder (mode 600) and is never in a response.
- The research floor: read it, and steer it (start, pause, resume, redirect,
  close, floor brief), each an OWNER_COMMAND in the ledger, "via dashboard".
  Changes need a JSON body, so a page on another site can't post a form.
- The paper view: status from the ledger; recent ledger events; a WebSocket
  that pushes new ledger events as they are written.
- Read-only for trading: no endpoint places, sizes or cancels an order, and
  there is no kill switch yet (M3b).
"""

from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient

from firm.control.api import create_app, load_or_create_token, token_path
from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger
from verify_helpers import REPO


@pytest.fixture
def env(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    app = create_app(
        config_root=REPO / "config", data_root=data, state_root=state, poll_seconds=0.05
    )
    key = load_or_create_token(state)
    return TestClient(app), key, state


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_everything_needs_the_token(env) -> None:
    client, token, state = env
    for path in ("/api/research", "/api/paper", "/api/ledger"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth("wrong")).status_code == 401
        assert client.get(path, headers=auth(token)).status_code == 200
    mode = stat.S_IMODE(token_path(state).stat().st_mode)
    assert mode == 0o600 and load_or_create_token(state) == token  # stable


def test_login_sets_an_httponly_strict_cookie(env) -> None:
    client, token, _ = env
    assert client.get("/login", params={"token": "nope"}, follow_redirects=False).status_code == 401
    r = client.get("/login", params={"token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/research").status_code == 200  # the cookie now works
    assert token not in client.get("/api/research").text


def test_the_page_needs_login_too(env) -> None:
    client, token, _ = env
    assert client.get("/", follow_redirects=False).status_code == 401
    client.get("/login", params={"token": token})
    page = client.get("/")
    assert page.status_code == 200 and "<html" in page.text.lower()


def test_steering_the_research_floor(env) -> None:
    client, token, state = env
    h = auth(token)
    body = {"brief": "momentum", "market": "crypto", "instruments": ["BTC-USD"],
            "timeframes": ["1d"]}  # fmt: skip
    first = client.post("/api/research", json=body | {"dossier_id": "d-001"}, headers=h)
    assert first.status_code == 200, first.text
    client.post("/api/research", json=body | {"dossier_id": "d-002"}, headers=h)
    client.post("/api/research", json=body | {"dossier_id": "d-003"}, headers=h)
    floor = client.get("/api/research", headers=h).json()
    assert floor["researchers"] == 2
    assert [d["place"] for d in floor["dossiers"]] == [
        "running",
        "running",
        "waiting (1 in the queue)",
    ]

    assert (
        client.post("/api/research/d-001/pause", json={"reason": "later"}, headers=h).status_code
        == 200
    )
    assert client.get("/api/research/d-001", headers=h).json()["place"] == "paused"
    assert client.post("/api/research/d-001/resume", json={}, headers=h).status_code == 200
    r = client.post("/api/research/d-002/redirect", json={"brief": "carry"}, headers=h)
    assert r.json()["dossier_id"] == "d-002"  # a draft keeps its ID
    assert (
        client.post("/api/research/d-003/close", json={"reason": "no"}, headers=h).status_code
        == 200
    )
    assert (
        client.put("/api/research/brief", json={"text": "prefer 1h"}, headers=h).status_code == 200
    )
    assert client.get("/api/research", headers=h).json()["floor_brief"] == "prefer 1h"

    events = list(Ledger(state / "ledger.sqlite").read())
    owner = [e.payload for e in events if e.event_type is LedgerEventType.OWNER_COMMAND]
    assert {p["via"] for p in owner} == {"dashboard"}
    assert [p["command"] for p in owner][-5:] == [
        "research pause", "research resume", "research redirect", "research close",
        "research brief",
    ]  # fmt: skip


def test_mistakes_are_4xx_not_crashes(env) -> None:
    client, token, _ = env
    h = auth(token)
    assert client.get("/api/research/d-404", headers=h).status_code == 404
    assert client.post("/api/research/d-404/pause", json={}, headers=h).status_code == 404
    bad = {"brief": "", "market": "crypto", "instruments": ["BTC-USD"], "timeframes": ["1d"]}
    assert client.post("/api/research", json=bad, headers=h).status_code == 400
    client.post("/api/research", json=bad | {"brief": "x", "dossier_id": "d-001"}, headers=h)
    client.post("/api/research/d-001/close", json={"reason": "x"}, headers=h)
    assert client.post("/api/research/d-001/pause", json={}, headers=h).status_code == 409


def test_changes_need_a_json_body(env) -> None:
    """A form posted from another site can't be JSON without a CORS preflight,
    which this API never answers."""
    client, token, _ = env
    r = client.post(
        "/api/research",
        data={"brief": "x", "market": "crypto"},
        headers=auth(token) | {"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 415


def test_no_endpoint_can_touch_orders_or_risk(env) -> None:
    client, _, _ = env
    paths = {r.path for r in client.app.routes}
    for word in ("order", "kill", "risk", "intent", "verdict", "config", "budget"):
        assert not any(word in p for p in paths), (word, paths)


def test_paper_and_ledger_views(env) -> None:
    client, token, state = env
    led = Ledger(state / "ledger.sqlite")
    for i in range(5):
        led.append(LedgerEventType.FEED_STATUS, f"feed {i}", {"provider": "p", "symbol": "s",
                   "status": "fresh", "detail": "", "at": "2026-11-20T00:00:00+00:00"})  # fmt: skip
    h = auth(token)
    paper = client.get("/api/paper", headers=h).json()
    assert paper["ledger"]["events"] == 5 and paper["feeds"][0]["status"] == "fresh"
    page = client.get("/api/ledger", params={"after": 1, "limit": 2}, headers=h).json()
    assert [e["sequence"] for e in page] == [2, 3]
    assert page[0]["type"] == "feed_status" and page[0]["reason"] == "feed 2"


def test_the_live_socket_pushes_new_ledger_events(env) -> None:
    client, token, state = env
    led = Ledger(state / "ledger.sqlite")
    led.append(LedgerEventType.FEED_STATUS, "before", {"status": "fresh"})
    from starlette.websockets import WebSocketDisconnect

    with (  # no token: the socket is closed before it is accepted
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/live") as ws,
    ):
        ws.receive_json()
    client.get("/login", params={"token": token})
    with client.websocket_connect("/api/live") as ws:
        led.append(LedgerEventType.FEED_STATUS, "after", {"status": "stale"})
        msg = ws.receive_json()
        assert msg["reason"] == "after" and msg["type"] == "feed_status"  # only new ones


def test_serving_is_loopback_only(tmp_path, capsys) -> None:
    from firm.control.cli import main

    code = main(["dashboard", "serve", "--host", "0.0.0.0", "--state-root", str(tmp_path)])
    assert code == 3 and "loopback" in capsys.readouterr().err


def test_url_prints_a_one_click_local_login(tmp_path, capsys) -> None:
    from firm.control.cli import main

    assert main(["dashboard", "url", "--state-root", str(tmp_path), "--port", "8765"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == f"http://127.0.0.1:8765/login?token={load_or_create_token(tmp_path)}"
