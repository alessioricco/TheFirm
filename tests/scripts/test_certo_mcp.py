"""`scripts/certo-mcp`: Certo's MCP server for the firm (docs/CERTO.md).

A stub stands in for Certo, so no network and no model: it records the
arguments and environment the launcher starts it with.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "scripts" / "certo-mcp"


def stub(tmp_path: Path) -> Path:
    path = tmp_path / "certo-stub"
    path.write_text(
        "#!/bin/sh\n"
        'python3 -c \'import json, os, sys; print(json.dumps({"args": sys.argv[1:], '
        '"env": {k: v for k, v in os.environ.items() if k.startswith("CERTO_")}}))\' "$@"\n'
    )
    path.chmod(0o755)
    return path


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(
        [str(LAUNCHER)], env=base | env, capture_output=True, text=True, timeout=30
    )


def test_it_is_an_executable_posix_script() -> None:
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR
    assert LAUNCHER.read_text().startswith("#!/bin/sh\n")
    assert subprocess.run(["sh", "-n", str(LAUNCHER)]).returncode == 0


def test_without_the_dgx_address_it_says_so_and_prints_nothing_on_stdout(tmp_path) -> None:
    r = run({"FIRM_CERTO_SETTINGS": str(tmp_path / "missing.env")})
    assert r.returncode != 0
    assert r.stdout == ""  # stdout is the MCP stream: never anything else
    assert "CERTO_LLM_BASE_URL" in r.stderr


def test_it_starts_certo_mcp_on_the_firms_workspace_with_its_settings(tmp_path) -> None:
    settings = tmp_path / "certo-mcp.env"
    workspace = tmp_path / "data" / "research" / "certo"
    settings.write_text(
        f"CERTO_LLM_BASE_URL=http://dgx.example:11434\nCERTO_BIN={stub(tmp_path)}\n"
    )
    r = run({"FIRM_CERTO_SETTINGS": str(settings), "FIRM_DATA_ROOT": str(tmp_path / "data")})
    assert r.returncode == 0, r.stderr
    seen = json.loads(r.stdout)
    assert seen["args"] == ["mcp", "--root", str(workspace)]
    env = seen["env"]
    assert env["CERTO_LLM_BASE_URL"] == "http://dgx.example:11434"
    assert env["CERTO_LLM_MODEL"] == "ollama/qwen3.8:27b"  # the research role's model
    assert env["CERTO_PROJECTS_DIR"] == str(workspace)
    assert (env["CERTO_LLM_TIMEOUT_SECONDS"], env["CERTO_MAX_TASK_SECONDS"]) == ("1200", "3600")
    assert workspace.is_dir()


def test_the_research_model_default_matches_the_config() -> None:
    import yaml

    models = yaml.safe_load((REPO / "config" / "models.yaml").read_text())
    research = models["roles"]["research"]
    assert f"CERTO_LLM_MODEL:={research['provider']}/{research['model']}" in LAUNCHER.read_text()
