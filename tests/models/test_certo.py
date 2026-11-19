"""The firm's Certo client: one MCP server, one task at a time, local models only.

A fake `certo mcp` (tests/models/fake_certo_mcp.py) speaks the protocol with
Certo's documented shapes, so no network and no model.

- **One server**: a lock on the workspace refuses a second server while the
  first runs (another process included); it is released when the server
  stops or its process dies.
- **One task at a time** (a local Ollama serves one model at a time).
- **The egress gate** (firm rule 8): a task runs only on a local provider's
  model, named explicitly, never escalating up a ladder; a refusal is logged.
- **Every task is a MODEL_CALL** in the ledger, with its dossier, tokens and
  time, so the dossier's budget sees it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from firm.core.config import load_models_config
from firm.core.domain import LedgerEventType
from firm.core.events import CollectingSink
from firm.models.certo import CertoBusy, CertoError, CertoServer
from firm.models.gateway import EgressRefused
from verify_helpers import REPO

FAKE = [sys.executable, str(Path(__file__).with_name("fake_certo_mcp.py"))]


def server(tmp_path, monkeypatch, *, model="ollama/qwen3.8:27b", sink=None, **env) -> CertoServer:
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CERTO_LOG", str(log))
    for k, v in env.items():
        monkeypatch.setenv(f"FAKE_CERTO_{k.upper()}", str(v))
    return CertoServer(
        FAKE,
        tmp_path / "workspace",
        models=load_models_config(REPO / "config"),
        model=model,
        sink=sink or CollectingSink(),
        poll_seconds=0.01,
    )


def calls(tmp_path) -> list[dict]:
    path = tmp_path / "calls.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_it_starts_certo_and_checks_its_version(tmp_path, monkeypatch) -> None:
    async def go():
        async with server(tmp_path, monkeypatch) as s:
            return s.version

    assert asyncio.run(go()) == "0.2.5"

    async def old():
        async with server(tmp_path, monkeypatch, version="0.2.3"):
            pass

    with pytest.raises(CertoError, match=r"0\.2\.4"):
        asyncio.run(old())


def test_only_one_server_per_workspace(tmp_path, monkeypatch) -> None:
    async def go():
        first = server(tmp_path, monkeypatch)
        await first.start()
        with pytest.raises(CertoBusy, match="already running"):
            await server(tmp_path, monkeypatch).start()
        await first.close()
        second = server(tmp_path, monkeypatch)  # free again once the first stopped
        await second.start()
        await second.close()

    asyncio.run(go())


def test_the_lock_is_released_when_its_process_dies(tmp_path, monkeypatch) -> None:
    (tmp_path / "workspace").mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from firm.models.certo import WorkspaceLock;"
         f"WorkspaceLock({str(tmp_path / 'workspace')!r}).acquire(); print('held', flush=True);"
         "time.sleep(60)"],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert holder.stdout.readline().strip() == "held"

    async def blocked():
        await server(tmp_path, monkeypatch).start()

    with pytest.raises(CertoBusy):
        asyncio.run(blocked())
    holder.kill()
    holder.wait()

    async def free():
        s = server(tmp_path, monkeypatch)
        await s.start()
        await s.close()

    asyncio.run(free())


def test_a_task_runs_on_the_named_local_model_and_never_escalates(tmp_path, monkeypatch) -> None:
    sink = CollectingSink()

    async def go():
        async with server(tmp_path, monkeypatch, sink=sink, polls=2) as s:
            run_id = await s.run_task(
                "d-001", "write strategy.py and its tests", dossier_id="d-001"
            )
            with pytest.raises(CertoBusy, match="one task at a time"):
                await s.run_task("d-002", "another", dossier_id="d-002")
            return await s.wait(run_id)

    status = asyncio.run(go())
    assert status["state"] == "finished" and status["result"]["verdict"] == "verified"
    (task,) = [c for c in calls(tmp_path) if c["tool"] == "run_task"]
    args = task["args"]
    assert args["model"] == "ollama/qwen3.8:27b" and args["project"] == "d-001"
    assert not args.get("escalate") and not args.get("auto_model")
    assert not args.get("independent_tests")  # they double a run's cost
    (rec,) = [p for t, _r, p, _i in sink.events if t is LedgerEventType.MODEL_CALL]
    assert (rec["outcome"], rec["dossier_id"], rec["tag"], rec["local"]) == (
        "ok",
        "d-001",
        "private",
        True,
    )
    assert (rec["input_tokens"], rec["output_tokens"], rec["cost_gbp"]) == (10000, 2000, 0.0)
    assert rec["seconds"] == 42.5 and rec["run_id"] and rec["verdict"] == "verified"
    assert "write strategy.py" not in json.dumps(rec)  # only a hash of the task


def test_a_cloud_model_is_refused_before_anything_is_sent(tmp_path, monkeypatch) -> None:
    sink = CollectingSink()

    async def go():
        async with server(tmp_path, monkeypatch, model="anthropic/claude-opus-5-5", sink=sink) as s:
            await s.run_task("d-001", "write code", dossier_id="d-001")

    with pytest.raises(EgressRefused, match="local"):
        asyncio.run(go())
    assert [c for c in calls(tmp_path) if c["tool"] == "run_task"] == []
    (rec,) = [p for t, _r, p, _i in sink.events if t is LedgerEventType.MODEL_CALL]
    assert rec["outcome"] == "refused" and rec["local"] is False


def test_the_ledger_charges_the_dossier(tmp_path, monkeypatch) -> None:
    from firm.ledger.sink import LedgerSink
    from firm.ledger.spend import dossier_model_usage
    from firm.ledger.store import Ledger

    ledger = Ledger(tmp_path / "ledger.sqlite")

    async def go():
        async with server(tmp_path, monkeypatch, sink=LedgerSink(ledger)) as s:
            await s.wait(await s.run_task("d-007", "write code", dossier_id="d-007"))

    asyncio.run(go())
    assert dossier_model_usage(ledger, "d-007") == (12000, 0.0)


def test_events_stream_while_waiting(tmp_path, monkeypatch) -> None:
    seen: list[str] = []

    async def go():
        async with server(tmp_path, monkeypatch, polls=3) as s:
            run_id = await s.run_task("d-001", "task", dossier_id="d-001")
            await s.wait(run_id, on_event=lambda e: seen.append(e["text"]))

    asyncio.run(go())
    assert seen == ["step 0", "step 1", "step 2"]


def test_tool_errors_and_a_dead_server_are_errors(tmp_path, monkeypatch) -> None:
    async def unknown():
        async with server(tmp_path, monkeypatch) as s:
            await s.status("nope")

    with pytest.raises(CertoError, match="no run with id"):
        asyncio.run(unknown())

    async def crash():
        async with server(tmp_path, monkeypatch, die_on="run_status") as s:
            await s.status("r1")

    with pytest.raises(CertoError, match="stopped"):
        asyncio.run(crash())
