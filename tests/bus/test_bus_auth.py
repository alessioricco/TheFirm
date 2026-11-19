"""Per-service bus logins: the server-side copy of the permission table
(golden rule 12). `scripts/bus_smoke.py` checks a real server enforces it."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from firm.bus.auth import (
    ADMIN,
    USERS,
    credentials_path,
    load_or_create_credentials,
    login,
    permissions,
    server_config,
    write_server_config,
)
from firm.bus.consumers import consumers_of
from firm.bus.nats import stream_config
from firm.core.config import load_bus
from firm.core.messages import AGENT_SERVICES, PUBLISHERS, Service, Subject

CONFIG = Path(__file__).resolve().parents[2] / "config"
FIRM_SUBJECTS = {s.value for s in Subject}


def firm_subjects_allowed(user: str) -> set[str]:
    allow = permissions(user, "FIRM")["publish"]["allow"]
    return {s for s in allow if not s.startswith(("$JS.", "_INBOX."))}


@pytest.mark.parametrize("service", list(Service))
def test_each_service_may_publish_exactly_its_own_subjects(service: Service) -> None:
    owned = {s.value for s, owner in PUBLISHERS.items() if owner is service}
    assert firm_subjects_allowed(service.value) == owned


@pytest.mark.parametrize("agent", sorted(AGENT_SERVICES))
def test_agents_get_no_order_risk_fill_verdict_alert_or_control_subject(agent: Service) -> None:
    allowed = firm_subjects_allowed(agent.value)
    forbidden = ("order.", "risk.", "fill.", "verdict.", "alert.", "control.", "digest.")
    assert not [s for s in allowed if s.startswith(forbidden)]


@pytest.mark.parametrize("user", USERS)
def test_no_wildcard_reaches_a_firm_subject_and_no_service_manages_streams(user: str) -> None:
    perms = permissions(user, "FIRM")
    publish = perms["publish"]["allow"]
    assert all(p.startswith(("$JS.", "_INBOX.")) or p in FIRM_SUBJECTS for p in publish)
    assert ">" not in publish and "*" not in publish
    # receive only via JetStream, and only its own deliveries
    assert perms["subscribe"]["allow"] == [f"_INBOX.{user}.>"]
    if user != ADMIN:
        stream_admin = ("$JS.API.STREAM.CREATE", "$JS.API.STREAM.UPDATE", "$JS.API.STREAM.DELETE")
        assert not [p for p in publish if p.startswith(stream_admin) or p == "$JS.API.>"]
        mine = {c.name for c in consumers_of(Service(user))}
        for p in publish:
            if p.startswith("$JS.API.CONSUMER."):
                assert p.startswith("$JS.API.CONSUMER.INFO.FIRM.") and p.rsplit(".", 1)[1] in mine
            elif p.startswith("$JS.ACK."):
                assert p.split(".")[3] in mine and p.split(".")[2] == "FIRM"
            elif p.startswith("$JS."):
                assert p in ("$JS.API.INFO", "$JS.API.STREAM.INFO.FIRM")


def test_admin_publishes_no_firm_subject() -> None:
    assert firm_subjects_allowed(ADMIN) == set()


def test_credentials_are_private_stable_and_complete(tmp_path: Path) -> None:
    first = load_or_create_credentials(tmp_path)
    assert set(first) == set(USERS) and len(set(first.values())) == len(USERS)
    mode = stat.S_IMODE(credentials_path(tmp_path).stat().st_mode)
    assert mode == 0o600
    assert load_or_create_credentials(tmp_path) == first  # kept across runs

    data = json.loads(credentials_path(tmp_path).read_text())
    del data["risk"]
    credentials_path(tmp_path).write_text(json.dumps(data))
    again = load_or_create_credentials(tmp_path)
    assert again["execution"] == first["execution"] and again["risk"] != first["risk"]
    assert login(tmp_path, Service.EXECUTION) == ("execution", first["execution"])


def test_login_without_credentials_says_how_to_create_them(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="firm bus up"):
        login(tmp_path, Service.RISK)


def test_server_config_is_private_and_lists_every_user(tmp_path: Path) -> None:
    cfg = load_bus(CONFIG)
    path = write_server_config(cfg, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    conf = json.loads(path.read_text())
    assert [u["user"] for u in conf["authorization"]["users"]] == list(USERS)
    assert conf["host"] == cfg.host and conf["port"] == cfg.port
    assert conf == server_config(
        cfg, load_or_create_credentials(tmp_path), tmp_path / "bus" / "jetstream"
    )


def test_bus_config_stays_on_loopback_and_the_stream_holds_only_firm_subjects() -> None:
    cfg = load_bus(CONFIG)
    assert cfg.host == "127.0.0.1"  # everything runs on one machine for now
    sc = stream_config(cfg)
    assert set(sc.subjects) == FIRM_SUBJECTS
    assert sc.deny_delete and sc.deny_purge
    assert sc.max_age == cfg.max_age_days * 86_400
