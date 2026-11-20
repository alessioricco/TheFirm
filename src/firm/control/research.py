"""Wiring the research floor to the verification desk and the ledger (M3, M2).

`firm.research` may not import the verifier (golden rule 12), so the
composition root gives the research manager two callables:

- `backtest`: a research backtest through `verify.research.ResearchBacktester`
  (research view only, the trial counted under the dossier's ID, agent code
  sandboxed);
- `verify`: a submission through `verify.runner.verify_strategy_file`, its
  report written to `<state>/reports/<verdict>.json` like `firm verify`, so
  the deployment gate can find it.

and `model_usage`, a dossier's tokens and cloud spend from the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from firm.core.events import NULL_SINK, EventSink
from firm.ledger.spend import dossier_model_usage
from firm.ledger.store import Ledger
from firm.research.manager import Backtest, ModelUsage, Verify
from firm.verify.research import ResearchBacktester
from firm.verify.runner import verify_strategy_file


def verification_desk(
    *,
    data_root: Path,
    config_root: Path,
    state: Path,
    now: Callable[[], datetime],
    jurisdiction: str = "uk",
    sink: EventSink = NULL_SINK,
) -> tuple[Backtest, Verify]:
    def backtest(
        path: Path,
        class_name: str,
        params: Mapping[str, Any],
        *,
        dossier_id: str,
        market: str,
        dataset_version: str,
        venue: str,
        instrument: str,
        tf: str,
    ) -> Mapping[str, Any]:
        with ResearchBacktester(
            strategy_path=path,
            class_name=class_name,
            experiment_id=dossier_id,
            data_root=data_root,
            dataset_version=dataset_version,
            venue=venue,
            instrument=instrument,
            tf=tf,
            config_root=config_root,
            jurisdiction=jurisdiction,
            market=market,
            state=state,
            now=now(),
            sink=sink,
        ) as bench:
            r = bench.run(params)
        return {"trial_id": r.trial_id, "new_trial": r.new_trial, "metrics": r.metrics}

    def verify(
        path: Path,
        class_name: str,
        params: Mapping[str, Any],
        *,
        market: str,
        dataset_version: str,
        venue: str,
        instrument: str,
        tf: str,
    ) -> tuple[str, str, str]:
        result = verify_strategy_file(
            strategy_path=path,
            class_name=class_name,
            params=params,
            data_root=data_root,
            dataset_version=dataset_version,
            venue=venue,
            instrument=instrument,
            tf=tf,
            config_root=config_root,
            jurisdiction=jurisdiction,
            market=market,
            state=state,
            now=now(),
            sink=sink,
        )
        out = state / "reports" / f"{result.verdict.verdict_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(result.report)
        candidate = json.loads(result.report)["candidate"]["candidate_id"]
        return result.verdict.verdict_id, candidate, result.verdict.outcome.value

    return backtest, verify


def ledger_model_usage(ledger: Ledger) -> ModelUsage:
    return lambda dossier_id: dossier_model_usage(ledger, dossier_id)


def open_floor(config_root: Path, data_root: Path, state_root: Path):  # type: ignore[no-untyped-def]
    """The research manager and the ledger for the owner's commands (CLI, API).
    Nothing here runs a researcher yet (M4): backtest and verify refuse."""
    from firm.core.config import load_research_config
    from firm.ledger.sink import LedgerSink, ledger_path
    from firm.research.dossier import DossierStore, FloorBriefs
    from firm.research.manager import ResearchManager

    def not_yet(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("researchers run with the researcher agent (milestone 3, M4)")

    ledger = Ledger(ledger_path(state_root))
    store = DossierStore(data_root, sink=LedgerSink(ledger))
    manager = ResearchManager(
        load_research_config(config_root),
        store,
        backtest=not_yet,
        verify=not_yet,
        model_usage=ledger_model_usage(ledger),
        floor_briefs=FloorBriefs(data_root),
    )
    return manager, ledger


def owner_command(ledger: Ledger, command: str, payload: dict[str, Any], via: str) -> None:
    """Record an owner command (OWNER_COMMAND), and where it came from (cli, dashboard)."""
    from datetime import UTC
    from datetime import datetime as dt

    from firm.core.domain import LedgerEventType

    ledger.append(
        LedgerEventType.OWNER_COMMAND,
        f"owner: research {command} ({via})"[:300],
        {"command": f"research {command}", "via": via, "at": dt.now(UTC).isoformat()} | payload,
    )


def place(manager, dossier_id: str) -> str:  # type: ignore[no-untyped-def]
    running = [d.dossier_id for d in manager.running()]
    waiting = [d.dossier_id for d in manager.waiting()]
    if dossier_id in running:
        return "running"
    if dossier_id in waiting:
        return f"waiting ({waiting.index(dossier_id) + 1} in the queue)"
    return "-"


def dossier_view(manager, d) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A dossier for the owner's eyes (CLI, dashboard): everything, with its place
    in the queue and the budget used."""
    u = manager.usage(d)
    return d.model_dump(mode="json") | {
        "place": "paused" if d.paused else place(manager, d.dossier_id),
        "usage": {
            "trials": u.trials,
            "hours": round(u.hours, 4),
            "tokens": u.tokens,
            "cloud_gbp": u.cloud_gbp,
        },
    }


