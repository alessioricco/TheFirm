"""Run a local nats-server for the firm (`firm bus up`).

The server's config is generated from `config/bus.yaml` and the permission
table (`firm.bus.auth`); its JetStream store and its log live in the state
folder. Starting checks the config with `nats-server -t` first and refuses
to start a second server on a port already in use.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

from firm.bus.auth import write_server_config
from firm.core.config import BusConfig


class BusServerError(RuntimeError):
    pass


def nats_server() -> str:
    exe = shutil.which("nats-server")
    if exe is None:
        raise BusServerError(
            "nats-server is not installed: `brew install nats-server` on macOS, "
            "a release binary on Linux (README, Quick start)"
        )
    return exe


def listening(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except OSError:
        return False
    return True


def start(cfg: BusConfig, state: Path, log_file: Path) -> subprocess.Popen[bytes]:
    """Start nats-server in the background and wait until it accepts connections."""
    exe = nats_server()
    if listening(cfg.host, cfg.port):
        raise BusServerError(
            f"something already listens on {cfg.host}:{cfg.port}: "
            "is the bus already up? (`firm bus status`)"
        )
    conf = write_server_config(cfg, state)
    check = subprocess.run([exe, "-c", str(conf), "-t"], capture_output=True, text=True)
    if check.returncode != 0:
        raise BusServerError(f"nats-server refused its config: {check.stderr.strip()[:300]}")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([exe, "-c", str(conf), "-l", str(log_file)])
    deadline = time.monotonic() + 10
    while not listening(cfg.host, cfg.port):
        if proc.poll() is not None:
            raise BusServerError(f"nats-server exited ({proc.returncode}); see {log_file}")
        if time.monotonic() > deadline:
            proc.terminate()
            raise BusServerError(f"nats-server did not start in 10s; see {log_file}")
        time.sleep(0.05)
    return proc
