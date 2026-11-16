"""One test per firm rule (SPEC "Firm rules (safety invariants)").

Each is a failing-by-design placeholder, `xfail(strict=True)`, so the full
list of rules shows in every test report until it is really enforced.
When a rule's enforcement lands, replace its placeholder with the real test
and drop the marker. Never weaken or skip one of these to make other work
pass: stop and ask the owner.

Partial enforcement that already exists (schema-level) is tested in
tests/core and tests/config and noted on each placeholder.
"""

import pytest


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


@_placeholder("milestone 1: hidden out-of-sample check")
def test_rule_04_final_oos_opened_once_per_strategy_by_verifier_only_and_logged() -> None:
    pytest.fail("placeholder")


@_placeholder("milestone 1: trial-count check")
def test_rule_05_every_trial_is_counted_and_verifier_corrects_for_trial_count() -> None:
    pytest.fail("placeholder")


@_placeholder("milestone 2: ledger")
def test_rule_06_ledger_is_append_only() -> None:
    pytest.fail("placeholder")


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