def floor_view(manager) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    dossiers = sorted(manager.store.list(), key=lambda d: (d.opened_at, d.dossier_id))
    brief = manager.floor_briefs.current()
    return {
        "researchers": manager.config.researchers,
        "floor_brief": brief.text if brief else None,
        "dossiers": [dossier_view(manager, d) for d in dossiers],
    }


# --------------------------------------------------------------------------- Certo
def repo_root() -> Path:
    import firm

    return Path(firm.__file__).resolve().parents[2]


def certo_server(
    *,
    config_root: Path,
    data_root: Path,
    state_root: Path,
    sink: EventSink = NULL_SINK,
    repo: Path | None = None,
    poll_seconds: float = 5.0,
):  # type: ignore[no-untyped-def]
    """The firm's one Certo server: `scripts/certo-mcp` inside its OS sandbox
    (firm.sandbox.certo), on the research role's local model."""
    import os
    import sys
    import tempfile
    from urllib.parse import urlparse

    from firm.core.config import load_models_config
    from firm.models.certo import (
        CONFIG_DIR,
        LAUNCHER,
        WORKSPACE,
        CertoServer,
        certo_settings,
        find_certo,
    )
    from firm.sandbox.certo import CertoSandbox, certo_command

    repo = repo or repo_root()
    settings = certo_settings(repo)  # private/<launcher>.env, then the environment
    url = settings.get("CERTO_LLM_BASE_URL")
    if not url:
        raise ValueError(
            f"CERTO_LLM_BASE_URL isn't set: create {settings['_settings_file']} "
            f"from {LAUNCHER}.env.example (docs/CERTO.md)"
        )
    port = urlparse(url).port or 11434
    certo_bin = find_certo(settings.get("CERTO_BIN"))
    install = Path(os.path.realpath(certo_bin)).parents[2]  # .../<install>/(.venv/)bin/certo
    home = Path.home()
    workspace = Path(settings.get("FIRM_CERTO_WORKSPACE") or Path(data_root) / WORKSPACE)
    workspace.mkdir(parents=True, exist_ok=True)
    box = CertoSandbox(
        workspace=workspace,
        tmp=Path(tempfile.mkdtemp(prefix="firm-certo-")),
        model_port=port,
        hidden=(repo, Path(data_root), Path(state_root)),
        readable=(
            install,
            home / CONFIG_DIR,
            home / ".openhands",
            repo / "scripts",
            Path(settings["_settings_file"]),
            repo / "src",
            Path(sys.prefix),
            Path(sys.base_prefix),
        ),
        writable=(home / ".openhands",),
    )
    models = load_models_config(config_root)
    role = models.roles["research"]
    return CertoServer(
        certo_command(box, [str(repo / LAUNCHER)]),
        workspace,
        models=models,
        model=f"{role.provider}/{role.model}",
        sink=sink,
        poll_seconds=poll_seconds,
    )


def model_gateway(config_root: Path, ledger: Ledger, *, repo: Path | None = None):  # type: ignore[no-untyped-def]
    """The egress gate for research models: Ollama on the host in OLLAMA_HOST (or,
    on the Mac, the one Certo's launcher uses), every call a MODEL_CALL event."""
    import os

    from firm.core.config import load_models_config
    from firm.ledger.sink import LedgerSink
    from firm.ledger.spend import cloud_spend_gbp
    from firm.models.certo import certo_settings
    from firm.models.gateway import ModelGateway
    from firm.models.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST") or certo_settings(repo or repo_root()).get(
        "CERTO_LLM_BASE_URL"
    )
    if not host:
        raise ValueError("no model server: set OLLAMA_HOST (docs/CERTO.md)")
    if "://" not in host:
        host = f"http://{host}"
    return ModelGateway(
        load_models_config(config_root),
        {"ollama": OllamaProvider(host)},
        sink=LedgerSink(ledger),
        spent_this_month=lambda now: cloud_spend_gbp(ledger, now),
    )


