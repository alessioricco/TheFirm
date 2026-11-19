"""`SandboxedStrategy`: a strategy whose code runs in a sandbox worker.

It looks like any strategy to the verifier and the paper run (`name`,
`default_params`, `signals(bars, params)`), but every call crosses a pipe
into a locked-down process (see `policy.py`). One worker serves many calls:
the verifier calls `signals` hundreds of times per verification.

Two kinds of failure, kept apart so a broken sandbox is never mistaken for
a broken strategy:

- `StrategyError`: the strategy's own code raised or broke the contract
  (wrong number of signals, a forbidden import). The worker stays usable.
- `SandboxError`: no OS sandbox, the worker crashed or hit a limit (CPU,
  memory, wall clock). The worker is gone.
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import pandas as pd

from firm.sandbox import protocol
from firm.sandbox.policy import Backend, Policy, detect, wrap

MEMORY_POLL_SECONDS = 0.25  # macOS has no memory rlimit; Linux also has RLIMIT_AS
STDERR_TAIL = 2000


class SandboxError(RuntimeError):
    """The sandbox itself failed: unavailable, crashed, or a limit was hit."""


class StrategyError(ValueError):
    """The strategy's code raised, or broke the strategy contract."""


def clean_env(work: str) -> dict[str, str]:
    """Nothing from the firm's environment: no keys, no hosts, no paths."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": work,
        "TMPDIR": work,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


class SandboxedStrategy:
    def __init__(
        self,
        path: Path,
        class_name: str,
        policy: Policy | None = None,
        *,
        backend: Backend | None = None,
    ) -> None:
        self.path, self.class_name = Path(path), class_name
        self.policy = policy or Policy()
        self._backend = backend
        self._proc: subprocess.Popen[bytes] | None = None
        self._work: str | None = None
        self._stderr: IO[bytes] | None = None
        self._killed: str | None = None
        self._buf = bytearray()
        self.name = ""
        self.default_params: dict[str, Any] = {}

    # ------------------------------------------------------------- lifecycle
    def start(self) -> SandboxedStrategy:
        backend = self._backend or detect()
        if backend is None:
            raise SandboxError(
                "no OS sandbox available (sandbox-exec on macOS, bubblewrap on Linux): "
                "refusing to run strategy code unsandboxed"
            )
        code = self.path.read_text()
        self._work = os.path.realpath(tempfile.mkdtemp(prefix="firm-sandbox-"))
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
        argv = wrap(
            backend,
            Path(self._work),
            self.policy,
            [sys.executable, "-I", "-m", "firm.sandbox.worker"],
        )
        limits = self.policy.limits
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            cwd=self._work,
            env=clean_env(self._work),
            preexec_fn=lambda: _set_limits(limits.cpu_seconds, limits.file_mb, limits.memory_mb),
            start_new_session=True,
        )
        threading.Thread(target=self._watch_memory, daemon=True).start()
        cfg = {"code": code, "class_name": self.class_name, "imports": sorted(self.policy.imports)}
        self._send(protocol.header(cfg))
        hello = self._recv_header(limits.start_seconds)
        if not hello["ok"]:
            self.close()
            raise StrategyError(hello["error"])
        self.name, self.default_params = hello["name"], hello["default_params"]
        return self

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(OSError):
                if proc.stdin:
                    proc.stdin.close()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                proc.wait()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        if self._work is not None:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = None

    def __enter__(self) -> SandboxedStrategy:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --------------------------------------------------------------- calls
    def signals(self, bars: pd.DataFrame, params: Mapping[str, Any]) -> pd.Series:
        self._send(
            protocol.header({"op": "signals", "params": dict(params)})
            + protocol.frame(protocol.bars_to_bytes(bars.reset_index(drop=True)))
        )
        reply = self._recv_header(self.policy.limits.call_seconds)
        if not reply["ok"]:
            if reply.get("limit"):  # the worker hit a limit it could report (memory)
                self._killed = reply["limit"]
                raise self._failure(reply["error"])
            raise StrategyError(reply["error"])
        values = protocol.signals_from_bytes(self._recv(self.policy.limits.call_seconds))
        if len(values) != len(bars):
            raise SandboxError(f"worker returned {len(values)} signals for {len(bars)} bars")
        return pd.Series(values, index=bars.index, dtype=float)

    # ------------------------------------------------------------ plumbing
    def _send(self, data: bytes) -> None:
        proc = self._alive()
        try:
            assert proc.stdin is not None
            proc.stdin.write(data)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise self._failure("the worker stopped") from None

    def _recv_header(self, seconds: float) -> dict[str, Any]:
        return json.loads(self._recv(seconds))

    def _recv(self, seconds: float) -> bytes:
        deadline = time.monotonic() + seconds
        n = protocol.LENGTH.unpack(self._read_exact(protocol.LENGTH.size, deadline))[0]
        if n > protocol.MAX_FRAME:
            raise self._failure(f"oversized frame ({n} bytes)")
        return self._read_exact(n, deadline)

    def _read_exact(self, n: int, deadline: float) -> bytes:
        proc = self._alive()
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_READ)
            while len(self._buf) < n:
                left = deadline - time.monotonic()
                if left <= 0 or not sel.select(timeout=left):
                    self._killed = self._killed or "wall-clock limit"
                    _kill_group(proc)
                    raise self._failure("no answer in time")
                chunk = os.read(fd, max(65536, n - len(self._buf)))
                if not chunk:
                    raise self._failure("the worker stopped")
                self._buf += chunk
        out, self._buf = bytes(self._buf[:n]), self._buf[n:]
        return out

    def _alive(self) -> subprocess.Popen[bytes]:
        if self._proc is None:
            raise SandboxError("the sandbox worker is not running")
        return self._proc

    def _failure(self, what: str) -> SandboxError:
        proc = self._proc
        rc = None
        if proc is not None:
            try:
                rc = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                rc = proc.wait()
        # killed by a signal: -N directly, 128+N when bubblewrap is the parent
        sig = -rc if rc is not None and rc < 0 else rc - 128 if rc and rc > 128 else None
        reason = self._killed or ("CPU limit" if sig == signal.SIGXCPU else None)
        tail = self._stderr_tail()
        self.close()
        detail = f" ({reason})" if reason else ""
        return SandboxError(f"sandbox worker failed: {what}{detail}, exit {rc}\n{tail}".rstrip())

    def _stderr_tail(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.seek(0, os.SEEK_END)
        size = self._stderr.tell()
        self._stderr.seek(max(0, size - STDERR_TAIL))
        return self._stderr.read().decode(errors="replace")

    def _watch_memory(self) -> None:
        proc, limit_kb = self._proc, self.policy.limits.memory_mb * 1024
        while proc is not None and proc.poll() is None:
            if _group_rss_kb(proc.pid) > limit_kb:
                self._killed = "memory limit"
                _kill_group(proc)
                return
            time.sleep(MEMORY_POLL_SECONDS)


def _set_limits(cpu_seconds: int, file_mb: int, memory_mb: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_mb << 20, file_mb << 20))
    if sys.platform.startswith("linux"):  # macOS refuses address-space limits
        # address space, so twice the resident limit: libraries map more than they use
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb << 21, memory_mb << 21))


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def _group_rss_kb(pgid: int) -> int:
    """Resident memory of every process in the worker's group, in KiB."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    total = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid) and parts[1].isdigit():
            total += int(parts[1])
    return total
