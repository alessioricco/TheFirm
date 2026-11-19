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
