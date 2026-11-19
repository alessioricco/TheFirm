"""Bars per year of an older trial, from the dataset version it ran on.

Trials recorded before 2026-11-19 carry their per-bar Sharpe but not their
timeframe. A dataset manifest lists its bar series with their timeframe and
alignment, so when a dataset holds bars of exactly one timeframe the trial's
scale is known: `session`-aligned bars follow an exchange calendar, any other
alignment is continuous (24x7). A dataset with several bar timeframes, or one
that can't be read, gives None: the trial is left out of the Sharpe variance
(it still counts towards the number of trials) and the report says how many.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from firm.core.domain import TrialRecord
from firm.core.timeframes import CONTINUOUS_CALENDAR, periods_per_year

EXCHANGE_CALENDAR = "XNYS"  # any exchange calendar: only daily/weekly bars are supported


def periods_from_dataset(root: Path, version: str) -> float | None:
    path = root / "datasets" / f"{version}.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        bars = [f for f in manifest["files"] if f.get("kind") == "bars" and f.get("tf")]
        series = {(f["tf"], f.get("alignment") == "session") for f in bars}
        if len(series) != 1:
            return None
        ((tf, session),) = series
        return periods_per_year(EXCHANGE_CALENDAR if session else CONTINUOUS_CALENDAR, tf)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def legacy_periods(root: Path) -> Callable[[TrialRecord], float | None]:
    """`trial -> bars per year` from its dataset version (cached per version)."""
    cache: dict[str, float | None] = {}

    def periods_of(trial: TrialRecord) -> float | None:
        v = trial.dataset_version
        if v not in cache:
            cache[v] = periods_from_dataset(root, v)
        return cache[v]

    return periods_of
