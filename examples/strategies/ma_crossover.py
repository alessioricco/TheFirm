"""Clean moving-average crossover: long when the fast MA is above the slow MA.

Causal: both moving averages at bar i use closes up to bar i only.
"""

from typing import ClassVar

import pandas as pd


class MovingAverageCrossover:
    name = "ma_crossover"
    default_params: ClassVar[dict[str, int]] = {"fast": 10, "slow": 40}

    def signals(self, bars: pd.DataFrame, params) -> pd.Series:
        fast, slow = int(params["fast"]), int(params["slow"])
        if not 1 <= fast < slow:
            raise ValueError("need 1 <= fast < slow")
        close = bars["close"]
        f = close.rolling(fast).mean()
        s = close.rolling(slow).mean()
        signal = (f > s).astype(float)
        return signal.where(s.notna())
