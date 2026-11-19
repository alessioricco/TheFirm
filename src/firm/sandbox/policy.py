"""What a sandbox worker may do, and the OS sandbox that enforces it.

macOS: `sandbox-exec` with a Seatbelt profile. Linux: `bubblewrap` with
every namespace unshared. Both give the same rules:

- no network (not even local sockets);
- no reading file contents under the hidden roots (the owner's home, the
  repo, the data store, the state folder), except the Python install, the
  virtual environment and the firm's source, which the worker needs to run;
- no writing anywhere except the worker's own temporary folder;
- macOS: no starting other programs (only the Python install may be
  executed, since Homebrew's Python re-executes itself) and no forking.
  Linux: programs may start, but inside the same namespaces, so they get
  the same rules (no network, hidden folders empty, read-only filesystem).

File names and sizes stay visible on macOS (Python needs to stat parent
folders); contents don't.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import firm

Backend = Literal["seatbelt", "bubblewrap"]

# Imports agent strategy code may use. Hygiene, not the boundary: the OS
# sandbox is. Widened to standard ML tools (scikit-learn, Prophet) only as
# the owner approves them (M3 plan, R2).
DEFAULT_IMPORTS = frozenset(
    {
        "__future__",
        "abc",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "itertools",
        "math",
        "numpy",
        "operator",
        "pandas",
        "statistics",
        "typing",
    }
)


@dataclass(frozen=True)
class Limits:
    cpu_seconds: int = 600  # CPU time over the worker's whole life
    memory_mb: int = 4096  # resident memory, checked by a watchdog
    call_seconds: float = 120.0  # wall clock per call
    start_seconds: float = 60.0  # wall clock to start and load the code
    file_mb: int = 64  # largest file the worker may write


@dataclass(frozen=True)
class Policy:
    """`hidden`: extra roots whose contents the worker may not read (the home
    folder is always hidden). Pass the repo, the data store and the state."""

    hidden: tuple[Path, ...] = ()
    limits: Limits = field(default_factory=Limits)
    imports: frozenset[str] = DEFAULT_IMPORTS


def _real(p: Path | str) -> str:
    return os.path.realpath(p)


def hidden_roots(policy: Policy) -> list[str]:
    return sorted({_real(Path.home()), *(_real(p) for p in policy.hidden)})


def readable_roots() -> list[str]:
    """What the worker needs to read to run: Python, its packages, the firm's code."""
    roots = {
        *_python_paths(),
        sysconfig.get_paths()["purelib"],
        sysconfig.get_paths()["platlib"],
        str(Path(firm.__file__).resolve().parent.parent),  # src/ (editable) or site-packages
    }
    return sorted({_real(r) for r in roots} | set(_python_paths()))


def _python_paths() -> set[str]:
    """The Python install as the venv names it and as it really is. uv reaches its
    Pythons through symlinked folders (cpython-3.14-... -> cpython-3.14.0-...), and
    the sandbox must show both, or the venv's interpreter link leads nowhere."""
    paths = {os.path.abspath(p) for p in (sys.prefix, sys.base_prefix)}
    for hop in _link_chain(sys.executable):  # .venv/bin/python -> .../bin/python3.14 -> ...
        paths.add(os.path.dirname(os.path.dirname(hop)))  # the install holding that bin/
    return paths | {_real(p) for p in paths}


def _link_chain(path: str) -> list[str]:
    """Every path a symlink chain passes through, unresolved, ending at the file."""
    hops, p = [], os.path.abspath(path)
    for _ in range(40):
        hops.append(p)
        if not os.path.islink(p):
            break
        p = os.path.normpath(os.path.join(os.path.dirname(p), os.readlink(p)))
    return hops


def exec_roots() -> list[str]:
    return sorted(_python_paths())


def _sb(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(work: str, hidden: Sequence[str], readable: Sequence[str]) -> str:
    def paths(ps: Sequence[str]) -> str:
        return " ".join(f"(subpath {_sb(p)})" for p in ps)

    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            "(deny process-fork)",
            "(deny process-exec)",
            f"(allow process-exec {paths(exec_roots())})",
            "(deny file-write*)",
            f'(allow file-write* (subpath {_sb(work)}) (literal "/dev/null"))',
            f"(deny file-read-data {paths(hidden)})",
            f"(allow file-read-data {paths([*readable, work])})",
            "",
        ]
    )


def bubblewrap_args(work: str, hidden: Sequence[str], readable: Sequence[str]) -> list[str]:
    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all"]
    args += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for h in hidden:
        args += ["--tmpfs", h]
    for r in readable:
        args += ["--ro-bind", r, r]
    args += ["--bind", work, work, "--chdir", work, "--"]
    return args


def wrap(backend: Backend, work: Path, policy: Policy, argv: Sequence[str]) -> list[str]:
    w, hidden, readable = _real(work), hidden_roots(policy), readable_roots()
    if backend == "seatbelt":
        profile = Path(w) / "sandbox.sb"
        profile.write_text(seatbelt_profile(w, hidden, readable))
        return ["sandbox-exec", "-f", str(profile), *argv]
    return [*bubblewrap_args(w, hidden, readable), *argv]


@functools.cache
def detect() -> Backend | None:
    """The OS sandbox that works here, found by trying it; None if none does."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        probe = ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"]
        if _works(probe):
            return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        if _works(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"]):
            return "bubblewrap"
    return None


def _works(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
