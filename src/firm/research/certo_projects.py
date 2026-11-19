"""A dossier's project folder in Certo's workspace, ready before Certo's agent starts.

Certo runs a Python project in the project's own environment, and uses an
existing `.venv` as it is (Certo's docs, "Make a project checkable"). The
firm makes that `.venv` itself, linked to the packages the firm already has
(pandas, numpy, pytest, ...), so Certo never needs to download anything:
its sandbox has no internet. A `pytest.ini` marks the project as pytest's
root, so pytest doesn't climb into the repository (which the sandbox
hides) and waste the agent's steps.
"""

from __future__ import annotations

import re
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PYTEST_INI = "[pytest]\n# the project is pytest's root (the firm prepared this folder)\n"


def firm_python() -> str:
    """The base interpreter of the firm's environment (the venvs share its version)."""
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sys.base_prefix) / "bin" / version,
        Path(sys.base_prefix) / "bin" / "python3",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def firm_site_packages() -> list[str]:
    paths = sysconfig.get_paths()
    return sorted({paths["purelib"], paths["platlib"]})


def prepare_project(
    workspace: Path,
    project: str,
    *,
    python: str | None = None,
    site_packages: Sequence[str] | None = None,
) -> Path:
    """Make (or keep) `<workspace>/<project>` with its `.venv` and `pytest.ini`."""
    if not _NAME.match(project):
        raise ValueError(f"invalid project name {project!r}")
    folder = Path(workspace) / project
    folder.mkdir(parents=True, exist_ok=True)
    venv = folder / ".venv"
    if not (venv / "bin" / "python").exists():
        subprocess.run(
            [python or firm_python(), "-m", "venv", "--without-pip", str(venv)],
            check=True,
            capture_output=True,
        )
    own = subprocess.run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "import sysconfig;print(sysconfig.get_paths()['purelib'])",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    pth = Path(own) / "firm_packages.pth"
    wanted = "\n".join(site_packages or firm_site_packages()) + "\n"
    if not pth.exists() or pth.read_text() != wanted:
        pth.write_text(wanted)
    ini = folder / "pytest.ini"
    if not ini.exists():
        ini.write_text(PYTEST_INI)
    return folder
