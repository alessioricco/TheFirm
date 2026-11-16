"""Data plane read interface (implemented by `firm.data.dataset.Dataset`).

Every read takes `as_of` and returns only what was known then; bars are
known at their close. `CappedDataset` is the research view that refuses
reads inside a strategy's hidden out-of-sample period.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd


class DataPlane(Protocol):
    @property
    def version(self) -> str: ...

    def bars(
        self,
        venue: str,
        instrument: str,
        tf: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
    ) -> pd.DataFrame: ...

    def trades(
        self, venue: str, instrument: str, *, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame: ...

    def universe(self, venue: str, *, as_of: datetime) -> dict[str, str]: ...
