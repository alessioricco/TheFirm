"""ResearchSession.code: one Certo task in the dossier's prepared project; the
strategy file comes back only with a passing, verified run."""

from __future__ import annotations

import asyncio

from firm.control.research import ResearchSession


class StubServer:
    def __init__(self, workspace, status, write=True):
        self.workspace, self.status, self.write, self.calls = workspace, status, write, []

    async def run_task(self, project, task, *, dossier_id, max_minutes):
        self.calls.append((project, dossier_id, max_minutes))
        if self.write:
            (self.workspace / project / "strategy.py").write_text("class S:\n    pass\n")
        return "run-1"

    async def wait(self, run_id, *, timeout):
        return self.status


def session(tmp_path, status, write=True):
    s = ResearchSession(
        config_root=tmp_path, data_root=tmp_path, state_root=tmp_path, certo_minutes=5
    )
    s._certo = StubServer(tmp_path / "ws", status, write)
    return s


def test_a_verified_run_returns_the_strategy(tmp_path) -> None:
    s = session(
        tmp_path, {"state": "finished", "verdict": "verified", "passed": True, "result": {}}
    )
    r = asyncio.run(s.code("d-001", "write it"))
    assert r.passed and r.code == b"class S:\n    pass\n" and r.verdict == "verified"
    assert s._certo.calls == [("d-001", "d-001", 5)]
    assert (tmp_path / "ws" / "d-001" / ".venv").is_dir()  # prepared before the task


def test_a_failed_run_is_not_passed(tmp_path) -> None:
    s = session(
        tmp_path,
        {
            "state": "finished",
            "verdict": "failed",
            "passed": False,
            "result": {"error": "pytest failed"},
        },
        write=False,
    )
    r = asyncio.run(s.code("d-002", "write it"))
    assert not r.passed and r.code is None and "pytest failed" in r.note
