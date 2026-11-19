"""Firm rule 3, for the agent-facing interface that exists today: agent code.

Agents cannot read or write the risk profile, capital allocation, the
jurisdiction profile, the verifier's thresholds or the research floor's
settings (`config/research.yaml`, M3 plan M1). Agent code runs only in the
sandbox, and every caller hides the whole repository from it, `config/`
included. The rest of rule 3 (the control API's agent endpoints, Certo
runs, MCP) is still a placeholder in tests/invariants until they exist.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from firm.research.store import save_strategy
from firm.sandbox.client import SandboxedStrategy, StrategyError
from firm.sandbox.policy import DEFAULT_IMPORTS, Policy, detect, hidden_roots
from verify_helpers import EXAMPLES, NOW, REPO

CONFIG = REPO / "config"
PROTECTED = [
    CONFIG / "risk" / "default.yaml",
    CONFIG / "jurisdictions" / "uk.yaml",
    CONFIG / "verify" / "default.yaml",
    CONFIG / "research.yaml",
]


def _covered(path: Path, roots: list[str]) -> bool:
    p = os.path.realpath(path)
    return any(p == r or p.startswith(r + os.sep) for r in roots)


@pytest.fixture
def captured(monkeypatch):
    """The policy each sandbox is started with (no OS sandbox needed)."""
    import firm.verify.strategy as strategy

    policies: list[Policy] = []

    class Spy:
        def __init__(self, path, class_name, policy, **kw) -> None:
            policies.append(policy)
            raise RuntimeError("captured")  # stop here: only the policy matters

    monkeypatch.setattr(strategy, "SandboxedStrategy", Spy)
    return policies


def test_the_verifier_hides_config_from_agent_code(dataset, tmp_path, captured) -> None:
    from firm.verify.runner import verify_strategy_file

    root, version = dataset
    saved = save_strategy(root, "d-rule3", (EXAMPLES / "ma_crossover.py").read_bytes())
    with pytest.raises(RuntimeError, match="captured"):
        verify_strategy_file(
            strategy_path=saved.path,
            class_name="MovingAverageCrossover",
            params=None,
            data_root=root,
            dataset_version=version,
            venue="kraken",
            instrument="BTC-USD",
            tf="1d",
            config_root=CONFIG,
            jurisdiction="uk",
            market="crypto",
            state=tmp_path,
            now=NOW,
        )
    (policy,) = captured
    assert all(_covered(p, hidden_roots(policy)) for p in PROTECTED)


def test_research_backtests_hide_config_from_agent_code(dataset, tmp_path, captured) -> None:
    from firm.verify.research import ResearchBacktester

    root, version = dataset
    saved = save_strategy(root, "d-rule3", (EXAMPLES / "ma_crossover.py").read_bytes())
    bench = ResearchBacktester(
        strategy_path=saved.path,
        class_name="MovingAverageCrossover",
        experiment_id="e-rule3",
        data_root=root,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=CONFIG,
        jurisdiction="uk",
        market="crypto",
        state=tmp_path,
        now=NOW,
    )
    with pytest.raises(RuntimeError, match="captured"), bench:
        pass
    (policy,) = captured
    assert all(_covered(p, hidden_roots(policy)) for p in PROTECTED)


def test_sandboxed_code_can_neither_read_nor_write_the_protected_profiles(tmp_path) -> None:
    if detect() is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec / bubblewrap)")
    policy = Policy(hidden=(REPO,), imports=DEFAULT_IMPORTS | {"os"})  # as the callers pass it
    for target in PROTECTED:
        before = target.read_bytes()
        code = textwrap.dedent(
            f"""
            import os
            import pandas as pd

            class S:
                name = "probe"
                default_params = {{}}

                def signals(self, bars, params):
                    found = []
                    try:
                        found.append(os.read(os.open({str(target)!r}, os.O_RDONLY), 64))
                    except OSError as e:
                        found.append(f"read blocked: {{e}}")
                    try:
                        os.close(os.open({str(target)!r}, os.O_WRONLY | os.O_APPEND))
                        found.append("WROTE")
                    except OSError as e:
                        found.append(f"write blocked: {{e}}")
                    raise ValueError(found)
            """
        )
        probe = tmp_path / "probe.py"
        probe.write_text(code)
        bars = __import__("pandas").DataFrame({"close": [1.0, 2.0]})
        with (
            SandboxedStrategy(probe, "S", policy) as s,
            pytest.raises(StrategyError) as err,
        ):
            s.signals(bars, {})
        msg = str(err.value)
        assert "read blocked" in msg and "write blocked" in msg and "WROTE" not in msg, msg
        assert target.read_bytes() == before
