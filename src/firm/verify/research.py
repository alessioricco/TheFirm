"""Research backtests, run by the verification desk (M3 plan, R4; firm rules 4 and 5).

A researcher never backtests on its own: its code runs only in the sandbox,
which can't read the data store. It asks this desk, which owns the trial
count and the hidden period, and so can guarantee:

- **the research view only**: bars before the hidden period the verifier
  will open later (the last `oos.fraction` of the dataset as of now, read
  through `Dataset.capped`, which refuses anything later);
- **no result without a counted trial**: the trial (experiment ID, bars per
  year, per-bar Sharpe) is recorded in the registry, and so the ledger,
  before the result is returned; if recording fails, nothing is returned;
- **same backtest, same trial**: a trial is its code, parameters and bars,
  so re-running it doesn't inflate the count, and a later verification's
  deflated Sharpe sees every distinct one;
- **bad data is never a trial**: implausible bars in the window refuse the
  backtest, as they make a verification UNVERIFIED.

One `ResearchBacktester` holds one strategy (sandboxed when it comes from
the private store, as everywhere) for many runs: a parameter search.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from firm.core.config import (
    load_config,
    load_data_quality,
    load_fee_schedule,
    load_verifier_config,
)
from firm.core.domain import TrialRecord
from firm.core.events import NULL_SINK, EventSink
from firm.core.timeframes import periods_per_year
from firm.data.dataset import open_dataset
from firm.data.quality import implausible_bars
from firm.verify.backtest import metrics, run_backtest
from firm.verify.state import TrialRegistry
from firm.verify.strategy import LoadedStrategy, load_strategy, private_root, sandbox_policy
from firm.verify.verifier import bars_fingerprint, cost_bps, split_in_sample


@dataclass(frozen=True)
class ResearchResult:
    trial_id: str
    new_trial: bool  # False: the same backtest was already counted
    metrics: dict[str, float]
    bars: int
    hidden_from: datetime  # the research view ends here
    last_bar_close: datetime


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class ResearchBacktester:
    def __init__(
        self,
        *,
        strategy_path: Path,
        class_name: str,
        experiment_id: str,
        data_root: Path,
        dataset_version: str,
        venue: str,
        instrument: str,
        tf: str,
        config_root: Path,
        jurisdiction: str,
        market: str,
        state: Path,
        now: datetime,
        verifier_name: str = "default",
        sink: EventSink = NULL_SINK,
        sandbox: bool = False,
    ) -> None:
        self.experiment_id, self.now = experiment_id, now
        self.strategy_path, self.class_name = strategy_path, class_name
        self._sandbox, self._data_root, self._state = sandbox, data_root, state
        self._config_root = config_root
        cfg = load_config(config_root, jurisdiction, [market])
        profile = cfg.markets[market]
        fees = load_fee_schedule(config_root, profile.costs.fee_schedule_for(venue))
        vcfg = load_verifier_config(config_root, verifier_name)
        self._cost = cost_bps(profile, fees)
        self._position = vcfg.sizing.position_pct / 100
        self._ppy = periods_per_year(profile.calendar, tf)
        self._rule = load_data_quality(config_root)
        self._dataset_version = dataset_version
        self.registry = TrialRegistry(state, sink=sink)

        ds = open_dataset(data_root, dataset_version)
        window = ds.bars(venue, instrument, tf, as_of=now)
        if window.empty:
            raise LookupError(f"no bars for {venue}:{instrument} {tf} in {dataset_version}")
        # The verifier hides the last fraction of the window it is given; with more
        # data later its hidden period starts later still, never earlier.
        self.hidden_from: datetime = (
            window["open_time"].iloc[split_in_sample(window, vcfg.oos.fraction)].to_pydatetime()
        )
        view = ds.capped(self.hidden_from)
        self._bars = view.bars(
            venue, instrument, tf, as_of=self.hidden_from - timedelta(microseconds=1)
        )
        self._loaded: LoadedStrategy | None = None

    def __enter__(self) -> ResearchBacktester:
        policy = sandbox_policy(
            self.strategy_path,
            data_root=self._data_root,
            hidden=(self._state, self._config_root.parent),
            force=self._sandbox,
        )
        self._loaded = load_strategy(
            self.strategy_path,
            self.class_name,
            sandbox=policy,
            private_root=private_root(self._data_root),
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._loaded is not None:
            self._loaded.close()
            self._loaded = None

    def run(self, params: Mapping[str, Any]) -> ResearchResult:
        loaded = self._loaded
        if loaded is None:
            raise RuntimeError("use the backtester as a context manager (`with ...:`)")
        bars = self._bars
        issues = implausible_bars(bars.to_dict("records"), self._rule, time_key="open_time")
        if issues:
            raise ValueError(
                f"{len(issues)} implausible bar(s) in the research view, e.g. {issues[0]}; "
                "fix the data first (no trial counted)"
            )
        p = dict(loaded.strategy.default_params) | dict(params)
        signals = loaded.strategy.signals(bars.copy(), p)
        result = run_backtest(bars, signals, cost_bps=self._cost, position=self._position)
        m = metrics(result, 0, len(bars), self._ppy)
        per_bar = m["sharpe_per_bar"]  # the scale the trial-count check expects
        name = loaded.strategy.name
        trial_id = "t-" + _h([name, loaded.code_sha256, p, bars_fingerprint(bars)])[:16]
        new = self.registry.record(  # counted before anything is returned
            TrialRecord(
                trial_id=trial_id,
                experiment_id=self.experiment_id,
                strategy_id=name,
                parameters=p,
                metrics={"sharpe_per_bar": per_bar, "periods_per_year": self._ppy},
                dataset_version=self._dataset_version,
                recorded_at=self.now,
            )
        )
        return ResearchResult(
            trial_id=trial_id,
            new_trial=new,
            metrics=m,
            bars=len(bars),
            hidden_from=self.hidden_from,
            last_bar_close=bars["close_time"].iloc[-1].to_pydatetime(),
        )
