"""Certo runs inside the OS sandbox (owner, 2026-11-19; docs/CERTO.md).

The rules are checked on any OS; what they do is checked inside the real
macOS sandbox with a probe that tries the forbidden things: read a planted
secret in a hidden folder (the repo stands for it), write outside the
workspace, reach the internet; and the allowed ones: write in the
workspace, reach the model server's port.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from firm.sandbox.certo import CertoSandbox, CertoSandboxError, certo_command, seatbelt_profile


def box(tmp_path: Path, port: int = 11434, **kw) -> CertoSandbox:
    fields = {
        "workspace": tmp_path / "home" / "repo" / "data" / "research" / "certo",
        "tmp": tmp_path / "tmp",
        "model_port": port,
        "hidden": (tmp_path / "home" / "repo",),
        "readable": (tmp_path / "home" / "certo",),
    }
    return CertoSandbox(**(fields | kw))


def test_the_profile_opens_only_the_model_port(tmp_path) -> None:
    profile = seatbelt_profile(box(tmp_path), home=tmp_path / "home")
    outbound = [line for line in profile.splitlines() if "network-outbound" in line]
    assert '(allow network-outbound (remote ip "*:11434"))' in outbound
    assert [line for line in outbound if "remote ip" in line] == [
        '(allow network-outbound (remote ip "*:11434"))'
    ]
    assert "(deny network*)" in profile


def test_the_profile_writes_only_the_workspace_and_hides_home_and_repo(tmp_path) -> None:
    home = os.path.realpath(tmp_path / "home")
    profile = seatbelt_profile(box(tmp_path), home=tmp_path / "home")
    write = next(
        line for line in profile.splitlines() if line.startswith("(allow file-write* (subpath")
    )
    assert "/research/certo" in write and "/tmp" in write
    assert '/repo")' not in write  # the repo itself is not writable
    hidden = next(line for line in profile.splitlines() if line.startswith("(deny file-read-data"))
    assert f'"{home}"' in hidden and f'"{home}/repo"' in hidden
    # the folders above the workspace: names only (literal, not subpath)
    assert f'(literal "{home}/repo/data")' in profile
    assert f'(subpath "{home}/repo/data")' not in profile


def test_outside_macos_certo_is_not_started(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(CertoSandboxError, match="refusing to start Certo unsandboxed"):
        certo_command(box(tmp_path), ["scripts/certo-mcp"])


PROBE = textwrap.dedent(
    """
    import json, os, socket, sys
    out = {}
    def attempt(name, fn):
        try:
            fn(); out[name] = "allowed"
        except OSError as e:
            out[name] = f"blocked: {e.errno}"
    secret, outside, inside, port = sys.argv[1:5]
    attempt("read_secret", lambda: open(secret).read())
    attempt("write_outside", lambda: open(outside, "w").write("x"))
    attempt("write_workspace", lambda: open(inside, "w").write("x"))
    def connect(host, p):
        s = socket.socket(); s.settimeout(2)
        try:
            s.connect((host, int(p)))
        finally:
            s.close()
    attempt("internet", lambda: connect("1.1.1.1", 443))
    try:
        connect("127.0.0.1", port); out["model_port"] = "connected"
    except ConnectionRefusedError:
        out["model_port"] = "reachable (refused: nothing listening)"
    except OSError as e:
        out["model_port"] = f"blocked: {e.errno}"
    print(json.dumps(out))
    """
)


@pytest.mark.skipif(sys.platform != "darwin", reason="Certo's sandbox is macOS only for now")
def test_inside_the_sandbox(tmp_path) -> None:
    from firm.sandbox.policy import detect

    if detect() is None:
        if os.environ.get("FIRM_REQUIRE_SANDBOX"):
            pytest.fail("FIRM_REQUIRE_SANDBOX is set but no OS sandbox works here")
        pytest.skip("UNVERIFIED: no OS sandbox here (sandbox-exec)")
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    (repo / ".env").write_text("KEY=planted\n")
    with socket.socket() as s:  # a free port standing in for the model server's
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    b = box(tmp_path, port=port, hidden=(repo,), readable=(Path(sys.prefix), Path(sys.base_prefix)))
    b.workspace.mkdir(parents=True)
    probe = b.workspace / "probe.py"
    probe.write_text(PROBE)
    argv = certo_command(
        b,
        [
            sys.executable,
            "-I",
            str(probe),
            str(repo / ".env"),
            str(repo / "escaped.txt"),
            str(b.workspace / "ok.txt"),
            str(port),
        ],
    )
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["read_secret"].startswith("blocked")
    assert out["write_outside"].startswith("blocked") and not (repo / "escaped.txt").exists()
    assert out["write_workspace"] == "allowed"
    assert out["internet"].startswith("blocked")
    assert out["model_port"].startswith("reachable")


def test_a_project_gets_an_environment_without_downloading_anything(tmp_path) -> None:
    from firm.research.certo_projects import prepare_project

    folder = prepare_project(tmp_path, "d-001")
    py = folder / ".venv" / "bin" / "python"
    r = subprocess.run(
        [str(py), "-c", "import pandas, numpy, pytest; print('ok')"], capture_output=True, text=True
    )
    assert r.stdout.strip() == "ok", r.stderr
    assert (folder / "pytest.ini").read_text().startswith("[pytest]")
    assert prepare_project(tmp_path, "d-001") == folder  # again: kept as it is
    with pytest.raises(ValueError):
        prepare_project(tmp_path, "../escape")


def test_certo_settings_and_a_missing_model_address(tmp_path, monkeypatch) -> None:
    from firm.control.research import certo_server
    from firm.models.certo import certo_settings
    from verify_helpers import REPO

    settings = tmp_path / "certo-mcp.env"
    settings.write_text("# comment\nCERTO_LLM_BASE_URL=http://dgx.example:11434  # the DGX\n")
    monkeypatch.setenv("FIRM_CERTO_SETTINGS", str(settings))
    monkeypatch.delenv("CERTO_LLM_BASE_URL", raising=False)
    assert certo_settings(REPO)["CERTO_LLM_BASE_URL"] == "http://dgx.example:11434"
    settings.write_text("")
    with pytest.raises(ValueError, match="CERTO_LLM_BASE_URL"):
        certo_server(
            config_root=REPO / "config", data_root=tmp_path / "data", state_root=tmp_path / "state"
        )
