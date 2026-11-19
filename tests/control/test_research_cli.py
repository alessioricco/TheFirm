"""`firm research ...`: the owner's commands for the research floor (M3, M2).

Each change is an OWNER_COMMAND in the ledger; stage changes are DOSSIER
events too. No command here starts a model or a backtest (that's M4).
"""

from __future__ import annotations

import pytest

from firm.control.cli import main
from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger
from firm.research.dossier import DossierStore, Stage


@pytest.fixture
def where(tmp_path):
    root, state = tmp_path / "data", tmp_path / "state"
    return root, state, ["--root", str(root), "--state-root", str(state)]


def run(capsys, *argv) -> tuple[int, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def start(capsys, opts, dossier_id, brief="momentum on BTC") -> None:
    code, out = run(
        capsys,
        "research", "start", "--brief", brief, "--market", "crypto",
        "--instrument", "BTC-USD", "--tf", "1h,1d", "--id", dossier_id, *opts,
    )  # fmt: skip
    assert code == 0, out


def test_start_list_and_show(where, capsys) -> None:
    root, _state, opts = where
    for i in (1, 2, 3):
        start(capsys, opts, f"d-00{i}")
    code, out = run(capsys, "research", "list", *opts)
    assert code == 0
    assert "researchers at once: 2" in out
    assert "d-001  draft      running" in out and "d-003  draft      waiting (1 in" in out
    from firm.core.config import load_research_config
    from verify_helpers import REPO

    trials = load_research_config(REPO / "config").budgets.trials  # the owner's budget
    assert f"trials 0/{trials}" in out and "cloud £0.00/£0.00" in out
    code, out = run(capsys, "research", "show", "d-001", *opts)
    assert code == 0 and "timeframes 1h, 1d" in out and "(not written yet)" in out
    d = DossierStore(root).get("d-001")
    assert d.timeframes == ["1h", "1d"] and d.budget.cloud_spend_gbp == 0


def test_pause_resume_redirect_close_are_owner_commands_in_the_ledger(where, capsys) -> None:
    root, state, opts = where
    start(capsys, opts, "d-001")
    start(capsys, opts, "d-002")
    assert run(capsys, "research", "pause", "d-001", "--reason", "later", *opts)[0] == 0
    assert DossierStore(root).get("d-001").paused
    assert run(capsys, "research", "resume", "d-001", *opts)[0] == 0
    code, out = run(capsys, "research", "redirect", "d-002", "--brief", "carry instead", *opts)
    assert code == 0 and "new brief" in out  # still a draft: same dossier
    assert run(capsys, "research", "close", "d-001", "--reason", "dead end", *opts)[0] == 0
    d = DossierStore(root).get("d-001")
    assert d.stage is Stage.CLOSED and d.closed_reason == "owner: dead end"

    events = list(Ledger(state / "ledger.sqlite").read())
    commands = [
        e.payload["command"] for e in events if e.event_type is LedgerEventType.OWNER_COMMAND
    ]
    assert commands == [
        "research start", "research start", "research pause", "research resume",
        "research redirect", "research close",
    ]  # fmt: skip
    stages = [(e.payload["dossier_id"], e.payload["to"]) for e in events
              if e.event_type is LedgerEventType.DOSSIER]  # fmt: skip
    assert ("d-001", "closed") in stages


def test_the_floor_brief(where, capsys, tmp_path) -> None:
    _, _, opts = where
    assert "no floor brief" in run(capsys, "research", "brief", *opts)[1]
    run(capsys, "research", "brief", "--text", "stay uncorrelated with the daily MA", *opts)
    assert "stay uncorrelated" in run(capsys, "research", "brief", *opts)[1]
    run(capsys, "research", "brief", "--clear", *opts)
    assert "no floor brief" in run(capsys, "research", "brief", *opts)[1]


def test_mistakes_are_errors_not_crashes(where, capsys) -> None:
    _, _, opts = where
    code, out = run(capsys, "research", "show", "d-404", *opts)
    assert code == 3 and "no dossier d-404" in out
    code, out = run(
        capsys, "research", "start", "--market", "crypto", "--instrument", "BTC-USD",
        "--tf", "1d", *opts,
    )  # fmt: skip
    assert code == 3 and "brief is required" in out
    start(capsys, opts, "d-001")
    run(capsys, "research", "close", "d-001", "--reason", "x", *opts)
    code, out = run(capsys, "research", "pause", "d-001", *opts)
    assert code == 3 and "closed" in out
