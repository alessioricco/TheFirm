"""The kill switch, end to end (M3 plan, M3b; SPEC "Kill switches: per market and global").

- The owner's KILL reaches the risk desk over the bus (`control.command`,
  publishable only by the control service) and the desk halts: every new
  proposal on that market (or every market) is rejected. RESUME releases it.
- A kill survives a restart: the paper run's risk desk replays the owner's
  commands from the ledger when it starts, so a run restarted while killed
  stays killed, including after a kill sent while it was down.
- Issuing a command records it in the ledger first, then publishes it; with
  no bus it is still recorded (and applies at the next start).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from test_bus_risk import on_bus
from test_worker import T0, Rig

from firm.bus import BusClient, MemoryBus
from firm.bus.client import BusError
from firm.bus.consumers import CONSUMERS
from firm.control.killswitch import issue, kill_state, owner_commands
from firm.core.domain import LedgerEventType
from firm.core.messages import PUBLISHERS, Service, Subject
from firm.core.messages.subjects import HaltScope, OwnerCommand, OwnerCommandKind
from firm.ledger.store import Ledger


def command(kind: str, scope: str = "market", market: str | None = "crypto", n: int = 1):
    return OwnerCommand(
        command_id=f"k-{n}",
        kind=OwnerCommandKind(kind),
        scope=HaltScope(scope),
        market=market if scope == "market" else None,
        reason="owner: test",
        issued_at=T0 + timedelta(minutes=n),
    )


def test_only_the_control_service_can_command_and_risk_listens() -> None:
    assert PUBLISHERS[Subject.CONTROL_COMMAND] is Service.CONTROL
    c = CONSUMERS["risk-commands"]
    assert (c.service, c.filter) == (Service.RISK, "control.command")


def test_a_kill_over_the_bus_stops_new_orders_and_resume_releases_it() -> None:
    rig = Rig()
    bus = on_bus(rig)
    control = BusClient(bus, Service.CONTROL)
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    asyncio.run(control.publish(Subject.CONTROL_COMMAND, command("kill"), message_id="k-1"))
    assert rig.risk.halted("crypto")
    out = rig.bar(2, 50_100)
    assert out.action in ("rejected", "skip") and "kill" in out.reason
    assert rig.sim.fills() == []
    halts = [p for t, _r, p, _i in rig.sink.events if t is LedgerEventType.RISK_HALT]
    assert halts and halts[0]["reason"].startswith("owner kill")

    asyncio.run(control.publish(Subject.CONTROL_COMMAND, command("resume", n=2), message_id="k-2"))
    assert not rig.risk.halted("crypto")
    assert rig.bar(3, 50_200).action == "submitted"


def test_a_global_kill_halts_every_market() -> None:
    rig = Rig()
    on_bus(rig)
    rig.risk.command(command("kill", scope="global"))
    assert rig.risk.halted("crypto") and rig.risk.halted("us_equities")
    rig.risk.command(command("resume", scope="global", n=2))
    assert not rig.risk.halted("crypto")


def test_nobody_else_can_publish_a_command() -> None:
    bus = MemoryBus()
    for service in (Service.EXECUTION, Service.RESEARCH, Service.RISK, Service.NOTIFIER):
        client = BusClient(bus, service)
        with pytest.raises(BusError):
            asyncio.run(client.publish(Subject.CONTROL_COMMAND, command("kill"), message_id="x"))
    assert bus.log == []


def test_a_restarted_desk_stays_killed(tmp_path) -> None:
    led = Ledger(tmp_path / "ledger.sqlite")
    asyncio.run(
        issue(led, command("kill", n=1), publish=None, via="cli")
    )  # sent while the run was down
    asyncio.run(issue(led, command("kill", market="us_equities", n=2), publish=None, via="cli"))
    asyncio.run(issue(led, command("resume", market="us_equities", n=3), publish=None, via="cli"))
    rig = Rig()
    rig.risk.restore(owner_commands(led))
    assert rig.risk.halted("crypto") and not rig.risk.halted("us_equities")
    rig.bar(0, 50_000)
    rig.bar(1, 50_000)
    assert rig.bar(2, 50_100).action != "submitted"


def test_issuing_records_first_then_publishes(tmp_path) -> None:
    led = Ledger(tmp_path / "ledger.sqlite")
    sent = []

    async def publish(cmd: OwnerCommand) -> None:
        sent.append(cmd)

    assert asyncio.run(issue(led, command("kill"), publish=publish, via="dashboard")) is True
    assert [c.command_id for c in sent] == ["k-1"]
    (event,) = [e for e in led.read() if e.event_type is LedgerEventType.OWNER_COMMAND]
    assert event.payload["kind"] == "kill" and event.payload["via"] == "dashboard"

    async def down(cmd: OwnerCommand) -> None:
        raise ConnectionError("bus down")

    assert asyncio.run(issue(led, command("kill", n=2), publish=down, via="cli")) is False
    assert len(owner_commands(led)) == 2  # recorded even though the bus was down


def test_the_kill_state_is_the_latest_command_per_scope(tmp_path) -> None:
    led = Ledger(tmp_path / "ledger.sqlite")
    assert kill_state(led) == {"global": None, "markets": {}}
    asyncio.run(issue(led, command("kill", n=1), publish=None, via="cli"))
    asyncio.run(issue(led, command("kill", scope="global", n=2), publish=None, via="cli"))
    state = kill_state(led)
    assert state["markets"]["crypto"]["command_id"] == "k-1"
    assert state["global"]["command_id"] == "k-2"
    asyncio.run(issue(led, command("resume", scope="global", n=3), publish=None, via="cli"))
    assert kill_state(led)["global"] is None and "crypto" in kill_state(led)["markets"]


def test_the_paper_run_replays_the_kill_switches_before_trading() -> None:
    """Wiring: the run's risk desk restores the owner's commands from its ledger
    at start (the behaviour itself is tested above, on the desk)."""
    import inspect

    from firm.control.paper import PaperRun

    source = inspect.getsource(PaperRun._run)
    assert "desk.restore(owner_commands(ledger))" in source
    assert source.index("desk.restore(") < source.index("MarketWorker(")


def test_the_owner_hears_the_kill_and_its_release() -> None:
    from firm.core.config import NotifyConfig
    from firm.core.domain import NotificationLevel
    from firm.notify.alerts import alert_for

    cfg = NotifyConfig(notify="default", throttle_seconds=300, attempts=1, retry_seconds=0,
                       drain_seconds=1, announce_start_stop=True)  # fmt: skip
    halt = alert_for(LedgerEventType.RISK_HALT, "owner kill: drill",
                     {"scope": "market", "market": "crypto"}, cfg)  # fmt: skip
    assert halt.level is NotificationLevel.URGENT
    applied_resume = command("resume").model_dump(mode="json")
    released = alert_for(LedgerEventType.OWNER_COMMAND, "resume: drill", applied_resume, cfg)
    assert released.level is NotificationLevel.ACTION and "released" in released.text
    assert released.key == f"{halt.key}:ok"
    requested = applied_resume | {"requested": True}  # the CLI's record, not the desk's
    assert alert_for(LedgerEventType.OWNER_COMMAND, "owner resume", requested, cfg) is None


def _dashboard(tmp_path, desk=None):
    from fastapi.testclient import TestClient

    from firm.control.api import create_app, load_or_create_token
    from verify_helpers import REPO

    def factory(_config, _state):
        async def publish(cmd):
            if desk is None:
                raise ConnectionError("bus down")
            desk.command(cmd)

        return publish

    state = tmp_path / "state"
    app = create_app(config_root=REPO / "config", data_root=tmp_path / "data", state_root=state,
                     publish_factory=factory, kill_wait_seconds=2)  # fmt: skip
    client = TestClient(app)
    client.get("/login", params={"token": load_or_create_token(state)})
    return client, state


def test_the_dashboard_kill_switch_needs_the_typed_confirmation(tmp_path) -> None:
    from firm.core.config import load_risk
    from firm.ledger.sink import LedgerSink
    from firm.risk.desk import RiskDesk
    from verify_helpers import REPO

    (tmp_path / "state").mkdir()
    desk = RiskDesk(load_risk(REPO / "config" / "risk" / "default.yaml"),
                    LedgerSink(Ledger(tmp_path / "state" / "ledger.sqlite")))  # fmt: skip
    client, state = _dashboard(tmp_path, desk)
    body = {"action": "kill", "market": "crypto", "reason": "drill"}
    assert client.post("/api/killswitch", json=body | {"confirm": "crypt"}).status_code == 400
    assert not desk.halted("crypto")
    r = client.post("/api/killswitch", json=body | {"confirm": "crypto"}).json()
    assert r["sent"] and r["confirmed"] and desk.halted("crypto")
    assert "crypto" in client.get("/api/killswitch").json()["state"]["markets"]
    (req,) = [
        e for e in Ledger(state / "ledger.sqlite").read() if e.event_id.startswith("request:")
    ]
    assert req.payload["via"] == "dashboard"
    everything = {"action": "kill", "market": None, "reason": "drill", "confirm": "crypto"}
    assert client.post("/api/killswitch", json=everything).status_code == 400  # needs ALL
    resume = {"action": "resume", "market": "crypto", "reason": "over", "confirm": "crypto"}
    assert client.post("/api/killswitch", json=resume).json()["confirmed"]
    assert not desk.halted("crypto")
    assert client.post("/api/killswitch", json=resume).status_code == 409  # nothing to resume


def test_with_the_bus_down_the_dashboard_says_so(tmp_path) -> None:
    client, _ = _dashboard(tmp_path, desk=None)
    r = client.post("/api/killswitch", json={"action": "kill", "market": None,
                    "reason": "drill", "confirm": "ALL"}).json()  # fmt: skip
    assert r["sent"] is False and r["confirmed"] is False
    assert r["state"]["global"]["reason"] == "drill"  # recorded: applies at the next start
    anon_form = client.post("/api/killswitch", content=b"action=kill")
    assert anon_form.status_code == 415
