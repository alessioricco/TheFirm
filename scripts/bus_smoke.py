"""Check the bus against a real nats-server (the unit tests use an in-memory bus).

Starts a throwaway server exactly as `firm bus up` does (generated config, one
login per service) on a free localhost port, with its state in a temporary
folder, then checks:

- every subject round-trips from its owner, each publisher on its own login;
- a retried publish lands once; a durable consumer resumes after a restart;
  a failing handler gets the message again;
- the server itself enforces the permission table: an agent's login can't
  publish an order (even bypassing the client's own check) nor read another
  desk's deliveries, a service can't delete or purge the stream, and a wrong
  password is refused.

    uv run python scripts/bus_smoke.py
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from bus_samples import SAMPLES
from firm.bus import BusClient, encode
from firm.bus.auth import ADMIN
from firm.bus.nats import NatsBus
from firm.bus.server import BusServerError, start
from firm.core.config import BusConfig, load_bus
from firm.core.messages import PUBLISHERS, Envelope, Service, Subject, wrap

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def wait_for(cond, seconds: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    end = time.monotonic() + seconds
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError("timed out waiting for delivery")
        await asyncio.sleep(0.02)


async def refused(coro) -> str:  # type: ignore[no-untyped-def]
    try:
        await coro
    except Exception as exc:
        return type(exc).__name__
    raise AssertionError("the server accepted it")


async def check(cfg: BusConfig, state: Path) -> None:
    buses: dict[str, NatsBus] = {}

    async def bus(user: Service | str) -> NatsBus:
        name = user.value if isinstance(user, Service) else user
        if name not in buses:
            buses[name] = await NatsBus.for_service(cfg, state, user)
        return buses[name]

    await (await bus(ADMIN)).ensure_stream()
    got: list[Envelope] = []

    async def inbox(env: Envelope) -> None:
        got.append(env)

    await BusClient(await bus(Service.LEDGER), Service.LEDGER).subscribe(">", "all", inbox)
    for subject in Subject:
        owner = PUBLISHERS[subject]
        assert owner is not None
        await BusClient(await bus(owner), owner).publish(
            subject, SAMPLES[subject], message_id=f"m-{subject}"
        )
    await wait_for(lambda: len(got) == len(Subject))
    assert all(e.typed_payload() == SAMPLES[e.subject] for e in got)
    print(f"ok  every subject round-trips from its owner's own login ({len(got)} subjects)")

    risk_bus = await bus(Service.RISK)
    risk, beat = BusClient(risk_bus, Service.RISK), SAMPLES[Subject.RISK_HEARTBEAT]
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="m-risk.heartbeat")  # retry
    await asyncio.sleep(0.3)
    assert len(got) == len(Subject)
    print("ok  a retried publish lands once (Nats-Msg-Id)")

    beats: list[str] = []

    async def hb(env: Envelope) -> None:
        beats.append(env.message_id)

    exe_bus = await bus(Service.EXECUTION)
    sub = await BusClient(exe_bus, Service.EXECUTION).subscribe("risk.heartbeat", "hb", hb)
    await wait_for(lambda: beats == ["m-risk.heartbeat"])
    await sub.close()
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-2")
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-3")
    await BusClient(exe_bus, Service.EXECUTION).subscribe("risk.heartbeat", "hb", hb)
    await wait_for(lambda: len(beats) == 3)
    assert beats == ["m-risk.heartbeat", "b-2", "b-3"]
    print("ok  a durable consumer resumes after a restart: nothing lost, nothing twice")

    calls: list[str] = []

    async def flaky(env: Envelope) -> None:
        calls.append(env.message_id)
        if len(calls) == 1:
            raise RuntimeError("ledger busy")

    ledger_bus = await bus(Service.LEDGER)
    await BusClient(ledger_bus, Service.LEDGER).subscribe("fill.executed", "flaky", flaky)
    await wait_for(lambda: len(calls) >= 2)
    assert calls[:2] == ["m-fill.executed", "m-fill.executed"]
    print("ok  a failing handler gets the message again")

    # --- the server enforces the org chart, not only the client
    before = (await (await bus(ADMIN)).js.stream_info(cfg.stream)).state.messages
    research = await bus(Service.RESEARCH)
    order = encode(
        wrap(
            message_id="forged-1",
            sender=Service.EXECUTION,
            subject=Subject.ORDER_INTENT,
            payload=SAMPLES[Subject.ORDER_INTENT],
            timestamp=SAMPLES[Subject.ORDER_INTENT].created_at,  # type: ignore[attr-defined]
        )
    )
    research.cfg = cfg.model_copy(update={"publish_timeout_seconds": 1.0})
    why = await refused(research.publish("order.intent", order, message_id="forged-1"))
    after = (await (await bus(ADMIN)).js.stream_info(cfg.stream)).state.messages
    assert after == before
    print(f"ok  the research login can't publish order.intent, even raw ({why}; stream unchanged)")

    snooped: list[str] = []

    async def snoop(msg) -> None:  # type: ignore[no-untyped-def]
        snooped.append(msg.subject)

    await research.nc.subscribe("_INBOX.ledger.>", cb=snoop)  # another desk's deliveries
    await research.nc.subscribe("fill.executed", cb=snoop)  # around JetStream
    seen = len(got)
    await BusClient(exe_bus, Service.EXECUTION).publish(
        Subject.FILL_EXECUTED, SAMPLES[Subject.FILL_EXECUTED], message_id="f-snoop"
    )
    await wait_for(lambda: len(got) == seen + 1)  # the ledger got it...
    await asyncio.sleep(0.3)
    assert snooped == []  # ...the research login saw nothing
    print("ok  the research login can't read the ledger's deliveries nor firm subjects directly")

    why = await refused(risk_bus.js.delete_stream(cfg.stream))
    why2 = await refused(risk_bus.js.purge_stream(cfg.stream))
    print(f"ok  a service login can't delete ({why}) or purge ({why2}) the stream")

    why = await refused(
        asyncio.wait_for(NatsBus.connect(cfg, user="execution", password="wrong"), timeout=3)
    )
    print(f"ok  a wrong password is refused ({why})")

    for b in buses.values():
        await b.close()


def main() -> int:
    port = free_port()
    cfg = load_bus(ROOT / "config").model_copy(update={"port": port})
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        try:
            proc = start(cfg, state, state / "logs" / "bus.log")
        except BusServerError as exc:
            print(f"error: {exc}")
            return 1
        try:
            asyncio.run(check(cfg, state))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    print("bus smoke: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
