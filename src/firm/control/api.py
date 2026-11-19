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
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from firm.control.research import dossier_view, floor_view, open_floor, owner_command
from firm.control.status import paper_status
from firm.ledger.sink import ledger_path
from firm.ledger.store import Ledger
from firm.research.dossier import DossierError

COOKIE = "firm_dashboard"
WEB = Path(__file__).with_name("web")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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


def create_app(
    *,
    config_root: Path,
    data_root: Path,
    state_root: Path,
    poll_seconds: float = 1.0,
) -> FastAPI:
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

    @app.get("/api/ledger", dependencies=guarded)
    def ledger_events(after: int = -1, limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for e in _ledger().read(from_sequence=after + 1):
            out.append(_event(e))
            if len(out) >= min(limit, 1000):
                break
        return out

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
