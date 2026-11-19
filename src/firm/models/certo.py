"""The firm's Certo client: one MCP server, one task at a time, local models only.

Certo writes code with an agent on a local model, then runs the project's
checks and gives a verdict (docs/CERTO.md). Certo calls the model itself, so
this is a model-calling path, and like every other it lives in `firm.models`
and passes the data-egress gate (firm rule 8):

- **One server.** `start()` takes an exclusive lock on the workspace
  (`.firm-certo.lock`) before it launches `scripts/certo-mcp`; a second
  server, from this process or another, is refused (`CertoBusy`). The lock
  goes when the server stops, or when its process dies (the OS releases it).
- **One task at a time**: a local Ollama serves one model at a time.
- **Local models only**, named on every task (`model`), never `auto_model`
  or `escalate` (a ladder could climb to a cloud model), and no
  independent tests (they double a run's cost).
- **Every task is a MODEL_CALL** in the ledger once it ends: dossier, run
  ID, verdict, tokens, seconds (and a refusal is one too), so the
  dossier's budget sees it. Never the task text: a hash of it.

The server speaks MCP (JSON-RPC, one message per line) over stdio; Certo's
tools and their shapes are in Certo's docs (`use-certo-over-mcp.md`,
`json-result.md`). A run lives in the server's memory: if the server stops
mid-run, `resume` continues the project's last run.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firm.core.config import ModelsConfig
from firm.core.domain import LedgerEventType
from firm.core.events import NULL_SINK, EventSink
from firm.models.gateway import EgressRefused

MIN_VERSION = (0, 2, 4)
LINE_LIMIT = 32 << 20  # a run's result can be long (checks' output tails)
LOCK_NAME = ".firm-certo.lock"


class CertoError(RuntimeError):
    """Certo refused a call, or its server stopped."""


class CertoBusy(CertoError):
    """Another server holds the workspace, or a task is already running."""


class WorkspaceLock:
    """An exclusive lock on the workspace, held while this process's server runs."""

    def __init__(self, workspace: Path | str) -> None:
        self.path = Path(workspace) / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.read(fd, 64).decode(errors="replace").strip() or "?"
            os.close(fd)
            raise CertoBusy(
                f"a Certo server is already running for {self.path.parent} (pid {holder}): "
                "the firm uses one at a time"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            self._fd = None


def _version(text: str) -> tuple[int, ...]:
    parts = []
    for p in text.split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


class CertoServer:
    def __init__(
        self,
        command: Sequence[str],
        workspace: Path,
        *,
        models: ModelsConfig,
        model: str,
        sink: EventSink = NULL_SINK,
        poll_seconds: float = 5.0,
        call_timeout: float = 120.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """`command` starts `certo mcp` (normally `scripts/certo-mcp`); `model` is
        `provider/model`: the research role's model from `config/models.yaml`."""
        self.command, self.workspace = list(command), Path(workspace)
        self.models, self.model, self.sink = models, model, sink
        self.poll_seconds, self.call_timeout, self.clock = poll_seconds, call_timeout, clock
        self.version: str | None = None
        self._lock = WorkspaceLock(self.workspace)
        self._proc: asyncio.subprocess.Process | None = None
        self._ids = 0
        self._active: dict[str, Any] | None = None  # the running task

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> CertoServer:
        self._lock.acquire()
        try:
            log = open(self.workspace / ".firm-certo.log", "ab")  # noqa: SIM115 - closed below
            env = os.environ | {"FIRM_CERTO_WORKSPACE": str(self.workspace)}
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                env=env,
                limit=LINE_LIMIT,
            )
            log.close()  # the child keeps its own copy
            init = await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "the-firm", "version": "1"},
                },
            )
            info = init.get("serverInfo", {})
            self.version = str(info.get("version", ""))
            if info.get("name") != "certo" or _version(self.version) < MIN_VERSION:
                raise CertoError(
                    f"needs Certo {'.'.join(map(str, MIN_VERSION))} or later, "
                    f"found {info.get('name')} {self.version}"
                )
            await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            await self.close()
            raise
        return self

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        self._active = None
        self._lock.release()

    async def __aenter__(self) -> CertoServer:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ------------------------------------------------------------- tasks
    async def run_task(
        self,
        project: str,
        task: str,
        *,
        dossier_id: str,
        max_minutes: int | None = None,
        max_steps: int | None = None,
    ) -> str:
        """Start a task in `project` (a folder in the workspace). Returns its run ID."""
        self._gate(task, dossier_id)
        if self._active is not None:
            raise CertoBusy(f"one task at a time: run {self._active['run_id']} is still going")
        args: dict[str, Any] = {
            "task": task,
            "project": project,
            "model": self.model,
            "auto_model": False,
            "independent_tests": False,
        }
        if max_minutes is not None:
            args["max_minutes"] = max_minutes
        if max_steps is not None:
            args["max_steps"] = max_steps
        started = await self.call("run_task", args)
        return self._began(started, task, dossier_id)

    async def resume(
        self, project: str, *, dossier_id: str, instruction: str | None = None
    ) -> str | None:
        """Continue the project's last run (after a stop); None if there is nothing to continue."""
        self._gate(instruction or "resume", dossier_id)
        if self._active is not None:
            raise CertoBusy(f"one task at a time: run {self._active['run_id']} is still going")
        args: dict[str, Any] = {"project": project, "model": self.model, "auto_model": False}
        if instruction:
            args["instruction"] = instruction
        started = await self.call("run_resume", args)
        if not started.get("run_id"):
            return None
        return self._began(started, instruction or "resume", dossier_id)

    async def status(self, run_id: str) -> dict[str, Any]:
        return await self.call("run_status", {"run_id": run_id})

    async def events(self, run_id: str, since: int = 0) -> dict[str, Any]:
        return await self.call("run_events", {"run_id": run_id, "since": since})

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return await self.call("run_cancel", {"run_id": run_id})

    async def wait(
        self,
        run_id: str,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Follow a run until it ends; record it as a MODEL_CALL; return its status."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        cursor = 0
        while True:
            if on_event is not None:
                batch = await self.events(run_id, cursor)
                for e in batch.get("events", []):
                    on_event(e)
                cursor = int(batch.get("next", cursor))
            status = await self.status(run_id)
            if status.get("state") != "running":
                if on_event is not None:  # whatever arrived after the last look
                    for e in (await self.events(run_id, cursor)).get("events", []):
                        on_event(e)
                self._record(status)
                return status
            if deadline is not None and loop.time() > deadline:
                raise TimeoutError(f"Certo run {run_id} still running after {timeout}s")
            await asyncio.sleep(self.poll_seconds)

    # ----------------------------------------------------------- plumbing
    def _gate(self, task: str, dossier_id: str) -> None:
        provider = self.model.split("/", 1)[0]
        if provider in self.models.egress.local_providers:
            return
        why = (
            f"Certo runs private research work on local models only; {self.model} is not "
            f"on a local provider ({', '.join(self.models.egress.local_providers)})"
        )
        self.sink.emit(
            LedgerEventType.MODEL_CALL,
            f"Certo task refused: {why}"[:300],
            self._payload(task, dossier_id, local=False) | {"outcome": "refused", "refusal": why},
        )
        raise EgressRefused(why)

    def _began(self, started: dict[str, Any], task: str, dossier_id: str) -> str:
        run_id = str(started["run_id"])
        self._active = {"run_id": run_id, "task": task, "dossier_id": dossier_id}
        return run_id

    def _payload(self, task: str, dossier_id: str, *, local: bool) -> dict[str, Any]:
        provider, _, name = self.model.partition("/")
        return {
            "role": "research",
            "via": "certo",
            "provider": provider,
            "model": name or self.model,
            "local": local,
            "tag": "private",
            "opt_in_run": None,
            "dossier_id": dossier_id,
            "messages_sha256": hashlib.sha256(task.encode()).hexdigest(),
            "at": self.clock().isoformat(),
        }

    def _record(self, status: dict[str, Any]) -> None:
        active, self._active = self._active, None
        if active is None or active["run_id"] != status.get("run_id"):
            return
        result = status.get("result") or {}
        usage = result.get("usage") or {}
        ok = status.get("state") == "finished" and result
        self.sink.emit(
            LedgerEventType.MODEL_CALL,
            f"Certo task for {active['dossier_id']}: {status.get('state')}, "
            f"verdict {status.get('verdict')}"[:300],
            self._payload(active["task"], active["dossier_id"], local=True)
            | {
                "outcome": "ok" if ok else "error",
                "run_id": active["run_id"],
                "state": status.get("state"),
                "verdict": status.get("verdict"),
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "cost_gbp": 0.0,  # local: no money spent (Certo's own cost is in USD)
                "seconds": float(usage.get("duration_seconds") or 0.0),
                "error": result.get("error") if result else status.get("state"),
            },
            event_id=f"certo:{active['run_id']}:{self.version}:{active['dossier_id']}",
        )

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            raise CertoError(text or f"Certo's {tool} failed")
        data = result.get("structuredContent")
        if data is None:
            texts = [c.get("text", "") for c in result.get("content", [])]
            data = json.loads(texts[0]) if texts and texts[0] else {}
        if (
            isinstance(data, dict)
            and set(data) == {"result"}
            and not isinstance(data["result"], dict)
        ):
            return {"result": data["result"]}
        return data if isinstance(data, dict) else {"result": data}

    async def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise CertoError("the Certo server isn't running")
        try:
            proc.stdin.write((json.dumps(message) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise CertoError("the Certo server stopped") from exc

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ids += 1
        msg_id = self._ids
        await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.call_timeout)
            except TimeoutError:
                raise CertoError(
                    f"no answer from Certo to {method} in {self.call_timeout}s"
                ) from None
            if not line:
                raise CertoError("the Certo server stopped (see .firm-certo.log in the workspace)")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # not ours: MCP stdout carries only JSON-RPC
            if message.get("id") != msg_id:
                continue  # a notification, or a late answer
            if "error" in message:
                raise CertoError(f"Certo: {message['error'].get('message', message['error'])}")
            return message.get("result") or {}
