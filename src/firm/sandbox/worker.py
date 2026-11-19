"""The sandbox worker: loads one strategy file and answers `signals` calls.

Run by `firm.sandbox.client` as `python -I -m firm.sandbox.worker`, inside
the OS sandbox, with a clean environment, in its own temporary folder.
Protocol (see `protocol.py`): the first frame configures the worker; it
answers with the strategy's name and default parameters, then serves
`signals` requests until `close` or end of input. Anything the strategy
prints goes to stderr, never into the protocol stream.
"""

from __future__ import annotations

import builtins
import json
import math
import os
import sys
import traceback
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from firm.sandbox import protocol

# Removed from the strategy's builtins: files and dynamic code go through
# nothing but the allow-listed libraries. Hygiene: the OS sandbox is the boundary.
REMOVED_BUILTINS = ("open", "exec", "eval", "compile", "input", "breakpoint", "help")


def guarded_import(allowed: frozenset[str]) -> Any:
    real = builtins.__import__

    def _import(name: str, globals=None, locals=None, fromlist=(), level=0):
        if level != 0:
            raise ImportError("relative imports are not allowed in a sandboxed strategy")
        if not any(name == a or name.startswith(a + ".") for a in allowed):
            raise ImportError(f"import of {name!r} is not allowed in a sandboxed strategy")
        return real(name, globals, locals, fromlist, level)

    return _import


def load(code: str, class_name: str, allowed: frozenset[str]) -> Any:
    safe = {k: v for k, v in vars(builtins).items() if k not in REMOVED_BUILTINS}
    safe["__import__"] = guarded_import(allowed)
    module: dict[str, Any] = {"__name__": "sandboxed_strategy", "__builtins__": safe}
    exec(compile(code, "strategy.py", "exec"), module)
    cls = module.get(class_name)
    if not isinstance(cls, type):
        raise TypeError(f"no class {class_name!r} in the strategy file")
    instance = cls()
    name = getattr(instance, "name", None)
    defaults = getattr(instance, "default_params", None)
    if not isinstance(name, str) or not isinstance(defaults, Mapping):
        raise TypeError("a strategy needs `name: str` and `default_params: dict`")
    if not callable(getattr(instance, "signals", None)):
        raise TypeError("a strategy needs a `signals(bars, params)` method")
    for k, v in defaults.items():
        if not isinstance(k, str) or not isinstance(v, int | float | str | bool):
            raise TypeError(f"default parameter {k!r} must be int, float, str or bool")
        if isinstance(v, float) and not math.isfinite(v):
            raise TypeError(f"default parameter {k!r} must be finite")
    return instance


def _error(exc: BaseException) -> dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def serve(inp: Any, out: Any) -> None:
    cfg = json.loads(protocol.read_frame(inp))
    try:
        strategy = load(cfg["code"], cfg["class_name"], frozenset(cfg["imports"]))
    except Exception as exc:
        traceback.print_exc()
        out.write(protocol.header(_error(exc)))
        return
    hello = {"ok": True, "name": strategy.name, "default_params": dict(strategy.default_params)}
    out.write(protocol.header(hello))
    while True:
        try:
            req = json.loads(protocol.read_frame(inp))
        except EOFError:
            return
        if req.get("op") != "signals":
            return
        bars = protocol.bars_from_bytes(protocol.read_frame(inp))
        try:
            sig = strategy.signals(bars, dict(req["params"]))
            values = pd.Series(sig).to_numpy(dtype=float, na_value=np.nan)
            if values.ndim != 1 or len(values) != len(bars):
                raise ValueError(f"strategy returned {len(values)} signals for {len(bars)} bars")
        except MemoryError as exc:  # Linux: RLIMIT_AS reached; a limit, not a strategy fault
            out.write(protocol.header(_error(exc) | {"limit": "memory limit"}))
            return
        except Exception as exc:
            traceback.print_exc()
            out.write(protocol.header(_error(exc)))
            continue
        out.write(protocol.header({"ok": True}))
        out.write(protocol.frame(protocol.signals_to_bytes(values)))


def main() -> None:
    out = os.fdopen(os.dup(1), "wb", buffering=0)  # the protocol stream
    os.dup2(2, 1)  # the strategy's prints go to stderr
    sys.stdout = sys.stderr
    serve(sys.stdin.buffer, out)


if __name__ == "__main__":
    main()
