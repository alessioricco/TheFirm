"""The dashboard's Data view: what the store holds, and downloads (control API).

- `GET /api/data`: every bar series (reach, age, what an update would do),
  the providers, and recent download jobs.
- `POST /api/data/update`: bring the downloaded series up to date (all, or
  those matching provider / symbol / timeframe), as `firm data update`.
- `POST /api/data/fetch`: download a new series, as `firm data fetch`.
- `GET /api/data/jobs/{id}`: a job's status and output lines.

Downloads use the network (public market-data endpoints, no keys) and run
in the background, one at a time (firm.control.data_jobs). Each start is an
OWNER_COMMAND "via dashboard". Nothing here touches orders, risk or the
verifier: it only adds captures and dataset versions to the store.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from firm.control.data_jobs import DataJobs, Job, JobBusy

SYMBOL = r"^\^?[A-Za-z0-9][A-Za-z0-9._=-]{0,39}$"  # BTC-USD, BTCUSDT, ^GSPC, GC=F
INSTRUMENT = r"^[A-Z0-9][A-Z0-9.-]{0,39}$"  # BTC-USD, NVDA.XNAS
DAY = r"^\d{4}-\d{2}-\d{2}$"
NAME = r"^[a-z][a-z0-9_]{0,30}$"


class UpdateBody(BaseModel):
    provider: str | None = Field(default=None, pattern=NAME)
    symbol: str | None = Field(default=None, pattern=SYMBOL)
    tf: str | None = Field(default=None, pattern=r"^[0-9]{1,2}[mhdw]$")


class FetchBody(BaseModel):
    provider: str = Field(pattern=NAME)
    symbol: str = Field(pattern=SYMBOL)
    instrument: str = Field(pattern=INSTRUMENT)
    tf: str = Field(default="1d", pattern=r"^[0-9]{1,2}[mhdw]$")
    venue: str | None = Field(default=None, pattern=NAME)
    asset_class: Literal["crypto", "equity"] = "crypto"
    quote: str | None = Field(default=None, pattern=r"^[A-Z]{3,5}$")
    since: str | None = Field(default=None, pattern=DAY)
    until: str | None = Field(default=None, pattern=DAY)
    listed_at: str | None = Field(default=None, pattern=DAY)


def _day(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(400, f"not a date: {s}") from exc


def add_data_routes(
    app: FastAPI,
    *,
    guarded: list[Any],
    change: list[Any],
    config_root: Path,
    data_root: Path,
    record: Callable[[str, dict[str, Any]], None],
    fetcher_factory: Callable[[Path], Any] | None = None,
    background: bool = True,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DataJobs:
    """`record(command, payload)` logs the owner's command in the ledger."""
    from firm.core.config import load_data_quality
    from firm.data import providers
    from firm.data.fetch import Fetcher

    jobs = DataJobs()
    make_fetcher = fetcher_factory or (lambda root: Fetcher(root))

    def quality():  # type: ignore[no-untyped-def]
        return load_data_quality(config_root)

    def provider_info() -> list[dict[str, Any]]:
        out = []
        for name in providers.available():
            p = providers.get(name)
            out.append(
                {
                    "name": name,
                    "description": p.description,
                    "timeframes": sorted(p.timeframes, key=_tf_order),
                    "history_note": p.history_note,
                }
            )
        return out

    def start(kind: Literal["update", "fetch"], params: dict[str, Any], work) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        try:
            job = jobs.start(kind, params, work, background=background)
        except JobBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        record(f"data {kind}", {"job_id": job.job_id} | params)
        return job.view()

    @app.get("/api/data", dependencies=guarded)
    def data() -> dict[str, Any]:
        from firm.data.update import series_view, stored_series

        t = now()
        running = jobs.running()
        return {
            "series": [series_view(s, t) for s in stored_series(data_root)],
            "providers": provider_info(),
            "jobs": [j.view(with_lines=False) for j in jobs.recent()],
            "running": running.job_id if running else None,
        }

    @app.post("/api/data/update", dependencies=change)
    def update(body: UpdateBody) -> dict[str, Any]:
        from firm.data.update import run_update, stored_series

        todo = stored_series(data_root, provider=body.provider, symbol=body.symbol, tf=body.tf)
        if not todo:
            raise HTTPException(404, "no matching bar series in the store")

        def work(job: Job) -> str:
            t = now()
            fetcher = make_fetcher(data_root)
            fetcher.log = lambda m: job.log(m, "detail")
            failed = run_update(
                data_root,
                fetcher,
                todo,
                t,
                quality(),
                out=job.log,
                err=lambda m: job.log(m, "error"),
            )
            if failed:
                raise RuntimeError(f"{failed} of {len(todo)} series failed; it is safe to retry")
            return f"{len(todo)} series checked"

        params = {k: v for k, v in body.model_dump().items() if v is not None}
        return start("update", params, work)

    @app.post("/api/data/fetch", dependencies=change)
    def fetch(body: FetchBody) -> dict[str, Any]:
        from firm.data.update import build_summary, fetch_series, stored_series

        try:
            source = providers.get(body.provider)
            source.supports("bars", body.tf)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.asset_class == "equity" and not body.quote:
            raise HTTPException(400, "shares need their quote currency (e.g. USD)")
        start_day, end_day = _day(body.since), _day(body.until)
        listed = _day(body.listed_at)

        def work(job: Job) -> str:
            fetcher = make_fetcher(data_root)
            fetcher.log = lambda m: job.log(m, "detail")
            n = fetch_series(
                fetcher,
                provider=body.provider,
                symbol=body.symbol,
                instrument=body.instrument,
                tf=body.tf,
                start=start_day,
                end=end_day or now(),
                venue=body.venue,
                asset_class=body.asset_class,
                quote=body.quote,
                listed_at=listed,
            )
            key = f"{body.provider}:{body.symbol}:{body.tf}"
            job.log(f"{key}: {n} request(s) stored")
            (series,) = stored_series(
                data_root, provider=body.provider, symbol=body.symbol, tf=body.tf
            )
            built = build_summary(data_root, series, quality())
            job.log(f"dataset {built}")
            return f"{key}: dataset {built.split()[0]}"

        params = {k: v for k, v in body.model_dump().items() if v is not None}
        return start("fetch", params, work)

    @app.get("/api/data/jobs/{job_id}", dependencies=guarded)
    def job(job_id: str) -> dict[str, Any]:
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(404, f"no job {job_id} (jobs are kept until the server restarts)")
        return j.view()

    return jobs


def _tf_order(tf: str) -> int:
    from firm.core.timeframes import tf_seconds

    return tf_seconds(tf)
