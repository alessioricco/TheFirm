"""Agent strategies are verified only through the sandbox (M3 plan, R2b).

- A strategy in the private store always runs in the sandbox; loading it
  in-process is refused, and with no OS sandbox nothing runs at all.
- Through the sandbox, the verdict is byte-for-byte the in-process one.
- A sandbox failure is never a judgement on the strategy: the remaining
  checks don't run and the verdict is UNVERIFIED, never FAIL or PASS.
- Trials stay counted: an out-of-sample opening is a trial the moment the
  vault grants it, even if computing its signals then fails.
"""

from __future__ import annotations

import json
import os

import pytest

from firm.core.domain import CheckStatus, VerdictOutcome
from firm.research.store import save_strategy
from firm.sandbox.client import SandboxError
from firm.sandbox.policy import detect
from firm.verify.state import OosVault, TrialRegistry
from firm.verify.strategy import SandboxRequired, load_strategy, sandbox_policy
from verify_helpers import EXAMPLES, NOW, REPO

CODE = (EXAMPLES / "ma_crossover.py").read_bytes()
CLS = "MovingAverageCrossover"


def verify(dataset, state, path, **kw):
    from firm.verify.runner import verify_strategy_file

    root, version = dataset
    return verify_strategy_file(
        strategy_path=path,
        class_name=CLS,
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


def test_a_private_strategy_is_never_loaded_in_process(tmp_path) -> None:
    saved = save_strategy(tmp_path, "d-001", CODE)
    with pytest.raises(SandboxRequired):
        load_strategy(saved.path, CLS, private_root=tmp_path / "research")
    # the runner's policy: private store -> sandbox; elsewhere only on request
    policy = sandbox_policy(saved.path, data_root=tmp_path, hidden=())
    assert policy is not None and os.path.realpath(tmp_path) in map(str, policy.hidden)
    assert sandbox_policy(EXAMPLES / "ma_crossover.py", data_root=tmp_path, hidden=()) is None
    forced = sandbox_policy(EXAMPLES / "ma_crossover.py", data_root=tmp_path, hidden=(), force=True)
    assert forced is not None


def test_with_no_os_sandbox_a_private_strategy_is_not_verified(dataset, tmp_path, monkeypatch):
    import firm.sandbox.client as client

    monkeypatch.setattr(client, "detect", lambda: None)
    root, _ = dataset
    saved = save_strategy(root, "d-001", CODE)  # in the store the verifier reads
    with pytest.raises(SandboxError, match="refusing to run strategy code unsandboxed"):
        verify(dataset, tmp_path / "state", saved.path)
    assert TrialRegistry(tmp_path / "state").count() == 0  # nothing ran, nothing counted


class Breaks:
    """An in-process strategy whose 'sandbox' dies when `when(bars)` is true."""

    def __init__(self, inner, when) -> None:
        self.inner, self.when, self.calls = inner, when, 0
        self.name, self.default_params = inner.name, inner.default_params

    def signals(self, bars, params):
        self.calls += 1
        if self.when(self, bars):
            raise SandboxError("sandbox worker failed: no answer in time (memory limit)")
        return self.inner.signals(bars, params)


def breaking(monkeypatch, when):
    import firm.verify.runner as runner

    real = runner.load_strategy

    def load(path, class_name, **kw):
        loaded = real(path, class_name)
        object.__setattr__(loaded, "strategy", Breaks(loaded.strategy, when))
        return loaded

    monkeypatch.setattr(runner, "load_strategy", load)


def test_a_sandbox_failure_during_the_checks_is_unverified_not_fail(dataset, tmp_path, monkeypatch):
    breaking(monkeypatch, lambda s, _bars: s.calls >= 2)  # in-sample run fine, then it dies
    r = verify(dataset, tmp_path, EXAMPLES / "ma_crossover.py")
    assert r.verdict.outcome is VerdictOutcome.UNVERIFIED and r.exit_code == 2
    checks = {c.name: c for c in r.verdict.checks}
    assert checks["lookahead_truncation"].status is CheckStatus.NOT_RUN
    assert "sandbox" in checks["lookahead_truncation"].detail
    assert all(c.status is not CheckStatus.FAILED for c in r.verdict.checks)
    assert checks["hidden_out_of_sample"].status is CheckStatus.NOT_RUN
    assert TrialRegistry(tmp_path).count() == 1  # the in-sample run was a real trial


def test_an_oos_opening_counts_as_a_trial_even_if_the_sandbox_then_fails(
    dataset, tmp_path, monkeypatch
) -> None:
    from firm.data.dataset import open_dataset

    root, version = dataset
    last = open_dataset(root, version).bars("kraken", "BTC-USD", "1d", as_of=NOW)
    last_close = last["close_time"].iloc[-1]
    breaking(monkeypatch, lambda _s, bars: bars["close_time"].iloc[-1] == last_close)
    r = verify(dataset, tmp_path, EXAMPLES / "ma_crossover.py")
    oos = next(c for c in r.verdict.checks if c.name == "hidden_out_of_sample")
    assert r.verdict.outcome is VerdictOutcome.UNVERIFIED
    assert oos.status is CheckStatus.NOT_RUN and "sandbox" in oos.detail
    openings = [t for t in TrialRegistry(tmp_path).all() if t.experiment_id == "oos_opening"]
    assert len(openings) == 1  # the vault was opened: that is a trial (firm rule 5)

    monkeypatch.undo()  # the sandbox works again: same candidate replays, nothing double-counted
    again = verify(dataset, tmp_path, EXAMPLES / "ma_crossover.py")
    assert again.verdict.outcome is VerdictOutcome.PASS
    assert TrialRegistry(tmp_path).count() == 2  # one manual trial + one opening
    sealed = [e for e in OosVault(tmp_path).events() if e["event"] == "sealed"]
    assert len(sealed) == 1


@pytest.fixture
def os_sandbox():
    if detect() is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec / bubblewrap)")


def test_through_the_sandbox_the_verdict_is_the_in_process_one(dataset, tmp_path, os_sandbox):
    root, _ = dataset
    saved = save_strategy(root, "d-042", CODE, stem="agent_ma")
    boxed = verify(dataset, tmp_path / "a", saved.path)
    local = verify(dataset, tmp_path / "b", EXAMPLES / "ma_crossover.py")
    assert boxed.report == local.report  # same bytes, same verdict ID
    assert json.loads(boxed.report)["outcome"] == "pass"
