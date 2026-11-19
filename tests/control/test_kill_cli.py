"""`firm kill` / `firm resume`: deliberate, recorded, confirmed by the risk desk."""

from __future__ import annotations

import pytest

import firm.control.killswitch as killswitch
from firm.control.cli import main
from firm.control.killswitch import kill_state
from firm.core.config import load_risk
from firm.core.domain import LedgerEventType
from firm.ledger.sink import LedgerSink
from firm.ledger.store import Ledger
from firm.risk.desk import RiskDesk
from verify_helpers import REPO


def run(capsys, *argv) -> tuple[int, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def test_nothing_happens_without_confirm(tmp_path, capsys) -> None:
    code, out = run(
        capsys, "kill", "--market", "crypto", "--reason", "x", "--state-root", str(tmp_path)
    )
    assert code == 3 and "--confirm" in out
    assert kill_state(Ledger(tmp_path / "ledger.sqlite"))["markets"] == {}


def test_with_no_bus_it_is_recorded_and_applies_at_the_next_start(tmp_path, capsys) -> None:
    code, out = run(capsys, "kill", "--market", "crypto", "--reason", "drill", "--confirm",
                    "--no-bus", "--state-root", str(tmp_path))  # fmt: skip
    assert code == 1 and "recorded in the ledger" in out and "next starts" in out
    state = kill_state(Ledger(tmp_path / "ledger.sqlite"))
    assert state["markets"]["crypto"]["reason"] == "drill"


def test_resume_needs_a_kill_to_release(tmp_path, capsys) -> None:
    code, out = run(
        capsys, "resume", "--all", "--reason", "x", "--confirm", "--state-root", str(tmp_path)
    )
    assert code == 3 and "nothing to resume" in out


def test_the_risk_desk_confirms_a_kill_and_its_release(tmp_path, capsys, monkeypatch) -> None:
    """The bus is replaced by a risk desk writing to the same ledger, as the
    running paper run's desk does."""
    desk = RiskDesk(
        load_risk(REPO / "config" / "risk" / "default.yaml"),
        LedgerSink(Ledger(tmp_path / "ledger.sqlite")),
    )

    def publisher(_config, _state):
        async def publish(cmd):
            desk.command(cmd)

        return publish

    monkeypatch.setattr(killswitch, "bus_publisher", publisher)
    code, out = run(capsys, "kill", "--market", "crypto", "--reason", "drill", "--confirm",
                    "--state-root", str(tmp_path), "--wait", "2")  # fmt: skip
    assert code == 0 and "confirmed: the risk desk has killed crypto" in out
    assert desk.halted("crypto")
    code, out = run(capsys, "resume", "--market", "crypto", "--reason", "drill over", "--confirm",
                    "--state-root", str(tmp_path), "--wait", "2")  # fmt: skip
    assert code == 0 and "confirmed: the risk desk has resumed crypto" in out
    assert not desk.halted("crypto")
    events = list(Ledger(tmp_path / "ledger.sqlite").read())
    assert [e.event_type for e in events].count(LedgerEventType.RISK_HALT) == 1
    assert kill_state(Ledger(tmp_path / "ledger.sqlite"))["markets"] == {}


@pytest.mark.parametrize(
    "argv", [["kill"], ["kill", "--market", "crypto", "--all", "--reason", "x"]]
)
def test_a_target_is_required_and_only_one(argv, capsys) -> None:
    with pytest.raises(SystemExit):
        main(argv)
