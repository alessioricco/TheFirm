"""The dashboard reviews the senior quant's proposals (owner, 2026-11-20).

Read them, approve one (a dossier opens with it as its brief) or reject it
with a reason: each an OWNER_COMMAND "via dashboard", behind the token and a
JSON body, the same rules as the CLI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from firm.control.api import create_app, load_or_create_token
from firm.core.domain import LedgerEventType
from firm.ledger.sink import ledger_path
from firm.ledger.store import Ledger
from firm.research.dossier import DossierStore
from firm.research.proposals import BriefProposal, ProposalStore
from research.test_proposals import GOOD
from verify_helpers import REPO


@pytest.fixture
def env(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    app = create_app(config_root=REPO / "config", data_root=data, state_root=state)
    key = load_or_create_token(state)
    store = ProposalStore(data)
    first, _ = store.add(BriefProposal.model_validate(GOOD))
    second, _ = store.add(BriefProposal.model_validate(GOOD | {"title": "Another idea on 1d"}))
    client = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    return client, data, state, first.proposal_id, second.proposal_id


def via_dashboard(state) -> list[str]:
    return [
        e.payload["command"]
        for e in Ledger(ledger_path(state)).read()
        if e.event_type == LedgerEventType.OWNER_COMMAND and e.payload.get("via") == "dashboard"
    ]


def test_list_and_show_need_the_token(env, tmp_path) -> None:
    client, _data, _state, pid, _ = env
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/proposals").status_code == 401
    r = client.get("/api/proposals")
    assert r.status_code == 200
    items = r.json()["proposals"]
    assert [p["status"] for p in items] == ["proposed", "proposed"]
    one = client.get(f"/api/proposals/{pid}").json()
    assert one["proposal"]["hypothesis"] == GOOD["hypothesis"] and "Falsified if:" in one["brief"]
    assert client.get("/api/proposals/p-nope").status_code == 404


def test_approve_opens_a_dossier_once(env) -> None:
    client, data, state, pid, _ = env
    assert client.post(f"/api/proposals/{pid}/approve", content=b"{}").status_code == 415
    r = client.post(f"/api/proposals/{pid}/approve", json={"dossier_id": "d-ui1"})
    assert r.status_code == 200, r.text
    assert (
        r.json()["proposal"]["status"] == "approved"
        and r.json()["dossier"]["dossier_id"] == "d-ui1"
    )
    d = DossierStore(data).get("d-ui1")
    assert d.market == "crypto" and GOOD["title"] in d.brief
    again = client.post(f"/api/proposals/{pid}/approve", json={})
    assert again.status_code == 409 and "already approved" in again.json()["detail"]
    assert via_dashboard(state) == ["research proposals approve"]


def test_reject_needs_a_reason(env) -> None:
    client, _data, state, _pid, other = env
    assert client.post(f"/api/proposals/{other}/reject", json={"reason": ""}).status_code == 422
    r = client.post(f"/api/proposals/{other}/reject", json={"reason": "already tested"})
    assert r.status_code == 200 and r.json()["rejected_reason"] == "already tested"
    assert client.post(f"/api/proposals/{other}/approve", json={}).status_code == 409
    assert via_dashboard(state) == ["research proposals reject"]
