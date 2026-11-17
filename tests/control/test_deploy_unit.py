"""The systemd unit for the unattended paper run stays in step with the CLI."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from firm.control.cli import EXIT_REFUSED, build_parser

REPO = Path(__file__).resolve().parents[2]
UNIT = (REPO / "deploy/systemd/firm-paper@.service").read_text()
CONF = (REPO / "deploy/paper-run.conf.example").read_text()


def _setting(key: str) -> str:
    return re.search(rf"^{key}=(.*)$", UNIT, re.M).group(1)  # type: ignore[union-attr]


def _conf() -> dict[str, str]:
    lines = [ln for ln in CONF.splitlines() if ln and not ln.startswith("#")]
    return dict(ln.split("=", 1) for ln in lines)


def test_every_variable_the_unit_uses_is_in_the_example() -> None:
    used = set(re.findall(r"\$\{?(FIRM_[A-Z_]+)", _setting("ExecStart")))
    assert used <= set(_conf())
    assert {"FIRM_DATA_ROOT", "FIRM_STATE_ROOT"} <= set(_conf())


def test_the_command_parses_as_a_paper_run() -> None:
    conf = _conf()
    line = re.sub(r"\$\{?(FIRM_[A-Z_]+)\}?", lambda m: conf[m.group(1)], _setting("ExecStart"))
    argv = shlex.split(line)
    assert argv[:4] == ["@UV@", "run", "--frozen", "firm"]
    args = build_parser().parse_args(argv[4:])
    assert (args.command, args.paper_cmd) == ("paper", "run")
    assert args.hours is None  # runs until stopped: a restart must not reset a timer


def test_a_refusal_is_never_retried() -> None:
    assert _setting("Restart") == "on-failure"
    assert _setting("RestartPreventExitStatus") == str(EXIT_REFUSED)
    assert _setting("KillSignal") == "SIGTERM"  # the run's graceful stop
