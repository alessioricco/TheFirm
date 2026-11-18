"""Fill the environment from a `.env` file (no dependency, no secret printed).

Only keys not already set are filled, so the shell (or a systemd
EnvironmentFile) always wins. `FIRM_DOTENV=0` turns it off (the tests do).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

DOTENV_SWITCH = "FIRM_DOTENV"


def load_env_file(path: Path, environ: MutableMapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # missing or unreadable: the environment stays as it is
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value and key not in env:
            env[key] = value


def load_dotenv_unless_disabled(path: Path = Path(".env")) -> None:
    if os.environ.get(DOTENV_SWITCH, "1") != "0":
        load_env_file(path)
