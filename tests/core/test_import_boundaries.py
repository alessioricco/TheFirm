"""Desks never import each other's internals; they share only `firm.core` (golden rule 12)."""

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


@pytest.mark.parametrize("desk", DESKS)
def test_desk_imports_only_core_and_itself(desk: str) -> None:
    allowed = {"core", desk} if desk != "core" else {"core"}
    for path in (SRC / desk).rglob("*.py"):
        bad = _firm_imports(path) - allowed
        assert not bad, f"{path.relative_to(SRC)} imports firm.{sorted(bad)}"
