"""Everything a check needs, frozen before any check runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from firm.core.config import VerifierConfig
from firm.verify.backtest import BacktestResult, run_backtest
from firm.verify.state import OosVault, TrialRegistry
from firm.verify.strategy import ParamValue, Strategy


@dataclass(frozen=True)
class VerifyContext:
    strategy: Strategy
    params: Mapping[str, ParamValue]
    strategy_id: str
    candidate_key: str  # hash of code + params + fingerprint of the bars read
    legacy_keys: tuple[str, ...]  # pre-2026-11-16 keys of the same candidate (vault aliases)
    scope: str  # strategy | instrument(s) | timeframe: the unit of an OOS opening
    config_key: str  # hash of costs + verifier config: one sealed OOS result per config
    trial_ids: tuple[str, ...]
    dataset_version: str
    in_sample: pd.DataFrame  # bars before the hidden period: all an author may see
    full: pd.DataFrame  # in-sample + hidden period; only the OOS check touches the tail
    cost_bps: float
    position: float  # fraction of equity a target of 1 holds (verifier sizing)
    periods_per_year: float
    config: VerifierConfig
    registry: TrialRegistry
    vault: OosVault
    now: datetime
    _cache: dict[str, object] = field(default_factory=dict, compare=False, repr=False)

    @property
    def oos_start(self) -> int:
        return len(self.in_sample)

    def signals(
        self, bars: pd.DataFrame, params: Mapping[str, ParamValue] | None = None
    ) -> pd.Series:
        return self.strategy.signals(
            bars.copy(), dict(params if params is not None else self.params)
        )

    def in_sample_backtest(
        self, params: Mapping[str, ParamValue] | None = None, cost_multiplier: float = 1.0
    ) -> BacktestResult:
        key = f"is:{sorted((params or self.params).items())}:{cost_multiplier}"
        if key not in self._cache:
            self._cache[key] = run_backtest(
                self.in_sample,
                self.signals(self.in_sample, params),
                cost_bps=self.cost_bps * cost_multiplier,
                position=self.position,
            )
        return self._cache[key]  # type: ignore[return-value]
