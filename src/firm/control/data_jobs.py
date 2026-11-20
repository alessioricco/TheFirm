"""Downloads started from the dashboard, run in the background one at a time.

A download takes seconds to minutes (a long backfill pages through a
provider politely), so the control API starts it as a job in a thread and
the page polls the job. Only one job runs at a time: two would hit the same
public endpoints at once and race on the same series. Jobs run the same
code as `firm data update` / `fetch` (firm.data.update) and keep their
output lines; the last few jobs stay in memory for the page (nothing here
is a record: the captures and dataset versions are, and the control API
logs each start as an OWNER_COMMAND).
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

KEEP = 20  # finished jobs kept for the page
MAX_LINES = 2000


class JobBusy(RuntimeError):
    """Another download is still running."""


@dataclass
class Job:
    job_id: str
    kind: Literal["update", "fetch"]
    params: dict[str, Any]
    status: Literal["running", "done", "failed"] = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    lines: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""

    def log(self, text: str, level: str = "info") -> None:
        for line in text.splitlines() or [""]:
            if len(self.lines) < MAX_LINES:
                self.lines.append({"level": level, "text": line})

    def view(self, *, with_lines: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "job_id": self.job_id,
            "kind": self.kind,
            "params": self.params,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summary": self.summary,
        }
        if with_lines:
            out["lines"] = list(self.lines)
        return out


class DataJobs:
    """At most one running job; the last KEEP jobs kept for the page."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.status == "running"), None)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[Job]:
        with self._lock:
            return list(reversed(self._jobs.values()))

    def start(
        self,
        kind: Literal["update", "fetch"],
        params: dict[str, Any],
        work: Callable[[Job], str],
        *,
        background: bool = True,
    ) -> Job:
        """Run `work(job)` (returns the summary; raising marks the job failed)."""
        with self._lock:
            if any(j.status == "running" for j in self._jobs.values()):
                raise JobBusy("a download is already running; wait for it to finish")
            job = Job(job_id="j-" + uuid.uuid4().hex[:10], kind=kind, params=params)
            self._jobs[job.job_id] = job
            while len(self._jobs) > KEEP:
                self._jobs.popitem(last=False)

        def run() -> None:
            try:
                job.summary = work(job)
                job.status = "done"
            except Exception as exc:  # a job must always end: report whatever stopped it
                job.log(f"error: {exc}", "error")
                job.summary = f"stopped: {exc}"
                job.status = "failed"
            finally:
                job.finished_at = datetime.now(UTC)

        if background:
            threading.Thread(target=run, name=f"data-{job.job_id}", daemon=True).start()
        else:
            run()
        return job
