"""The control API behind the dashboard (M3 plan, M3a): FastAPI, REST + one WebSocket.

Loopback only for now, and only for the owner:

- **Token**: a random token in `<state>/dashboard/token` (mode 600), created
  on first use. `GET /login?token=...` (the URL `firm dashboard url` prints)
  sets it as an HttpOnly, SameSite=Strict cookie and redirects to the page;
  scripts can send `Authorization: Bearer <token>` instead. Nothing else
  answers without it, and no response contains it.
- **Cross-site requests**: changes need a JSON body (415 otherwise), which a
  form on another site can't send without a CORS preflight this API never
  answers; the cookie is SameSite=Strict; the WebSocket checks the cookie.
- **What it can do**: read the research floor and steer it (start, pause,
  resume, redirect, close, floor brief: the same as `firm research`, each an
  OWNER_COMMAND "via dashboard"); read paper status and the ledger; push new
  ledger events live. **Read-only for trading**: nothing here can place,
  size or cancel an order or touch risk; the kill switch comes separately
  (M3b), through the bus and the risk desk.
- **Data** (firm.control.data_api): list the store's series, update them or
  download a new one in the background, as `firm data update|fetch`; each
  start is an OWNER_COMMAND "via dashboard". Uses the network (public
  market-data endpoints only).
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from firm.control.data_api import add_data_routes
from firm.control.research import dossier_view, floor_view, open_floor, owner_command
from firm.control.status import paper_status
from firm.ledger.sink import ledger_path
from firm.ledger.store import Ledger
from firm.research.dossier import DossierError

COOKIE = "firm_dashboard"
WEB = Path(__file__).with_name("web")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
VIEWS = ("research", "trading")  # the named views whose panel layouts are saved
CONTENT_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
# No inline or third-party scripts; styles may be set inline (the chart and
# panel libraries size elements that way). Nothing may frame the page.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def token_path(state_root: Path) -> Path:
    return Path(state_root) / "dashboard" / "token"


def load_or_create_token(state_root: Path) -> str:
    path = token_path(state_root)
    if path.is_file():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    return value


class StartBody(BaseModel):
    brief: str
    market: str
    instruments: list[str] = Field(min_length=1)
    timeframes: list[str] = Field(min_length=1)
    dossier_id: str | None = None


class ReasonBody(BaseModel):
    reason: str | None = None


class BriefBody(BaseModel):
    brief: str


class FloorBriefBody(BaseModel):
    text: str


class KillBody(BaseModel):
    action: Literal["kill", "resume"]
    market: str | None = None  # None: every market
    reason: str = Field(min_length=1, max_length=300)
    confirm: str  # must repeat the market's name, or ALL


class LayoutItem(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,40}$")
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=200)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=40)


class LayoutBody(BaseModel):
    items: list[LayoutItem] = Field(max_length=40)


def create_app(
    *,
    config_root: Path,
    data_root: Path,
    state_root: Path,
    poll_seconds: float = 1.0,
    publish_factory=None,  # type: ignore[no-untyped-def]
    kill_wait_seconds: float = 8.0,
    data_fetcher=None,  # type: ignore[no-untyped-def]
    data_background: bool = True,
) -> FastAPI:
    """`publish_factory(config_root, state_root)`: how owner commands reach the risk
    desk (default: the bus, as the control service). `data_fetcher(root)`: the
    downloader for the Data view (default: the real network one)."""
    expected = load_or_create_token(state_root)
    app = FastAPI(title="The firm", docs_url=None, redoc_url=None, openapi_url=None)

    def _ok(value: str | None) -> bool:
        return value is not None and hmac.compare_digest(value, expected)

    def owner(request: Request) -> None:
        bearer = request.headers.get("authorization", "")
        sent = bearer[7:] if bearer.lower().startswith("bearer ") else request.cookies.get(COOKIE)
        if not _ok(sent):
            raise HTTPException(401, "log in with the URL `firm dashboard url` prints")

    async def json_body(request: Request) -> None:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "changes need a JSON body")

    def floor():  # type: ignore[no-untyped-def]
        return open_floor(config_root, data_root, state_root)

    def steer(fn):  # type: ignore[no-untyped-def]
        """Run a floor change; map the floor's refusals to HTTP errors."""
        try:
            return fn()
        except DossierError as exc:
            msg = str(exc)
            raise HTTPException(404 if msg.startswith("no dossier") else 409, msg) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    guarded = [Depends(owner)]
    change = [Depends(owner), Depends(json_body)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    # ------------------------------------------------------------- page, login
    @app.get("/login")
    def login(token: str = "") -> RedirectResponse:
        if not _ok(token):
            raise HTTPException(401, "wrong or missing token")
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return r

    @app.get("/", dependencies=guarded)
    def page() -> FileResponse:
        return FileResponse(WEB / "index.html", media_type="text/html")

    @app.get("/static/{path:path}", dependencies=guarded)
    def static(path: str) -> FileResponse:
        target = (WEB / path).resolve()
        kind = CONTENT_TYPES.get(target.suffix)
        if not target.is_relative_to(WEB.resolve()) or not target.is_file() or kind is None:
            raise HTTPException(404, "not found")
        return FileResponse(target, media_type=kind)

    # ------------------------------------------------------- page support
    @app.get("/api/meta", dependencies=guarded)
    def meta() -> dict[str, Any]:
        from firm import __version__
        from firm.core.timeframes import TIMEFRAME_SECONDS

        markets = sorted(p.stem for p in (config_root / "markets").glob("*.yaml"))
        manager, _ = floor()
        return {
            "version": __version__,
            "markets": markets,
            "timeframes": sorted(TIMEFRAME_SECONDS, key=TIMEFRAME_SECONDS.__getitem__),
            "researchers": manager.config.researchers,
            "views": list(VIEWS),
        }

    @app.get("/api/commands", dependencies=guarded)
    def commands() -> list[dict[str, Any]]:
        from firm.control.cli import build_parser

        return command_catalog(build_parser())

    @app.get("/api/layouts/{view}", dependencies=guarded)
    def get_layout(view: str) -> dict[str, Any]:
        path = _layout_path(view)
        if not path.is_file():
            return {"items": None}
        return LayoutBody.model_validate_json(path.read_text()).model_dump()

    @app.put("/api/layouts/{view}", dependencies=change)
    def put_layout(view: str, body: LayoutBody) -> dict[str, Any]:
        path = _layout_path(view)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.model_dump_json(indent=1))
        return body.model_dump()

    def _layout_path(view: str) -> Path:
        if view not in VIEWS:
            raise HTTPException(404, f"no view {view!r}; views: {', '.join(VIEWS)}")
        return Path(state_root) / "dashboard" / "layouts" / f"{view}.json"

    # --------------------------------------------------------- research floor
    @app.get("/api/research", dependencies=guarded)
    def research() -> dict[str, Any]:
        manager, _ = floor()
        return floor_view(manager)

    @app.post("/api/research", dependencies=change)
    def start(body: StartBody) -> dict[str, Any]:
        manager, ledger = floor()
        d = steer(
            lambda: manager.start(
                brief=body.brief,
                market=body.market,
                instruments=body.instruments,
                timeframes=body.timeframes,
                dossier_id=body.dossier_id,
            )
        )
        owner_command(ledger, "start", {"dossier_id": d.dossier_id}, via="dashboard")
        return dossier_view(manager, d)

    @app.put("/api/research/brief", dependencies=change)
    def floor_brief(body: FloorBriefBody) -> dict[str, Any]:
        manager, ledger = floor()
        manager.floor_briefs.set(body.text)
        owner_command(ledger, "brief", {"cleared": not body.text.strip()}, via="dashboard")
        current = manager.floor_briefs.current()
        return {"floor_brief": current.text if current else None}

    @app.get("/api/research/{dossier_id}", dependencies=guarded)
    def dossier(dossier_id: str) -> dict[str, Any]:
        manager, _ = floor()
        return dossier_view(manager, steer(lambda: manager.store.get(dossier_id)))

    @app.post("/api/research/{dossier_id}/pause", dependencies=change)
    def pause(dossier_id: str, body: ReasonBody) -> dict[str, Any]:
        manager, ledger = floor()
        steer(lambda: manager.store.pause(dossier_id, body.reason or "owner"))
        owner_command(
            ledger, "pause", {"dossier_id": dossier_id, "reason": body.reason}, via="dashboard"
        )
        return dossier_view(manager, manager.store.get(dossier_id))

    @app.post("/api/research/{dossier_id}/resume", dependencies=change)
    def resume(dossier_id: str, body: ReasonBody) -> dict[str, Any]:
        manager, ledger = floor()
        steer(lambda: manager.store.resume(dossier_id))
        owner_command(ledger, "resume", {"dossier_id": dossier_id}, via="dashboard")
        return dossier_view(manager, manager.store.get(dossier_id))

    @app.post("/api/research/{dossier_id}/redirect", dependencies=change)
    def redirect(dossier_id: str, body: BriefBody) -> dict[str, Any]:
        manager, ledger = floor()
        new = steer(lambda: manager.redirect(dossier_id, body.brief))
        owner_command(
            ledger,
            "redirect",
            {"dossier_id": dossier_id, "new_dossier_id": new.dossier_id},
            via="dashboard",
        )
        return dossier_view(manager, new)

    @app.post("/api/research/{dossier_id}/close", dependencies=change)
    def close(dossier_id: str, body: ReasonBody) -> dict[str, Any]:
        if not (body.reason or "").strip():
            raise HTTPException(400, "a reason is required")
        manager, ledger = floor()
        steer(lambda: manager.store.close(dossier_id, f"owner: {body.reason}"))
        owner_command(
            ledger, "close", {"dossier_id": dossier_id, "reason": body.reason}, via="dashboard"
        )
        return dossier_view(manager, manager.store.get(dossier_id))

    # ------------------------------------------------------------ paper, ledger
    def _ledger() -> Ledger:
        return Ledger(ledger_path(state_root))

    @app.get("/api/paper", dependencies=guarded)
    def paper() -> dict[str, Any]:
        return paper_status(_ledger(), data_root)

    # ------------------------------------------------------------ kill switch
    @app.get("/api/killswitch", dependencies=guarded)
    def killswitch() -> dict[str, Any]:
        from firm.control.killswitch import kill_state

        markets = sorted(p.stem for p in (config_root / "markets").glob("*.yaml"))
        return {"state": kill_state(_ledger()), "markets": markets}

    @app.post("/api/killswitch", dependencies=change)
    async def killswitch_command(body: KillBody) -> dict[str, Any]:
        """The owner's kill switch (or its release), as `firm kill` / `firm resume`.
        Recorded in the ledger, sent to the risk desk over the bus, and confirmed
        when the desk records it. `confirm` must repeat the market (or ALL)."""
        from firm.control.killswitch import (
            bus_publisher,
            issue,
            kill_state,
            new_command,
            wait_applied,
        )

        expected = body.market or "ALL"
        if body.confirm.strip() != expected:
            raise HTTPException(400, f"type {expected} to confirm")
        led = _ledger()
        current = kill_state(led)
        on = current["global"] if body.market is None else current["markets"].get(body.market)
        if body.action == "resume" and on is None:
            raise HTTPException(409, f"no kill switch is on for {body.market or 'all markets'}")
        try:
            cmd = new_command(body.action, market=body.market, reason=body.reason)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        factory = publish_factory or bus_publisher
        sent = await issue(led, cmd, publish=factory(config_root, state_root), via="dashboard")
        confirmed = await wait_applied(led, cmd, kill_wait_seconds) if sent else False
        return {
            "command_id": cmd.command_id,
            "sent": sent,
            "confirmed": confirmed,
            "state": kill_state(led),
        }

    @app.get("/api/paper/chart", dependencies=guarded)
    def paper_chart(market: str, venue: str, bars: int = 500) -> dict[str, Any]:
        return run_chart(_ledger(), state_root, data_root, market, venue, min(bars, 5000))

    @app.get("/api/ledger", dependencies=guarded)
    def ledger_events(
        after: int | None = None, before: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Events after a sequence (oldest first), or the `limit` events before one
        (default: the newest), oldest first either way."""
        limit = max(1, min(limit, 1000))
        led = _ledger()
        if after is not None:
            out = []
            for e in led.read(from_sequence=after + 1):
                out.append(_event(e))
                if len(out) >= limit:
                    break
            return out
        head = led.head()
        end = before if before is not None else (head.sequence + 1 if head else 0)
        start = max(0, end - limit)
        return [_event(e) for e in led.read(from_sequence=start) if e.sequence < end]

    # ------------------------------------------------------------ data store
    def record_data(command: str, payload: dict[str, Any]) -> None:
        from datetime import UTC, datetime

        from firm.core.domain import LedgerEventType

        _ledger().append(
            LedgerEventType.OWNER_COMMAND,
            f"owner: {command} (dashboard)"[:300],
            {"command": command, "via": "dashboard", "at": datetime.now(UTC).isoformat()} | payload,
        )

    add_data_routes(
        app,
        guarded=guarded,
        change=change,
        config_root=config_root,
        data_root=data_root,
        record=record_data,
        fetcher_factory=data_fetcher,
        background=data_background,
    )

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        if not _ok(ws.cookies.get(COOKIE)):
            await ws.close(code=4401)
            return
        await ws.accept()
        led = _ledger()
        head = led.head()
        last = head.sequence if head else -1
        try:
            while True:
                for e in led.read(from_sequence=last + 1):
                    await ws.send_json(_event(e))
                    last = e.sequence
                await asyncio.sleep(poll_seconds)
        except WebSocketDisconnect:
            return

    return app


def _event(e) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "sequence": e.sequence,
        "type": e.event_type.value,
        "reason": e.reason,
        "recorded_at": e.recorded_at.isoformat(),
        "payload": e.payload,
    }


def command_catalog(parser) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    """Every runnable `firm` command with its help and options, from the parser
    itself, so the dashboard's command reference can't drift from the CLI."""
    import argparse

    out: list[dict[str, Any]] = []

    def walk(p: argparse.ArgumentParser, prefix: str, help_text: str) -> None:
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if subs:
            helps = {c.dest: c.help for c in subs[0]._choices_actions}
            for name, sub in subs[0].choices.items():
                walk(sub, f"{prefix} {name}", helps.get(name) or "")
            return
        options = []
        for a in p._actions:
            if a.option_strings and "--help" in a.option_strings:
                continue
            options.append(
                {
                    "flags": a.option_strings or [a.dest.upper()],
                    "help": a.help or "",
                    "required": bool(a.required) or not a.option_strings,
                    "default": None
                    if a.default in (None, argparse.SUPPRESS, False)
                    else str(a.default),
                    "takes_value": a.nargs != 0,
                }
            )
        out.append({"command": prefix, "help": help_text, "options": options})

    walk(parser, "firm", "")
    return [c for c in out if c["command"] != "firm"]


def run_chart(
    ledger: Ledger, state_root: Path, data_root: Path, market: str, venue: str, bars: int
) -> dict[str, Any]:
    """Price bars for a paper run (from the dataset its verdict cites) and its fills.

    The bars end where that dataset ends: the dashboard reads the store, never
    the venue. Fills come from the ledger."""
    import json as _json
    from datetime import UTC, datetime

    from firm.core.domain import LedgerEventType
    from firm.data.dataset import open_dataset

    status = paper_status(ledger, data_root)
    run = next((r for r in status["runs"] if (r["market"], r["venue"]) == (market, venue)), None)
    if run is None or not run.get("verdict_id"):
        raise HTTPException(404, f"no paper run for {market} on {venue} in this ledger")
    report_path = Path(state_root) / "reports" / f"{run['verdict_id']}.json"
    if not report_path.is_file():
        raise HTTPException(404, "the run's verifier report isn't in this state folder")
    data = _json.loads(report_path.read_text())["data"]
    instrument, tf = data["instruments"][0], data["timeframe"]
    try:
        frame = open_dataset(data_root, data["dataset_version"]).bars(
            data["venue"], instrument, tf, as_of=datetime.now(UTC)
        )
    except (OSError, LookupError, ValueError) as exc:
        raise HTTPException(404, f"the run's dataset isn't readable here: {exc}") from exc
    frame = frame.tail(bars)
    candles = [
        {
            "time": int(r.open_time.timestamp()),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
        }
        for r in frame.itertuples()
    ]
    fills = [
        {
            "time": e.payload["filled_at"],
            "side": e.payload["side"],
            "price": e.payload["price"],
            "quantity": e.payload["quantity"],
        }
        for e in ledger.read()
        if e.event_type is LedgerEventType.FILL
        and e.payload.get("venue") == venue
        and e.payload.get("instrument") == instrument
    ]
    return {
        "market": market,
        "venue": venue,
        "instrument": instrument,
        "timeframe": tf,
        "dataset_version": data["dataset_version"],
        "verdict_id": run["verdict_id"],
        "candles": candles,
        "fills": fills,
    }
