"""One test per firm rule (SPEC "Firm rules (safety invariants)").

Each is a failing-by-design placeholder, `xfail(strict=True)`, so the full
list of rules shows in every test report until it is really enforced.
When a rule's enforcement lands, replace its placeholder with the real test
and drop the marker. Never weaken or skip one of these to make other work
pass: stop and ask the owner.

Partial enforcement that already exists (schema-level) is tested in
tests/core and tests/config and noted on each placeholder. Rules 4 and 5
are enforced since milestone 1; rule 6 since the ledger, rules 1 and 2 since
the market worker (milestone 2), rule 8 since the model gateway (M3, R3).
"""

from decimal import Decimal
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


def _submit_calls() -> list[tuple[str, int, str]]:
    """Every `.submit(...)` call in src/firm outside the adapters themselves:
    (file, line, how its argument was made)."""
    import ast

    out = []
    for path in sorted((REPO / "src" / "firm").rglob("*.py")):
        if path.name in ("adapter.py", "simulator.py"):
            continue
        tree = ast.parse(path.read_text())
        made: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fn = node.value.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "?")
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        made[t.id] = name
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "submit"
            ):
                arg = node.args[0] if node.args else None
                how = made.get(arg.id, "?") if isinstance(arg, ast.Name) else "?"
                out.append((str(path.relative_to(REPO)), node.lineno, how))
    return out


def test_rule_01_no_llm_output_reaches_execution_without_verdict_and_risk_approval() -> None:
    """Orders reach a venue only as `build_intent(proposal, risk_decision)`: every
    submit in the codebase takes a value made by build_intent, the market worker has
    no input that accepts an order, and a rejecting risk desk means no order at all.
    (Bus side: only EXECUTION may publish order.intent, tests/core/test_bus.py.)"""
    import inspect

    from firm.core.domain import OrderIntent, OrderProposal, RiskDecision
    from firm.execution.worker import MarketWorker

    calls = _submit_calls()
    assert calls, "expected the execution worker to submit orders"
    assert all(how == "build_intent" for _, _, how in calls), calls

    order_types = {OrderIntent, OrderProposal, RiskDecision}
    for name, fn in inspect.getmembers(MarketWorker, inspect.isfunction):
        if name.startswith("_"):
            continue
        hints = {p.annotation for p in inspect.signature(fn).parameters.values()}
        assert not any(
            t in str(h) for h in hints for t in ("OrderIntent", "OrderProposal", "RiskDecision")
        ), f"MarketWorker.{name} accepts an order from outside"
        assert not (hints & order_types)

    from tests_worker_rig import run_bars

    submitted, events = run_bars(reject_all=True)
    assert submitted == [] and "order_intent" not in {e for e, *_ in events}


def test_rule_02_every_order_carries_verdict_id_and_risk_decision_id() -> None:
    """Every order the venue receives names the deployment's verdict and a risk
    decision that approved (or shrank) exactly that proposal, recorded in the ledger.
    (Schema side: an OrderIntent can't be built without both, tests/core.)"""
    from tests_worker_rig import run_bars

    submitted, events = run_bars(reject_all=False)
    assert submitted, "expected some orders"
    decisions = {p["decision_id"]: p for e, _, p, _ in events if e == "risk_decision"}
    for intent in submitted:
        assert intent.verdict_id == "v-1"
        d = decisions[intent.risk_decision_id]
        assert d["proposal_id"] == intent.proposal_id and d["action"] in ("approve", "resize")
        assert Decimal(d["approved_quantity"]) >= intent.quantity


@_placeholder("milestone 3/4: agent-facing interfaces")
def test_rule_03_agents_cannot_read_or_write_risk_config_capital_or_jurisdiction() -> None:
    # Partial: agent code (the only agent-facing interface so far) runs in the
    # sandbox with the repo hidden, so it can't read or write config/ (risk,
    # jurisdiction, verifier thresholds, research.yaml):
    # tests/sandbox/test_agent_cannot_read_config.py. Still to come: the control
    # API's agent endpoints, Certo runs, the MCP endpoint.
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


