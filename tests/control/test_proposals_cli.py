"""`firm research proposals ...`: the owner reviews the senior quant's ideas.

Import checks them strictly; approve opens a dossier with the proposal as its
brief; every change is an OWNER_COMMAND; outcomes leave only with --outcomes,
and that egress is logged (owner, 2026-11-20).
"""

from __future__ import annotations

import pytest
import yaml

from firm.control.cli import main
from firm.core.domain import LedgerEventType
from firm.ledger.store import Ledger
from firm.research.dossier import DossierStore
from research.test_proposals import GOOD
from verify_helpers import REPO


@pytest.fixture
def where(tmp_path):
    root, state = tmp_path / "data", tmp_path / "state"
    opts = ["--root", str(root), "--state-root", str(state), "--config", str(REPO / "config")]
    return root, state, opts


def run(capsys, *argv) -> tuple[int, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def owner_commands(state) -> list[str]:
    from firm.ledger.sink import ledger_path

    ledger = Ledger(ledger_path(state))
    return [
        e.payload["command"] for e in ledger.read() if e.event_type == LedgerEventType.OWNER_COMMAND
    ]


def write(folder, name, data) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(yaml.safe_dump(data))


def test_import_list_show_approve(where, tmp_path, capsys) -> None:
    root, state, opts = where
    inbox = tmp_path / "inbox"
    write(inbox, "a.yaml", GOOD)
    write(inbox, "b.yaml", GOOD | {"method_family": "market_making"})
    code, out = run(capsys, "research", "proposals", "import", str(inbox), *opts)
    assert code == 2  # one refused
    assert "b.yaml: refused" in out and "out of scope" in out and "new  a.yaml" in out
    pid = out.split("  new")[0].split()[-1]

    code, out = run(capsys, "research", "proposals", "import", str(inbox / "a.yaml"), *opts)
    assert code == 0 and "already imported" in out

    code, out = run(capsys, "research", "proposals", "list", *opts)
    assert code == 0 and pid in out and "proposed" in out
    code, out = run(capsys, "research", "proposals", "show", pid, *opts)
    assert code == 0 and "Falsified if:" in out

    code, out = run(capsys, "research", "proposals", "approve", pid, "--id", "d-q1", *opts)
    assert code == 0 and "d-q1  draft" in out
    d = DossierStore(root).get("d-q1")
    assert d.market == "crypto" and d.timeframes == ["4h"] and "Time-series momentum" in d.brief
    code, out = run(capsys, "research", "proposals", "approve", pid, *opts)
    assert code == 3 and "already approved" in out
    assert owner_commands(state) == [
        "research proposals import",
        "research proposals approve",
    ]


def test_reject_and_context(where, tmp_path, capsys) -> None:
    _root, state, opts = where
    write(tmp_path / "in", "a.yaml", GOOD)
    _, out = run(capsys, "research", "proposals", "import", str(tmp_path / "in"), *opts)
    pid = out.split("  new")[0].split()[-1]
    code, _ = run(capsys, "research", "proposals", "reject", pid, "--reason", "tested", *opts)
    assert code == 0

    code, out = run(capsys, "research", "proposals", "context", *opts)
    assert code == 0
    assert "Spot only" in out and "crypto: calendar 24x7" in out and "taker" in out
    assert f"{pid} [rejected] momentum" in out and "(rejected: tested)" in out
    assert "Outcomes" not in out
    assert "research proposals context" not in owner_commands(state)  # nothing private left

    code, out = run(capsys, "research", "proposals", "context", "--outcomes", *opts)
    assert code == 0 and "Outcomes of approved proposals" in out
    assert owner_commands(state)[-1] == "research proposals context"
