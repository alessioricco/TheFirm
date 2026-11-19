"""Desks never import each other's internals (golden rule 12).

- `core` imports nothing else from the firm.
- `data` (the point-in-time data plane, a read-only shared library in v1)
  imports only `core`.
- `bus` (the message bus client, how desks talk to each other) imports
  only `core`.
- `sandbox` (runs untrusted strategy code in a locked-down worker; used by
  the verifier, the paper run and research) imports only `core`.
- `models` (the model gateway: every model call, firm rule 8) imports only
  `core`.
- Every desk may import `core`, `data`, `bus`, `sandbox` and `models`.
- `control` is the composition root (CLI, later the API): it wires desks
  together and may import any of them. Nothing imports `control`.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "firm"
DESKS = sorted(p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists())


def _firm_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "firm" and len(parts) > 1:
                found.add(parts[1])
    return found


def _allowed(desk: str) -> set[str]:
    if desk == "core":
        return {"core"}
    if desk in ("data", "bus", "sandbox", "models"):
        return {"core", desk}
    if desk == "control":
        return set(DESKS)
    return {"core", "data", "bus", "sandbox", "models", desk}


@pytest.mark.parametrize("desk", DESKS)
def test_desk_imports_only_core_data_and_itself(desk: str) -> None:
    allowed = _allowed(desk)
    for path in (SRC / desk).rglob("*.py"):
        bad = _firm_imports(path) - allowed
        assert not bad, f"{path.relative_to(SRC)} imports firm.{sorted(bad)}"


def test_nothing_imports_control() -> None:
    for desk in DESKS:
        if desk == "control":
            continue
        for path in (SRC / desk).rglob("*.py"):
            assert "control" not in _firm_imports(path), path
