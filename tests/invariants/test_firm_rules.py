"""One test per firm rule (SPEC "Firm rules (safety invariants)").

Each is a failing-by-design placeholder, `xfail(strict=True)`, so the full
list of rules shows in every test report until it is really enforced.
When a rule's enforcement lands, replace its placeholder with the real test
and drop the marker. Never weaken or skip one of these to make other work
pass: stop and ask the owner.

Partial enforcement that already exists (schema-level) is tested in
tests/core and tests/config and noted on each placeholder. Rules 4 and 5
are enforced since milestone 1.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from firm.core.config import load_verifier_config
from firm.core.domain import CheckStatus, TrialRecord
from firm.data.dataset import HiddenPeriodError, open_dataset
from firm.verify.checks import trials as trials_check
from firm.verify.state import OosVault, TrialRegistry
from verify_helpers import NOW, REPO, run_verify


def _status(result, name):
    return next(c.status for c in result.verdict.checks if c.name == name)


def _metric(result, name, key):
    return next(c.metrics[key] for c in result.verdict.checks if c.name == name)


def _placeholder(milestone: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(reason=f"not enforced end-to-end yet ({milestone})", strict=True)


@_placeholder("milestone 2: execution desk + bus")
def test_rule_01_no_llm_output_reaches_execution_without_verdict_and_risk_approval() -> None:
    # Partial: only EXECUTION may publish order.intent; agents cannot (tests/core/test_bus.py).
    pytest.fail("placeholder")


@_placeholder("milestone 2: execution desk")
def test_rule_02_every_order_carries_verdict_id_and_risk_decision_id() -> None:
    # Partial: OrderIntent cannot be built without both (tests/core/test_domain.py).
    pytest.fail("placeholder")


@_placeholder("milestone 3/4: agent-facing interfaces")
def test_rule_03_agents_cannot_read_or_write_risk_config_capital_or_jurisdiction() -> None:
    pytest.fail("placeholder")


def test_rule_04_final_oos_opened_once_per_strategy_instrument_timeframe_by_verifier_and_logged(
    dataset, tmp_path
) -> None:
    """Research reads are capped before the hidden period; only the verifier's vault
    opens it; a second version in the same scope (strategy | instrument | timeframe)
    is refused, another scope gets its own opening; every attempt is logged and every
    first opening is counted as a trial; a strategy failing in-sample never opens it."""
    root, version = dataset
    ds = open_dataset(root, version)
    bars = ds.bars("kraken", "BTC-USD", "1d", as_of=NOW)
    hidden_from = bars["open_time"].iloc[len(bars) - round(len(bars) * 0.25)]
    with pytest.raises(HiddenPeriodError):
        ds.capped(hidden_from).bars("kraken", "BTC-USD", "1d", as_of=hidden_from)

    run_verify(dataset, tmp_path, "lookahead_ma.py", "LookaheadCrossover")  # fails in-sample
    assert OosVault(tmp_path).events() == []

    run_verify(dataset, tmp_path, "ma_crossover.py", "MovingAverageCrossover")
    run_verify(
        dataset,
        tmp_path,
        "ma_crossover.py",
        "MovingAverageCrossover",
        params={"fast": 12, "slow": 40},
    )
    events = [(e["event"], e["scope"]) for e in OosVault(tmp_path).events()]
    scope = "ma_crossover|BTC-USD|1d"
    assert events == [("first_opening", scope), ("sealed", scope), ("refused", scope)]
    assert [t.experiment_id for t in TrialRegistry(tmp_path).all()].count("oos_opening") == 1
    other = OosVault(tmp_path).request(
        strategy_id="ma_crossover",
        scope="ma_crossover|ETH-USD|1d",
        candidate_key="k",
        dataset_version="ds",
        window=["a", "b"],
        at=NOW,
    )
    assert other.access == "first_opening"

    src = Path(__file__).resolve().parents[2] / "src" / "firm"
    for path in src.rglob("*.py"):
        if path.is_relative_to(src / "verify") or path.is_relative_to(src / "control"):
            continue
        assert "firm.verify" not in path.read_text(), f"{path} reaches into the verifier"


def test_rule_05_every_trial_is_counted_and_verifier_corrects_for_trial_count(
    dataset, tmp_path
) -> None:
    """Unrecorded trials fail the check; the same strategy that passes after one trial
    fails once the global count says a thousand were tried."""
    clean = run_verify(dataset, tmp_path / "a", "ma_crossover.py", "MovingAverageCrossover")
    assert _status(clean, "trial_count") is CheckStatus.PASSED

    ctx = SimpleNamespace(
        registry=TrialRegistry(tmp_path / "empty"),
        trial_ids=("t-unknown",),
        config=load_verifier_config(REPO / "config"),
    )
    unrecorded = trials_check.run(ctx)  # type: ignore[arg-type]
    assert unrecorded.status is CheckStatus.FAILED and "unrecorded" in unrecorded.detail

    reg = TrialRegistry(tmp_path / "b")
    for i in range(1000):
        reg.record(
            TrialRecord(
                trial_id=f"x-{i}",
                experiment_id="search",
                strategy_id="other",
                parameters={"i": i},
                metrics={},
                dataset_version="ds",
                recorded_at=NOW,
            )
        )
    searched = run_verify(dataset, tmp_path / "b", "ma_crossover.py", "MovingAverageCrossover")
    assert _status(searched, "trial_count") is CheckStatus.FAILED
    assert _metric(searched, "trial_count", "n_trials") == 1001


def test_rule_06_ledger_is_append_only(tmp_path) -> None:
    """The database refuses UPDATE and DELETE; the hash chain exposes any edit made
    around those guards; the Python API has no update or delete at all."""
    import sqlite3

    from firm.core.domain import LedgerEventType
    from firm.ledger.store import Ledger

    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    for i in range(3):
        ledger.append(LedgerEventType.VERDICT, f"event {i}", {"i": i})
    assert not any(hasattr(ledger, m) for m in ("update", "delete", "remove", "edit"))
    con = sqlite3.connect(path)
    for sql in ("UPDATE events SET reason = 'x'", "DELETE FROM events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)
    con.execute("DROP TRIGGER events_no_update")
    con.execute("UPDATE events SET reason = 'rewritten' WHERE seq = 1")
    con.commit()
    assert not Ledger(path).verify_chain().ok


@_placeholder("milestone 4: web UI manual approval")
def test_rule_07_live_trading_false_and_enabling_needs_config_plus_manual_approval() -> None:
    # Partial: the loader refuses live_trading: true; adapters refuse live mode
    # (tests/config/test_loader.py, tests/execution/test_adapter_mode.py).
    pytest.fail("placeholder")


@_placeholder("milestone 3: egress gate")
def test_rule_08_every_model_call_passes_egress_gate_and_cloud_calls_are_logged() -> None:
    pytest.fail("placeholder")


@_placeholder("milestone 3: scheduler")
def test_rule_09_training_never_takes_trading_stack_reserved_memory_or_cpu() -> None:
    pytest.fail("placeholder")


@_placeholder("milestone 4: notifier")
def test_rule_10_notifications_owner_only_and_chat_read_only_except_confirmed_kill() -> None:
    # Partial: Notification has no recipient field; config recipients must be
    # [owner] (tests/core/test_domain.py, tests/config/test_loader.py).
    pytest.fail("placeholder")