# What only a model server or a model SDK would need. Outside `firm.models`
# (the gateway and its provider adapters) none of it may appear in the code.
MODEL_ENDPOINTS = (
    "/api/chat",
    "/api/generate",
    "/v1/chat",
    "/v1/completions",
    "/v1/messages",
    "/v1/systemone",
    "/v1/responses",
    "/api/embed",
)
MODEL_SDKS = ("openai", "anthropic", "ollama", "litellm", "google.generativeai", "mistralai")


def test_rule_08_every_model_call_passes_egress_gate_and_cloud_calls_are_logged() -> None:
    """Private work never reaches a cloud model without the owner's per-run opt-in;
    every call (local, cloud, refused) is a MODEL_CALL event with tag and cost; and
    no code outside the gateway can call a model at all, Certo included (it calls
    a model itself, so only firm.models may launch it)."""
    import ast

    from firm.core.config import load_models_config
    from firm.core.domain import DataTag, LedgerEventType
    from firm.core.events import CollectingSink
    from firm.models.gateway import Completion, EgressRefused, ModelGateway, OwnerOptIn, Target

    class Fake:
        def __init__(self, local: bool) -> None:
            self.local, self.sent = local, 0

        def chat(self, model, messages, *, timeout):
            self.sent += 1
            return Completion("ok", 10, 5, 0.0 if self.local else 0.1)

    cfg = load_models_config(REPO / "config")
    assert cfg.egress.cloud_spend_budget_gbp == 0  # the shipped default
    cfg = cfg.model_copy(
        update={"egress": cfg.egress.model_copy(update={"cloud_spend_budget_gbp": 5.0})}
    )
    local, cloud = Fake(True), Fake(False)
    sink = CollectingSink()
    gw = ModelGateway(
        cfg, {"ollama": local, "cloudco": cloud}, sink=sink, spent_this_month=lambda _n: 0.0
    )
    msg = [{"role": "user", "content": "strategy code"}]
    big = Target("cloudco", "frontier")

    gw.chat(role="research", messages=msg, tag=DataTag.PRIVATE)  # local: fine
    with pytest.raises(EgressRefused):
        gw.chat(role="research", messages=msg, tag=DataTag.PRIVATE, target=big)
    assert cloud.sent == 0
    gw.chat(role="research", messages=msg, tag=DataTag.PUBLIC, target=big)
    gw.chat(
        role="research",
        messages=msg,
        tag=DataTag.PRIVATE,
        target=big,
        opt_in=OwnerOptIn(run_id="run-1", reason="owner"),
    )
    logged = [p for t, _r, p, _i in sink.events if t is LedgerEventType.MODEL_CALL]
    assert [(p["outcome"], p["tag"], p["local"]) for p in logged] == [
        ("ok", "private", True),
        ("refused", "private", False),
        ("ok", "public", False),
        ("ok", "private", False),
    ]
    assert logged[3]["opt_in_run"] == "run-1" and logged[3]["cost_gbp"] == 0.1
    assert "strategy code" not in str(sink.events)

    src = REPO / "src" / "firm"
    for path in src.rglob("*.py"):
        if path.is_relative_to(src / "models"):
            continue
        text = path.read_text()
        # Certo calls a model itself: only firm.models (the gate) may launch it
        tree = ast.parse(text)
        docstrings = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                value = node.value.strip().lower()
                launches = value in ("certo", "certo-mcp") or "certo-mcp" in value
                assert not launches, f"{path.relative_to(src)} launches Certo outside firm.models"
        for endpoint in MODEL_ENDPOINTS:
            assert endpoint not in text, f"{path.relative_to(src)} names {endpoint}"
        for node in ast.walk(ast.parse(text)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            for name in names:
                assert name.split(".")[0] not in {m.split(".")[0] for m in MODEL_SDKS} or (
                    name.startswith("firm.")
                ), f"{path.relative_to(src)} imports {name}"


@_placeholder("milestone 3: scheduler")
def test_rule_09_training_never_takes_trading_stack_reserved_memory_or_cpu() -> None:
    pytest.fail("placeholder")


@_placeholder("milestone 4: notifier")
def test_rule_10_notifications_owner_only_and_chat_read_only_except_confirmed_kill() -> None:
    # Partial: Notification has no recipient field; config recipients must be
    # [owner] (tests/core/test_domain.py, tests/config/test_loader.py).
    pytest.fail("placeholder")
