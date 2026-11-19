"""What a strategy is, to the verifier, and how one is loaded.

A strategy is a class in a Python file with:

- `name: str`
- `default_params: dict[str, int | float | str | bool]`
- `signals(bars: pd.DataFrame, params) -> pd.Series`: target exposure per
  bar, in [0, 1] (spot, no leverage, no shorting in v1), NaN during warm-up.
  The value at row i is decided at the close of bar i and may use rows <= i
  only. The verifier does not trust this: the truncation check tests it.

Hand-written strategies (`examples/strategies/`) are imported in-process.
Agent-written ones live in the private store (`<data root>/research/`) and
run only in the sandbox (`firm.sandbox`, M3 plan R2): loading one in-process
is refused, and any other file can be sandboxed on request.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import pandas as pd

from firm.sandbox.client import SandboxedStrategy
from firm.sandbox.policy import Policy

ParamValue = int | float | str | bool


class SandboxRequired(PermissionError):
    """Agent-written code (the private store) asked to run in-process."""


@runtime_checkable
class Strategy(Protocol):
    name: str
    default_params: Mapping[str, ParamValue]

    def signals(self, bars: pd.DataFrame, params: Mapping[str, ParamValue]) -> pd.Series: ...


@dataclass(frozen=True)
class LoadedStrategy:
    strategy: Strategy
    path: Path
    class_name: str
    code_sha256: str
    sandboxed: bool = False
    close: Callable[[], None] = lambda: None  # stops the sandbox worker, if any


def private_root(data_root: Path) -> Path:
    """Where agent-written strategies live (see `firm.research.store`)."""
    return Path(data_root) / "research"


def _inside(path: Path, root: Path) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)


def sandbox_policy(
    path: Path, *, data_root: Path, hidden: Sequence[Path], force: bool = False
) -> Policy | None:
    """The sandbox a strategy file must run in: always for the private store,
    otherwise only when asked (`force`). `hidden`: the repo, state, config."""
    if not (force or _inside(path, private_root(data_root))):
        return None
    return Policy(hidden=tuple(Path(os.path.realpath(p)) for p in (data_root, *hidden)))


def load_strategy(
    path: Path,
    class_name: str,
    *,
    sandbox: Policy | None = None,
    private_root: Path | None = None,
) -> LoadedStrategy:
    """Load a strategy; in the sandbox when `sandbox` is given. A file under
    `private_root` without a sandbox is refused. Call `close()` when done."""
    code = path.read_bytes()
    sha = hashlib.sha256(code).hexdigest()
    if sandbox is not None:
        boxed = SandboxedStrategy(path, class_name, sandbox, code=code).start()
        return LoadedStrategy(cast(Strategy, boxed), path, class_name, sha, True, boxed.close)
    if private_root is not None and _inside(path, private_root):
        raise SandboxRequired(f"{path} is agent-written (private store): it runs only sandboxed")
    spec = importlib.util.spec_from_file_location(f"firm_strategy_{sha[:16]}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls: Any = getattr(module, class_name)
    instance = cls()
    if not isinstance(instance, Strategy):
        raise TypeError(f"{path}:{class_name} does not implement the Strategy protocol")
    return LoadedStrategy(cast(Strategy, instance), path, class_name, sha)
