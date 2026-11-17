"""The verifier mirrors trials, vault events and verdicts into the ledger, and old
state can be imported once (idempotently)."""

from collections import Counter
from pathlib import Path

import pytest

from firm.control.cli import main
from firm.core.domain import LedgerEventType as T
from firm.ledger.sink import LedgerSink, ledger_path
from firm.ledger.store import Ledger
from firm.verify.runner import verify_strategy_file
from verify_helpers import EXAMPLES, NOW, REPO


def _verify(dataset, state: Path, sink=None, file="ma_crossover.py", cls="MovingAverageCrossover"):
    root, version = dataset
    kw = {} if sink is None else {"sink": sink}
    return verify_strategy_file(
        strategy_path=EXAMPLES / file,
        class_name=cls,
        params=None,
        data_root=root,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=state,
        now=NOW,
        **kw,
    )


def test_a_verification_lands_in_the_ledger(dataset, tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    r = _verify(dataset, tmp_path / "state", LedgerSink(ledger))
    kinds = Counter(e.event_type for e in ledger.read())
    assert kinds[T.TRIAL] == 2  # the in-sample run and the OOS opening
    assert kinds[T.OOS_VAULT] == 2  # first_opening, sealed
    assert kinds[T.VERDICT] == 1
    verdict = next(e for e in ledger.read() if e.event_type is T.VERDICT)
    assert verdict.payload["verdict_id"] == r.verdict.verdict_id
    assert verdict.payload["checks"]["hidden_out_of_sample"] == "passed"
    assert ledger.verify_chain().ok


def test_replaying_a_verification_adds_only_the_new_facts(dataset, tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    _verify(dataset, tmp_path / "state", LedgerSink(ledger))
    n = ledger.count()
    _verify(dataset, tmp_path / "state", LedgerSink(ledger))
    new = [e.event_type for e in ledger.read(n)]
    assert new == [T.OOS_VAULT]  # the replay attempt itself; trial and verdict are the same facts


def test_import_state_is_idempotent_and_matches_live_mirroring(dataset, tmp_path, capsys) -> None:
    state = tmp_path / "state"
    _verify(dataset, state)  # no sink: state only, as before the ledger existed
    _verify(dataset, state, file="lookahead_ma.py", cls="LookaheadCrossover")
    (state / "reports").mkdir(exist_ok=True)
    assert main(["ledger", "import-state", "--state-root", str(state)]) == 0
    ledger = Ledger(ledger_path(state))
    kinds = Counter(e.event_type for e in ledger.read())
    assert kinds[T.TRIAL] == 3 and kinds[T.OOS_VAULT] == 2
    n = ledger.count()
    assert main(["ledger", "import-state", "--state-root", str(state)]) == 0
    assert Ledger(ledger_path(state)).count() == n  # second import: nothing new
    assert main(["ledger", "verify", "--state-root", str(state)]) == 0
    assert "hash chain intact" in capsys.readouterr().out


def test_cli_verify_writes_to_the_ledger(dataset, tmp_path, capsys) -> None:
    root, version = dataset
    state = tmp_path / "state"
    assert (
        main(
            [
                "verify",
                "--strategy",
                f"{EXAMPLES}/ma_crossover.py:MovingAverageCrossover",
                "--dataset",
                version,
                "--venue",
                "kraken",
                "--instrument",
                "BTC-USD",
                "--tf",
                "1d",
                "--market",
                "crypto",
                "--config",
                str(REPO / "config"),
                "--root",
                str(root),
                "--state-root",
                str(state),
            ]
        )
        == 0
    )
    assert main(["ledger", "show", "--state-root", str(state), "--tail", "3"]) == 0
    out = capsys.readouterr().out
    assert "verdict" in out and "events" in out


def test_ledger_verify_reports_a_broken_chain(tmp_path, capsys) -> None:
    import sqlite3

    state = tmp_path / "state"
    ledger = Ledger(ledger_path(state))
    for i in range(3):
        ledger.append(T.VERDICT, f"e{i}", {"i": i})
    con = sqlite3.connect(ledger_path(state))
    con.execute("DROP TRIGGER events_no_update")
    con.execute("UPDATE events SET reason = 'x' WHERE seq = 1")
    con.commit()
    assert main(["ledger", "verify", "--state-root", str(state)]) == 1
    assert "BROKEN at sequence 1" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["show", "verify"])
def test_ledger_commands_on_an_empty_state(tmp_path, cmd) -> None:
    assert main(["ledger", cmd, "--state-root", str(tmp_path / "s")]) == 0