class ResearchSession:
    """The research floor, wired for real researchers (M4): the manager with
    backtests and verification, the model gateway (private work, local models),
    the firm's one Certo server (sandboxed) and the data store's datasets.

        async with ResearchSession(config_root=..., data_root=..., state_root=...) as s:
            await s.manager.atick(ModelResearcher())
    """

    def __init__(
        self,
        *,
        config_root: Path,
        data_root: Path,
        state_root: Path,
        certo_minutes: int = 20,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        from datetime import UTC
        from datetime import datetime as dt

        self.config_root, self.data_root, self.state_root = config_root, data_root, state_root
        self.certo_minutes = certo_minutes
        self.now = now or (lambda: dt.now(UTC))
        self.manager: Any = None
        self.ledger: Ledger | None = None
        self._certo: Any = None

    async def __aenter__(self) -> ResearchSession:
        from firm.core.config import load_research_config
        from firm.core.domain import DataTag
        from firm.data.dataset import find_datasets
        from firm.ledger.sink import LedgerSink, ledger_path
        from firm.research.dossier import DossierStore, FloorBriefs
        from firm.research.manager import ResearchManager

        self.ledger = ledger = Ledger(ledger_path(self.state_root))
        sink = LedgerSink(ledger)
        gateway = model_gateway(self.config_root, ledger)
        self._certo = certo_server(
            config_root=self.config_root,
            data_root=self.data_root,
            state_root=self.state_root,
            sink=sink,
        )
        await self._certo.start()
        backtest, verify = verification_desk(
            data_root=self.data_root,
            config_root=self.config_root,
            state=self.state_root,
            now=self.now,
            sink=sink,
        )

        def think(dossier_id: str, messages: list[dict[str, str]]) -> str:
            reply = gateway.chat(
                role="research", messages=messages, tag=DataTag.PRIVATE, dossier_id=dossier_id
            )
            return str(reply.text)

        self.manager = ResearchManager(
            load_research_config(self.config_root),
            DossierStore(self.data_root, sink=sink),
            backtest=backtest,
            verify=verify,
            model_usage=ledger_model_usage(ledger),
            floor_briefs=FloorBriefs(self.data_root),
            think=think,
            coder=self.code,
            datasets=lambda instrument, tf: find_datasets(self.data_root, instrument, tf),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._certo is not None:
            await self._certo.close()

    async def code(self, dossier_id: str, task: str):  # type: ignore[no-untyped-def]
        """One Certo task in the dossier's project; the strategy file's bytes back."""
        from firm.research.agent import STRATEGY_FILE
        from firm.research.certo_projects import prepare_project
        from firm.research.manager import CodeResult

        folder = prepare_project(self._certo.workspace, dossier_id)
        run = await self._certo.run_task(
            dossier_id, task, dossier_id=dossier_id, max_minutes=self.certo_minutes
        )
        status = await self._certo.wait(run, timeout=self.certo_minutes * 60 + 120)
        result = status.get("result") or {}
        file = folder / STRATEGY_FILE
        code = file.read_bytes() if file.is_file() else None
        note = str(result.get("error") or result.get("summary") or status.get("state"))[:300]
        passed = bool(status.get("passed")) and status.get("verdict") == "verified"
        return CodeResult(status.get("verdict"), passed, code, note)


# ------------------------------------------------------------ brief proposals
def approve_proposal(
    manager, ledger: Ledger, store, proposal_id: str, *, dossier_id: str | None, via: str
):  # type: ignore[no-untyped-def]
    """The owner approves a senior-quant proposal: a dossier opens with it as its
    brief (CLI and dashboard). Refuses anything not in `proposed`."""
    from firm.research.proposals import ProposalError

    p = store.get(proposal_id)
    if p.status != "proposed":
        raise ProposalError(f"{proposal_id} is already {p.status}")
    d = manager.start(
        brief=p.proposal.as_brief(),
        market=p.proposal.market,
        instruments=p.proposal.instruments,
        timeframes=p.proposal.timeframes,
        dossier_id=dossier_id,
    )
    store.approve(proposal_id, d.dossier_id)
    owner_command(
        ledger, "proposals approve", {"proposal_id": proposal_id, "dossier_id": d.dossier_id}, via
    )
    return store.get(proposal_id), d


def reject_proposal(ledger: Ledger, store, proposal_id: str, reason: str, *, via: str) -> None:  # type: ignore[no-untyped-def]
    store.reject(proposal_id, reason)
    owner_command(ledger, "proposals reject", {"proposal_id": proposal_id, "reason": reason}, via)


def proposal_view(p) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A proposal for the owner's eyes: everything, plus the brief it becomes."""
    return p.model_dump(mode="json") | {"brief": p.proposal.as_brief()}
