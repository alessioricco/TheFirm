"""Per-service logins for the bus, generated from the permission table.

Golden rule 12 says each service may publish only its own subjects. The code
already refuses it (`BusClient`, `Envelope`); this module makes the server
refuse it too. `server_config` turns `PUBLISHERS` into a nats-server config:

- one user per `Service`, allowed to publish exactly the subjects it owns,
  plus the JetStream calls a client needs on the firm's stream (look up the
  stream, create or look up its own consumers, acknowledge messages). No
  service may create, change, purge or delete a stream.
- one admin user (`firm-admin`) that may manage the stream and publishes no
  firm subject; `firm bus up` and `firm bus status` use it.
- every user may receive only under its own inbox prefix
  (`_INBOX.<user>.>`: replies and its JetStream deliveries), so nothing
  reads firm subjects around JetStream, nor another desk's deliveries.

Passwords are random, generated once into `<state>/bus/credentials.json`
(mode 600) and kept across runs; they are never printed. Later, each machine
gets only the logins of the services it runs.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from firm.core.config import BusConfig
from firm.core.messages import Service, subjects_published_by

ADMIN = "firm-admin"
USERS: tuple[str, ...] = (*sorted(s.value for s in Service), ADMIN)


def bus_dir(state: Path) -> Path:
    return state / "bus"


def credentials_path(state: Path) -> Path:
    return bus_dir(state) / "credentials.json"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_or_create_credentials(state: Path) -> dict[str, str]:
    """Every user's password; missing users get a new one, existing ones are kept."""
    path = credentials_path(state)
    creds: dict[str, str] = {}
    if path.exists():
        creds = json.loads(path.read_text(encoding="utf-8"))
    missing = [u for u in USERS if not creds.get(u)]
    for user in missing:
        creds[user] = secrets.token_urlsafe(24)
    if missing or not path.exists():
        _write_private(path, json.dumps(creds, indent=2, sort_keys=True) + "\n")
    return creds


def login(state: Path, user: Service | str) -> tuple[str, str]:
    """(user, password) for one service or the admin, from the credentials file."""
    name = user.value if isinstance(user, Service) else user
    path = credentials_path(state)
    if not path.exists():
        raise FileNotFoundError(f"no bus credentials at {path}: run `firm bus up` once")
    creds = json.loads(path.read_text(encoding="utf-8"))
    if not creds.get(name):
        raise KeyError(f"no bus login for {name} in {path}")
    return name, creds[name]


def client_api(stream: str) -> list[str]:
    """JetStream calls every service needs, all scoped to the firm's stream."""
    return [
        "$JS.API.INFO",
        f"$JS.API.STREAM.INFO.{stream}",
        f"$JS.API.CONSUMER.INFO.{stream}.*",
        f"$JS.API.CONSUMER.CREATE.{stream}.>",
        f"$JS.API.CONSUMER.DURABLE.CREATE.{stream}.*",
        f"$JS.ACK.{stream}.>",
    ]


def inbox_prefix(user: str) -> str:
    """Each login receives replies and JetStream deliveries under its own prefix."""
    return f"_INBOX.{user}"


def permissions(user: str, stream: str) -> dict[str, Any]:
    if user == ADMIN:
        publish = ["$JS.API.>"]
    else:
        own = sorted(s.value for s in subjects_published_by(Service(user)))
        publish = own + client_api(stream)
    return {
        "publish": {"allow": publish},
        "subscribe": {"allow": [f"{inbox_prefix(user)}.>"]},
    }


def server_config(cfg: BusConfig, creds: dict[str, str], store_dir: Path) -> dict[str, Any]:
    """The nats-server configuration (nats-server reads JSON)."""
    return {
        "host": cfg.host,
        "port": cfg.port,
        "server_name": "firm",
        "jetstream": {"store_dir": str(store_dir)},
        "authorization": {
            "users": [
                {"user": u, "password": creds[u], "permissions": permissions(u, cfg.stream)}
                for u in USERS
            ]
        },
    }


def write_server_config(cfg: BusConfig, state: Path) -> Path:
    """Write `<state>/bus/nats-server.conf` (mode 600: it holds the passwords)."""
    creds = load_or_create_credentials(state)
    path = bus_dir(state) / "nats-server.conf"
    conf = server_config(cfg, creds, bus_dir(state) / "jetstream")
    _write_private(path, json.dumps(conf, indent=2) + "\n")
    return path
