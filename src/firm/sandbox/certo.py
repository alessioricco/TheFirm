"""The OS sandbox Certo's MCP server runs in (owner, 2026-11-19; docs/CERTO.md).

Certo is a guard rail, not a sandbox: its agent runs commands as the user.
So the firm starts `scripts/certo-mcp` inside the operating system's
sandbox, with these rules (found by running Certo in it, and kept as tight
as Certo allows):

- **Network**: out only to the model server's port (the DGX's Ollama),
  plus name lookup. No other internet: no PyPI, no web. (Seatbelt filters
  by port, not by host.)
- **Writes**: only the Certo workspace, a private temporary folder, the
  terminal devices Certo's agent opens, and `~/.openhands` (the state of
  the agent engine Certo uses).
- **Reads**: the home folder, the repository and the stores are hidden,
  except what Certo needs to run: the workspace, Certo's install and its
  settings (`~/.config/certo`), the launcher and its settings file, the
  firm's Python packages (each project's environment reuses them, so
  nothing is downloaded), and the names (not the contents) of the folders
  above the workspace, which Certo looks up.

macOS only for now (`sandbox-exec`). On Linux, bubblewrap can't open one
port and close the rest of the network; the research floor moves to the
DGX with its own design.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


class CertoSandboxError(RuntimeError):
    """Certo can't be sandboxed here: nothing is started."""


@dataclass(frozen=True)
class CertoSandbox:
    workspace: Path  # Certo's projects folder and root
    tmp: Path  # its private temporary folder
    model_port: int  # the model server's port (Ollama: 11434)
    hidden: tuple[Path, ...]  # the repo, the data store, the state: unreadable
    readable: tuple[Path, ...]  # Certo's install, the firm's packages, the launcher, ...
    writable: tuple[Path, ...] = field(default_factory=tuple)  # extra writable folders


def _real(p: Path | str) -> str:
    return os.path.realpath(p)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(box: CertoSandbox, home: Path | None = None) -> str:
    home_dir = _real(home or Path.home())
    ws, tmp = _real(box.workspace), _real(box.tmp)
    hidden = sorted({home_dir, *(_real(p) for p in box.hidden)})
    readable = sorted({ws, tmp, *(_real(p) for p in box.readable)})
    writable = sorted({ws, tmp, *(_real(p) for p in box.writable)})
    # the folders above the workspace, up to the home folder: names only
    above, d = [], ws
    while True:
        d = os.path.dirname(d)
        above.append(d)
        if d in (home_dir, "/") or not d.startswith(home_dir):
            break

    def subpaths(ps: Sequence[str]) -> str:
        return " ".join(f"(subpath {_q(p)})" for p in ps)

    mdns = '(remote unix-socket (path-literal "/private/var/run/mDNSResponder"))'
    devices = '(literal "/dev/null") (literal "/dev/tty") (subpath "/dev/fd")'
    ptys = '(literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$")'
    names_only = " ".join(f"(literal {_q(a)})" for a in above)
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            f'(allow network-outbound (remote ip "*:{int(box.model_port)}"))',
            f"(allow network-outbound {mdns})",
            f"(allow network* (remote unix-socket (subpath {_q(tmp)})))",
            f"(allow network* (local unix-socket (subpath {_q(tmp)})))",
            "(deny file-write*)",
            f"(allow file-write* {subpaths(writable)} {devices})",
            f"(allow file-read* file-write* file-ioctl {ptys})",
            f"(deny file-read-data {subpaths(hidden)})",
            f"(allow file-read-data {subpaths(readable)})",
            f"(allow file-read-data {names_only})",
            "",
        ]
    )


def certo_command(box: CertoSandbox, launcher: Sequence[str]) -> list[str]:
    """The argv that starts the launcher inside the sandbox (writes the profile)."""
    if sys.platform != "darwin":
        raise CertoSandboxError(
            "Certo's sandbox needs macOS (sandbox-exec) for now: refusing to start Certo "
            "unsandboxed (docs/CERTO.md)"
        )
    Path(box.tmp).mkdir(parents=True, exist_ok=True)
    profile = Path(box.tmp) / "certo.sb"
    profile.write_text(seatbelt_profile(box))
    return ["sandbox-exec", "-f", str(profile), "env", f"TMPDIR={_real(box.tmp)}/", *launcher]
