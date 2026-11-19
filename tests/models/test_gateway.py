"""Every model call goes through one gateway (firm rule 8, M3 plan R3).

- Work tagged `private` runs on local models; a cloud model is refused
  unless the owner opted in for that run (and never if the policy says so).
- Cloud spend has a budget per calendar month (default £0): a cloud call is
  refused once the month's spend has reached it.
- Every call, refused or not, is a MODEL_CALL ledger event with its role,
  provider, model, tag, tokens and cost, and never the prompt itself.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from firm.core.config import EgressConfig, load_models_config
from firm.core.domain import DataTag, LedgerEventType
from firm.core.events import CollectingSink
from firm.models.gateway import (
    Completion,
    EgressRefused,
    ModelGateway,
    OwnerOptIn,
    Target,
)
from verify_helpers import REPO

NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)
SECRET = "my private strategy: buy when fast MA > slow MA"


class FakeProvider:
    def __init__(self, local: bool, cost: float = 0.0) -> None:
        self.local, self.cost, self.calls = local, cost, []

    def chat(self, model, messages, *, timeout):
        self.calls.append((model, messages))
        return Completion(text="ok", input_tokens=120, output_tokens=30, cost_gbp=self.cost)


def gateway(*, budget=0.0, private_to_cloud="per_run_opt_in", spent=0.0, cloud_cost=0.25):
    cfg = load_models_config(REPO / "config")
    egress = EgressConfig(
        cloud_spend_budget_gbp=budget,
        private_to_cloud=private_to_cloud,
        log_every_cloud_call=True,
        local_providers=["ollama"],
    )
    cfg = cfg.model_copy(update={"egress": egress})
    providers = {"ollama": FakeProvider(local=True), "cloudco": FakeProvider(False, cloud_cost)}
    sink = CollectingSink()
    gw = ModelGateway(
        cfg, providers, sink=sink, clock=lambda: NOW, spent_this_month=lambda _now: spent
    )
    return gw, providers, sink


def calls(sink):
    return [p for t, _r, p, _i in sink.events if t is LedgerEventType.MODEL_CALL]


CLOUD = Target(provider="cloudco", model="big-1")
MSG = [{"role": "user", "content": SECRET}]


def test_a_local_call_runs_and_is_logged_without_its_prompt() -> None:
    gw, providers, sink = gateway()
    out = gw.chat(role="research", messages=MSG, tag=DataTag.PRIVATE)
    assert out.text == "ok" and len(providers["ollama"].calls) == 1
    model = load_models_config(REPO / "config").roles["research"].model
    assert providers["ollama"].calls[0][0] == model  # the role's model, from config
    (rec,) = calls(sink)
    assert rec["outcome"] == "ok" and rec["tag"] == "private" and rec["local"] is True
    assert (rec["role"], rec["provider"], rec["model"]) == ("research", "ollama", model)
    assert (rec["input_tokens"], rec["output_tokens"], rec["cost_gbp"]) == (120, 30, 0.0)
    assert SECRET not in str(sink.events)  # only a hash of the messages
    assert len(rec["messages_sha256"]) == 64


def test_private_work_never_reaches_a_cloud_model_without_an_opt_in() -> None:
    gw, providers, sink = gateway(budget=10.0)
    with pytest.raises(EgressRefused, match="private"):
        gw.chat(role="research", messages=MSG, tag=DataTag.PRIVATE, target=CLOUD)
    assert providers["cloudco"].calls == []
    (rec,) = calls(sink)
    assert rec["outcome"] == "refused" and rec["local"] is False and "private" in rec["refusal"]


def test_the_owners_opt_in_lets_one_run_use_a_cloud_model_and_is_logged() -> None:
    gw, providers, sink = gateway(budget=10.0)
    opt_in = OwnerOptIn(run_id="run-7", reason="owner asked for a frontier review")
    gw.chat(role="research", messages=MSG, tag=DataTag.PRIVATE, target=CLOUD, opt_in=opt_in)
    assert len(providers["cloudco"].calls) == 1
    (rec,) = calls(sink)
    assert rec["outcome"] == "ok" and rec["opt_in_run"] == "run-7" and rec["cost_gbp"] == 0.25


def test_a_never_policy_refuses_private_cloud_work_even_with_an_opt_in() -> None:
    gw, providers, _ = gateway(budget=10.0, private_to_cloud="never")
    with pytest.raises(EgressRefused):
        gw.chat(
            role="research",
            messages=MSG,
            tag=DataTag.PRIVATE,
            target=CLOUD,
            opt_in=OwnerOptIn(run_id="run-7", reason="x"),
        )
    assert providers["cloudco"].calls == []


def test_cloud_calls_stop_at_the_monthly_budget_default_zero() -> None:
    gw, providers, sink = gateway()  # budget £0
    with pytest.raises(EgressRefused, match="budget"):
        gw.chat(role="research", messages=MSG, tag=DataTag.PUBLIC, target=CLOUD)
    assert providers["cloudco"].calls == []

    gw, providers, sink = gateway(budget=1.0, spent=0.70, cloud_cost=0.25)
    gw.chat(role="research", messages=MSG, tag=DataTag.PUBLIC, target=CLOUD)  # 0.95 spent
    gw.chat(role="research", messages=MSG, tag=DataTag.PUBLIC, target=CLOUD)  # 1.20: over now
    with pytest.raises(EgressRefused, match="budget"):
        gw.chat(role="research", messages=MSG, tag=DataTag.PUBLIC, target=CLOUD)
    assert len(providers["cloudco"].calls) == 2
    assert [r["outcome"] for r in calls(sink)] == ["ok", "ok", "refused"]


def test_local_calls_are_not_held_back_by_the_cloud_budget() -> None:
    gw, providers, _ = gateway(budget=0.0, spent=99.0)
    gw.chat(role="fast_worker", messages=MSG, tag=DataTag.PUBLIC)
    assert len(providers["ollama"].calls) == 1


def test_a_failing_call_is_logged_and_raised() -> None:
    gw, providers, sink = gateway()

    def boom(model, messages, *, timeout):
        raise TimeoutError("model server did not answer")

    providers["ollama"].chat = boom
    with pytest.raises(TimeoutError):
        gw.chat(role="research", messages=MSG, tag=DataTag.PRIVATE)
    (rec,) = calls(sink)
    assert rec["outcome"] == "error" and "TimeoutError" in rec["error"]


def test_the_tag_is_required_and_must_be_a_data_tag() -> None:
    gw, _, _ = gateway()
    with pytest.raises(TypeError):
        gw.chat(role="research", messages=MSG)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        gw.chat(role="research", messages=MSG, tag="public")  # type: ignore[arg-type]


def test_a_provider_with_no_adapter_is_refused(tmp_path) -> None:
    gw, _, sink = gateway(budget=10.0)
    with pytest.raises(EgressRefused, match="no adapter"):
        gw.chat(role="research", messages=MSG, tag=DataTag.PUBLIC, target=Target("nope", "m"))
    assert calls(sink)[0]["outcome"] == "refused"


def test_a_call_for_a_dossier_names_it_so_its_budget_can_be_charged() -> None:
    gw, _, sink = gateway()
    gw.chat(role="research", messages=MSG, tag=DataTag.PRIVATE, dossier_id="d-001")
    assert calls(sink)[0]["dossier_id"] == "d-001"
