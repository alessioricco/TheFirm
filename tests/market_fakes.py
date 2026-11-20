"""A fake Binance klines endpoint for data-update tests (synthetic bars, no network)."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from firm.data.synthetic import binance_klines_payload, synthetic_bars

START = datetime(2020, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)
BARS = {b.open_time: b for b in synthetic_bars(500, seed=3, start=START)}


class Market:
    """Binance klines as of `now`: every bar opened by then (the last one forming)."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        q = parse_qs(urlparse(url).query)
        lo, hi = int(q["startTime"][0]) // 1000, (int(q["endTime"][0]) + 1) // 1000
        t_now = self.now.timestamp()
        return binance_klines_payload(
            [b for t, b in sorted(BARS.items()) if lo <= t < hi and t <= t_now]
        )
