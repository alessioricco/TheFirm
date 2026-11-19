"""docs/COMMANDS.md documents every `firm` command and every option (owner,
2026-11-19: all commands and their features, with examples). Adding a command
or a flag without documenting it fails here."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from firm.control.cli import build_parser

DOC = Path(__file__).resolve().parents[2] / "docs" / "COMMANDS.md"


def leaves(parser: argparse.ArgumentParser, prefix: str = "firm") -> dict[str, list[str]]:
    """Every runnable command ('firm paper run') and its long options."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        opts = [
            o
            for a in parser._actions
            for o in a.option_strings
            if o.startswith("--") and o != "--help"
        ]
        return {prefix: opts}
    out: dict[str, list[str]] = {}
    for name, sub in subs[0].choices.items():
        out |= leaves(sub, f"{prefix} {name}")
    return out


COMMANDS = leaves(build_parser())


def section(text: str, command: str) -> str:
    """The doc text under `## command` or `### command`, up to the next heading."""
    m = re.search(rf"^#{{2,3}} {re.escape(command)}\n(.*?)(?=^#{{1,3}} |\Z)", text, re.M | re.S)
    assert m, f"docs/COMMANDS.md has no heading for `{command}`"
    return m.group(1)


def test_the_parser_has_the_commands_we_expect() -> None:
    assert "firm paper run" in COMMANDS and "firm bus up" in COMMANDS
    assert "firm verify" in COMMANDS and len(COMMANDS) >= 18


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_and_option_is_documented(command: str) -> None:
    body = section(DOC.read_text(encoding="utf-8"), command)
    missing = [o for o in COMMANDS[command] if f"`{o}" not in body and f"{o}`" not in body]
    assert not missing, f"`{command}` options not documented in docs/COMMANDS.md: {missing}"
