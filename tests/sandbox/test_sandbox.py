"""Agent strategy code runs only in a locked-down worker (M3 plan, R2).

The worker can't open a socket, read the owner's files or `.env`, write
outside its own folder, start programs, see the firm's environment, or
outlive its limits; with no OS sandbox nothing runs at all. A clean
strategy gives exactly the signals it gives in-process.

These tests need a working OS sandbox (`sandbox-exec` on macOS, `bubblewrap`
on Linux) and skip as UNVERIFIED without one, for example inside another
sandbox, where macOS can't nest them. CI sets FIRM_REQUIRE_SANDBOX=1 so a
missing sandbox fails the build there instead of skipping.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from firm.sandbox.client import SandboxedStrategy, SandboxError, StrategyError
from firm.sandbox.policy import DEFAULT_IMPORTS, Limits, Policy, detect, hidden_roots
from firm.verify.strategy import load_strategy
from verify_helpers import EXAMPLES

OS_ACCESS = DEFAULT_IMPORTS | {"os", "socket", "subprocess"}


@pytest.fixture(scope="module")
def backend():
    found = detect()
    if found is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec / bubblewrap)")
    return found


def bars(n: int = 300) -> pd.DataFrame:
    t = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    close = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, n))
    return pd.DataFrame(
        {
            "open_time": t,
            "close_time": t + pd.Timedelta(days=1),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1.0,
        }
    )


def strategy_file(tmp_path: Path, body: str, header: str = "") -> Path:
    """A strategy whose `signals` runs `body` and then returns flat signals."""
    code = textwrap.dedent(header) + textwrap.dedent(
        """
        import pandas as pd

        class S:
            name = "probe"
            default_params = {"x": 1}

            def signals(self, bars, params):
        """
    )
    code += textwrap.indent(textwrap.dedent(body), " " * 8)
    code += "\n        return pd.Series(0.0, index=bars.index)\n"
    path = tmp_path / "probe.py"
    path.write_text(code)
    return path


def blocked(tmp_path: Path, backend, body: str, policy: Policy) -> str:
    """Run `body` in the sandbox; return the error it raised (fails if it succeeded)."""
    path = strategy_file(tmp_path, body)
    with (
        SandboxedStrategy(path, "S", policy, backend=backend) as s,
        pytest.raises(StrategyError) as e,
    ):
        s.signals(bars(10), {})
    return str(e.value)


def test_a_clean_strategy_gives_the_same_signals_as_in_process(backend) -> None:
    path = EXAMPLES / "ma_crossover.py"
    local = load_strategy(path, "MovingAverageCrossover").strategy
    b = bars()
    with SandboxedStrategy(path, "MovingAverageCrossover", backend=backend) as s:
        assert s.name == "ma_crossover"
        assert s.default_params == {"fast": 10, "slow": 40}
        for params in ({"fast": 10, "slow": 40}, {"fast": 5, "slow": 20}):
            pd.testing.assert_series_equal(
                s.signals(b, params), local.signals(b, params), check_names=False
            )
        sliced = b.iloc[50:120]  # the verifier passes slices; the index is kept
        assert s.signals(sliced, s.default_params).index.equals(sliced.index)


def test_the_worker_cannot_reach_the_network(tmp_path, backend) -> None:
    import socket

    server = socket.socket()  # a service on the host's loopback
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0)
    port = server.getsockname()[1]
    try:
        for host, p in (("127.0.0.1", port), ("192.0.2.1", 80)):  # host, outside (TEST-NET)
            body = f"import socket\nsocket.create_connection(({host!r}, {p}), timeout=2)\n"
            err = blocked(tmp_path, backend, body, Policy(imports=OS_ACCESS))
            # macOS refuses outright; Linux has its own empty network namespace
            assert any(s in err for s in ("not permitted", "refused", "unreachable")), err
        with pytest.raises(BlockingIOError):
            server.accept()  # nothing ever reached the host's service
    finally:
        server.close()


def test_the_worker_cannot_read_hidden_folders(tmp_path, backend) -> None:
    repo = tmp_path / "repo"  # stands in for the repo, a store, or the home folder
    repo.mkdir()
    (repo / ".env").write_text("KEY=planted\n")
    body = f"import os\nraise ValueError(os.read(os.open({str(repo / '.env')!r}, 0), 100))\n"
    err = blocked(tmp_path, backend, body, Policy(hidden=(repo,), imports=OS_ACCESS))
    assert "planted" not in err
    # macOS refuses the read; Linux mounts an empty folder over the hidden one
    assert "not permitted" in err or "No such file" in err
    assert os.path.realpath(Path.home()) in hidden_roots(Policy())  # always hidden


def test_the_worker_cannot_write_outside_its_folder(tmp_path, backend) -> None:
    target = tmp_path / "escaped.txt"
    body = f"""
        import os
        try:
            os.close(os.open({str(target)!r}, os.O_WRONLY | os.O_CREAT))
            result = "wrote"
        except OSError as e:
            result = f"blocked: {{e}}"
        raise ValueError(result)
        """
    err = blocked(tmp_path, backend, body, Policy(imports=OS_ACCESS))
    assert not target.exists()  # nothing reached the real folder
    if backend == "seatbelt":
        assert "blocked" in err and "not permitted" in err


def test_the_worker_cannot_start_programs_on_macos(tmp_path, backend) -> None:
    if backend != "seatbelt":
        pytest.skip("Linux: programs may start, inside the same sandbox (no network, hidden files)")
    body = "import subprocess\nsubprocess.run(['/bin/echo', 'hi'], check=True)\n"
    assert "not permitted" in blocked(tmp_path, backend, body, Policy(imports=OS_ACCESS))


def test_the_worker_sees_none_of_the_firms_environment(tmp_path, backend, monkeypatch) -> None:
    monkeypatch.setenv("FIRM_TEST_PLANTED_SECRET", "s3cret")
    body = "import os\nraise ValueError(sorted(os.environ))\n"
    err = blocked(tmp_path, backend, body, Policy(imports=OS_ACCESS))
    assert "FIRM_TEST_PLANTED_SECRET" not in err and "PATH" in err


def test_imports_are_limited_to_the_allow_list(tmp_path, backend) -> None:
    path = strategy_file(tmp_path, "pass", header="import socket\n")
    with pytest.raises(StrategyError, match="import of 'socket' is not allowed"):
        SandboxedStrategy(path, "S", backend=backend).start()
    body = "open('x', 'w')\n"  # file access is gone from the builtins
    assert "open" in blocked(tmp_path, backend, body, Policy())


def test_a_strategy_error_keeps_the_worker_and_a_wrong_length_is_refused(tmp_path, backend) -> None:
    body = "if params.get('boom'):\n    raise ValueError('need fast < slow')\n"
    with SandboxedStrategy(strategy_file(tmp_path, body), "S", backend=backend) as s:
        with pytest.raises(StrategyError, match="ValueError: need fast < slow"):
            s.signals(bars(10), {"boom": True})
        assert (s.signals(bars(10), {}) == 0.0).all()  # still serving
    short = tmp_path / "short"
    short.mkdir()
    body = "return pd.Series([0.0])\n"
    with (
        SandboxedStrategy(strategy_file(short, body), "S", backend=backend) as s,
        pytest.raises(StrategyError, match="1 signals for 10 bars"),
    ):
        s.signals(bars(10), {})


@pytest.mark.parametrize(
    ("body", "limits", "reason"),
    [
        ("while True:\n    pass\n", Limits(call_seconds=1.5), "wall-clock limit"),
        ("while True:\n    pass\n", Limits(cpu_seconds=1, call_seconds=20), "CPU limit"),
        (
            "import numpy as np\nx = [np.ones(1 << 24) for _ in range(40)]\n",
            Limits(memory_mb=300, call_seconds=20),
            "memory limit",
        ),
    ],
    ids=["wall-clock", "cpu", "memory"],
)
def test_the_worker_cannot_outlive_its_limits(tmp_path, backend, body, limits, reason) -> None:
    path = strategy_file(tmp_path, body)
    s = SandboxedStrategy(path, "S", Policy(limits=limits), backend=backend).start()
    with pytest.raises(SandboxError, match=reason):
        s.signals(bars(10), {})
    with pytest.raises(SandboxError, match="not running"):
        s.signals(bars(10), {})  # gone for good


def test_without_an_os_sandbox_nothing_runs(tmp_path, monkeypatch) -> None:
    import firm.sandbox.client as client

    monkeypatch.setattr(client, "detect", lambda: None)
    started = []
    monkeypatch.setattr(client.subprocess, "Popen", lambda *a, **k: started.append(a))
    with pytest.raises(SandboxError, match="refusing to run strategy code unsandboxed"):
        SandboxedStrategy(EXAMPLES / "ma_crossover.py", "MovingAverageCrossover").start()
    assert started == []
