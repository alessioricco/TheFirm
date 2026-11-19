"""`config/research.yaml`: the research floor's settings (M3 plan, M1).

Owner-editable, read by deterministic code only (firm rule 3: no agent may
read or change it; see tests/sandbox/test_agent_cannot_read_config.py).
Strict like every profile: unknown keys, missing values and out-of-range
numbers are errors, and cloud spend can't exceed the egress budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from firm.core.config import ConfigError, load_research_config

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def raw() -> dict:
    return yaml.safe_load((REPO_CONFIG / "research.yaml").read_text())


def write(tmp: Path, data: dict) -> Path:
    for name in ("models.yaml",):  # the egress budget it is checked against
        (tmp / name).write_text((REPO_CONFIG / name).read_text())
    (tmp / "research.yaml").write_text(yaml.safe_dump(data))
    return tmp


def test_the_shipped_defaults() -> None:
    cfg = load_research_config(REPO_CONFIG)
    assert cfg.researchers == 2
    assert cfg.budgets.cloud_spend_gbp == 0
    assert cfg.budgets.trials > 0 and cfg.budgets.wall_clock_hours > 0 and cfg.budgets.tokens > 0
    # owner, 2026-11-19: at most 3 paper strategies, correlation <= 0.7, 10% each
    assert cfg.promotion.max_paper_strategies == 3
    assert cfg.promotion.max_correlation == 0.7
    assert cfg.promotion.paper_allocation_pct == 10


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("researchers",), 0),
        (("budgets", "trials"), 0),
        (("budgets", "wall_clock_hours"), -1),
        (("budgets", "tokens"), 0),
        (("budgets", "cloud_spend_gbp"), -0.01),
        (("promotion", "max_paper_strategies"), 0),
        (("promotion", "max_correlation"), 1.5),
        (("promotion", "max_correlation"), -0.1),
        (("promotion", "paper_allocation_pct"), 0),
        (("promotion", "paper_allocation_pct"), 101),
    ],
)
def test_out_of_range_values_are_refused(tmp_path, path, value) -> None:
    data = raw()
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ConfigError):
        load_research_config(write(tmp_path, data))


def test_unknown_or_missing_keys_are_refused(tmp_path) -> None:
    data = raw()
    data["budgets"]["gpu_hours"] = 3
    with pytest.raises(ConfigError, match="gpu_hours"):
        load_research_config(write(tmp_path, data))
    data = raw()
    del data["promotion"]
    with pytest.raises(ConfigError, match="promotion"):
        load_research_config(write(tmp_path, data))


def test_a_dossier_cant_be_given_more_cloud_spend_than_the_egress_budget(tmp_path) -> None:
    data = raw()
    data["budgets"]["cloud_spend_gbp"] = 5  # models.yaml says £0 a month
    with pytest.raises(ConfigError, match="cloud"):
        load_research_config(write(tmp_path, data))


def test_all_paper_strategies_together_fit_in_paper_capital(tmp_path) -> None:
    data = raw()
    data["promotion"]["max_paper_strategies"] = 11
    data["promotion"]["paper_allocation_pct"] = 10  # 110% of paper capital
    with pytest.raises(ConfigError, match="100"):
        load_research_config(write(tmp_path, data))
