"""Check the bus against a real nats-server (the unit tests use an in-memory bus).

Starts a throwaway `nats-server -js` on a free localhost port with its store in
a temporary folder, runs the same behaviour the tests pin (every subject
round-trips from its owner, publish dedupe, durable resume, redelivery after a
failing handler), prints what it saw, and stops the server.

    uv run python scripts/bus_smoke.py
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from bus_samples import SAMPLES
from firm.bus import BusClient
from firm.bus.nats import NatsBus
from firm.core.messages import PUBLISHERS, Envelope, Service, Subject


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


async def check(url: str) -> None:
    bus = await NatsBus.connect(url, redeliver_seconds=0.2)
    got: list[Envelope] = []

    async def inbox(env: Envelope) -> None:
        got.append(env)

    ledger = BusClient(bus, Service.LEDGER)
    await ledger.subscribe(">", "all", inbox)
    for subject in Subject:
        owner = PUBLISHERS[subject]
        assert owner is not None
        await BusClient(bus, owner).publish(subject, SAMPLES[subject], message_id=f"m-{subject}")
    await wait_for(lambda: len(got) == len(Subject))
    assert all(e.typed_payload() == SAMPLES[e.subject] for e in got)
    print(f"ok  every subject round-trips from its owner ({len(got)} subjects)")

    risk, beat = BusClient(bus, Service.RISK), SAMPLES[Subject.RISK_HEARTBEAT]
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="m-risk.heartbeat")  # retry
    await asyncio.sleep(0.3)
    assert len(got) == len(Subject)
    print("ok  a retried publish lands once (Nats-Msg-Id)")

    beats: list[str] = []

    async def hb(env: Envelope) -> None:
        beats.append(env.message_id)

    execution = BusClient(bus, Service.EXECUTION)
    sub = await execution.subscribe("risk.heartbeat", "hb", hb)
    await wait_for(lambda: beats == ["m-risk.heartbeat"])
    await sub.close()
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-2")
    await risk.publish(Subject.RISK_HEARTBEAT, beat, message_id="b-3")
    await BusClient(bus, Service.EXECUTION).subscribe("risk.heartbeat", "hb", hb)
    await wait_for(lambda: len(beats) == 3)
    assert beats == ["m-risk.heartbeat", "b-2", "b-3"]
    print("ok  a durable consumer resumes after a restart: nothing lost, nothing twice")

    calls: list[str] = []

    async def flaky(env: Envelope) -> None:
        calls.append(env.message_id)
        if len(calls) == 1:
            raise RuntimeError("ledger busy")

    await BusClient(bus, Service.LEDGER).subscribe("fill.executed", "flaky", flaky)
    await wait_for(lambda: len(calls) >= 2)
    assert calls[:2] == ["m-fill.executed", "m-fill.executed"]
    print("ok  a failing handler gets the message again")

    await bus.close()


def main() -> int:
    exe = shutil.which("nats-server")
    if exe is None:
        print("nats-server is not installed (see README, Quick start)")
        return 1
    port = free_port()
    with tempfile.TemporaryDirectory() as store:
        proc = subprocess.Popen(
            [exe, "-js", "-a", "127.0.0.1", "-p", str(port), "-sd", store],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            asyncio.run(check(f"nats://127.0.0.1:{port}"))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    print("bus smoke: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
