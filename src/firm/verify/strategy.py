"""What a strategy is, to the verifier, and how one is loaded.

A strategy is a class in a Python file with:

- `name: str`
- `default_params: dict[str, int | float | str | bool]`
- `signals(bars: pd.DataFrame, params) -> pd.Series`: target exposure per
  bar, in [0, 1] (spot, no leverage, no shorting in v1), NaN during warm-up.
  The value at row i is decided at the close of bar i and may use rows <= i
  only. The verifier does not trust this: the truncation check tests it.

Strategies are hand-written in milestone 1. Agent-written code (milestone 3)
will need a sandbox before it is loaded; this loader executes the file.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import pandas as pd

ParamValue = int | float | str | bool


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


def load_strategy(path: Path, class_name: str) -> LoadedStrategy:
    code = path.read_bytes()
    sha = hashlib.sha256(code).hexdigest()
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
