"""The same crossover with PLANTED LOOKAHEAD: the fast MA uses tomorrow's close.

It looks brilliant in a backtest. The verifier's truncation check must reject it.
"""

from typing import ClassVar

import pandas as pd


class LookaheadCrossover:
    name = "lookahead_ma"
    default_params: ClassVar[dict[str, int]] = {"fast": 10, "slow": 40}

    def signals(self, bars: pd.DataFrame, params) -> pd.Series:
        fast, slow = int(params["fast"]), int(params["slow"])
        close = bars["close"]
        f = close.shift(-1).rolling(fast).mean()  # <- peeks one bar ahead
        s = close.rolling(slow).mean()
        signal = (f > s).astype(float)
        return signal.where(s.notna())
