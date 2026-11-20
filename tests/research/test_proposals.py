"""Brief proposals from the senior quant: strict schema, append-only store,
outcome-only feedback (owner, 2026-11-20)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from firm.research.proposals import (
    BriefProposal,
    ProposalError,
    ProposalStore,
    outcomes,
    parse_proposal,
)

GOOD = {
    "title": "Time-series momentum on BTC 4h",
    "method_family": "momentum",
    "market": "crypto",
    "instruments": ["BTC-USD"],
    "timeframes": ["4h"],
    "hypothesis": "Returns over the last 20 days predict the next week's sign.",
    "why_edge": "Slow diffusion of information and trend-following flows.",
    "rules": "Hold a target of 1 when the 20-day return is positive, else 0.",
    "data_needed": "4h bars, two years or more",
    "falsified_if": "Net Sharpe under 0.5 after costs on the research view.",
    "expected_trades_per_month": 4,
    "cost_notes": "Few trades, so taker fees matter little.",
    "sources": [
        {
            "title": "Time series momentum (Moskowitz, Ooi, Pedersen 2012)",
            "url": "https://example.org/tsmom",
            "accessed": "2026-11-20",
        }
    ],
}


def text(**changes) -> str:
    return yaml.safe_dump(GOOD | changes)


def test_a_good_proposal_parses_and_renders_a_brief() -> None:
    p = parse_proposal(text(), markets={"crypto", "us_equities"})
    brief = p.as_brief()
    assert "Time-series momentum" in brief and "Falsified if:" in brief
    assert "https://example.org/tsmom" in brief


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"method_family": "market_making"}, "out of scope"),
        ({"method_family": "high_frequency"}, "out of scope"),
        ({"method_family": "astrology"}, "unknown method family"),
        ({"timeframes": ["7m"]}, "unknown timeframe"),
        ({"market": "forex"}, "unknown market"),
        ({"sources": []}, "sources"),
        ({"instruments": []}, "instruments"),
        ({"leverage": 3}, "leverage"),  # unknown key
        ({"expected_trades_per_month": 0}, "expected_trades_per_month"),
    ],
)
def test_a_bad_proposal_is_refused_with_the_reason(changes, message) -> None:
    with pytest.raises(ProposalError, match=message):
        parse_proposal(text(**changes), markets={"crypto"})


def test_not_yaml_or_not_a_mapping_is_refused() -> None:
    with pytest.raises(ProposalError):
        parse_proposal("- just\n- a list\n")
    with pytest.raises(ProposalError):
        parse_proposal("title: [unclosed")


def test_store_is_append_only_and_deduplicates(tmp_path) -> None:
    store = ProposalStore(tmp_path)
    p, new = store.add(BriefProposal.model_validate(GOOD))
    assert new and p.status == "proposed" and p.proposal_id.startswith("p-")
    again, new = store.add(BriefProposal.model_validate(GOOD))
    assert not new and again.proposal_id == p.proposal_id
    store.approve(p.proposal_id, "d-001")
    assert store.get(p.proposal_id).dossier_id == "d-001"
    with pytest.raises(ProposalError, match="already approved"):
        store.reject(p.proposal_id, "changed my mind")
    lines = (tmp_path / "research" / "proposals" / f"{p.proposal_id}.jsonl").read_text()
    assert [json.loads(x)["event"] for x in lines.splitlines()] == ["proposed", "approved"]


def test_reject_needs_a_reason_and_ids_cannot_escape_the_store(tmp_path) -> None:
    store = ProposalStore(tmp_path)
    p, _ = store.add(BriefProposal.model_validate(GOOD))
    with pytest.raises(ProposalError, match="reason"):
        store.reject(p.proposal_id, "  ")
    store.reject(p.proposal_id, "already tested")
    assert store.get(p.proposal_id).status == "rejected"
    with pytest.raises(ProposalError, match="not a proposal ID"):
        store.get("p-../../etc")


class _Dossiers:
    def get(self, _id):
        verdict = SimpleNamespace(verdict_id="v-1", outcome="fail")
        stage = SimpleNamespace(value="failed")
        return SimpleNamespace(stage=stage, verdicts=[verdict], hypothesis="SECRET HYPOTHESIS")


def test_outcomes_carry_check_names_only_never_metrics(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "v-1.json").write_text(
        json.dumps(
            {
                "checks": [
                    {"name": "lookahead_truncation", "status": "passed", "metrics": {"x": 1}},
                    {"name": "cost_realism", "status": "failed", "metrics": {"net_sharpe": 0.123}},
                ]
            }
        )
    )
    store = ProposalStore(tmp_path)
    p, _ = store.add(BriefProposal.model_validate(GOOD))
    store.approve(p.proposal_id, "d-001")
    rows = outcomes(store, _Dossiers(), reports)
    assert rows == [
        {
            "proposal_id": p.proposal_id,
            "title": GOOD["title"],
            "status": "approved",
            "stage": "failed",
            "verdicts": [{"outcome": "fail", "checks_not_passed": ["cost_realism"]}],
        }
    ]
    dumped = json.dumps(rows)
    assert "0.123" not in dumped and "SECRET" not in dumped
