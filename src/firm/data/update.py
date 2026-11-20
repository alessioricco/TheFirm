"""What the store already holds, and bringing it up to date.

- `stored_series` lists every bar series in `raw/` (one per provider,
  provider symbol and timeframe): how it was captured, how many captures,
  and the close time of its last closed bar, read from the captures
  themselves (the same parsers the normaliser uses, so a still-forming bar
  never counts).
- `fetch_series` is a first download (or a backfill with `start`): it
  records the instrument the first time and stores every response as a
  capture.
- `update_series` downloads only what is missing: from a couple of bars
  before the last stored close up to now. The overlap re-reads the last
  closed bars, so a venue that changed one shows up as a revision when the
  dataset is built. A series whose next bar hasn't closed yet makes no
  request.
- `build_series` normalises one series and freezes a dataset version of it.

Only API captures are updated: synthetic series are test data, and legacy
imports have no source to ask. Network use is the caller's (`firm data
fetch` / `update`, owner-run); tests pass a fake transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from firm.core.config import DataQualityConfig
from firm.core.timeframes import tf_seconds
from firm.data import providers
from firm.data.dataset import DatasetManifest, build_dataset
from firm.data.fetch import Fetcher, ensure_instrument
from firm.data.normalize import NormalizedFile, normalize_bars
from firm.data.reference import latest_instruments
from firm.data.store import Capture, iter_captures

OVERLAP_BARS = 2  # closed bars re-read on every update (revision check)


@dataclass(frozen=True)
class Series:
    """One stored bar series and how far it reaches."""

    provider: str
    symbol: str  # as the provider spells it
    tf: str
    venue: str | None  # from the instrument master (None: no record)
    instrument: str | None
    methods: tuple[str, ...]  # capture methods present (api, synthetic, legacy_import)
    captures: int
    first_open: datetime | None
    last_close: datetime | None  # close of the last closed bar
    last_captured_at: datetime | None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.symbol}:{self.tf}"

    @property
    def updatable(self) -> bool:
        return self.methods == ("api",) and self.provider in providers.available()


def _span(caps: list[Capture], provider: str, tf: str) -> tuple[datetime | None, datetime | None]:
    try:
        source = providers.get(provider)
    except ValueError:
        return None, None
    first: int | None = None
    last: int | None = None
    for cap in caps:
        if cap.meta.captured_at is None or cap.meta.capture_method not in ("api", "synthetic"):
            continue  # legacy imports: not in the provider's format, closed bars unknown
        try:
            rows = source.parse_bars(cap.read(), tf=tf, captured_at=cap.meta.captured_at)
        except ValueError:
            continue  # a malformed capture: building the dataset reports it
        if rows:
            lo, hi = rows[0]["open_time_ns"], rows[-1]["close_time_ns"]
            first = lo if first is None else min(first, lo)
            last = hi if last is None else max(last, hi)

    def ts(ns: int | None) -> datetime | None:
        return None if ns is None else datetime.fromtimestamp(ns / 1e9, tz=UTC)

    return ts(first), ts(last)


def stored_series(
    root: Path,
    *,
    provider: str | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> list[Series]:
    """Every bar series in the store (filtered), sorted by provider, symbol, tf."""
    groups: dict[tuple[str, str, str], list[Capture]] = {}
    for cap in iter_captures(root, provider=provider, kind="bars", provider_symbol=symbol, tf=tf):
        if cap.meta.tf is None:
            continue
        groups.setdefault((cap.meta.provider, cap.meta.provider_symbol, cap.meta.tf), []).append(
            cap
        )
    master = latest_instruments(root)
    out = []
    for (prov, sym, t), caps in sorted(groups.items()):
        venue = instrument = None
        for (v, i), (_, rec) in master.items():
            if rec.provider_symbols.get(prov) == sym:
                venue, instrument = v, i
                break
        first, last = _span(caps, prov, t)
        times = [c.meta.captured_at for c in caps if c.meta.captured_at is not None]
        out.append(
            Series(
                provider=prov,
                symbol=sym,
                tf=t,
                venue=venue,
                instrument=instrument,
                methods=tuple(sorted({c.meta.capture_method for c in caps})),
                captures=len(caps),
                first_open=first,
                last_close=last,
                last_captured_at=max(times) if times else None,
            )
        )
    return out


def fetch_series(
    fetcher: Fetcher,
    *,
    provider: str,
    symbol: str,
    instrument: str,
    tf: str,
    start: datetime | None,
    end: datetime,
    venue: str | None = None,
    asset_class: Literal["crypto", "equity"] = "crypto",
    quote: str | None = None,
    listed_at: datetime | None = None,
    trades_pages: int = 0,
) -> int:
    """First download (or backfill) of one series. Returns the bar requests made."""
    ensure_instrument(
        fetcher.root,
        provider=provider,
        symbol=symbol,
        venue=venue or provider,
        instrument=instrument,
        asset_class=asset_class,
        listed_at=listed_at,
        quote=quote,
        clock=fetcher.clock,
    )
    n = fetcher.bars(provider, symbol, tf, start=start, end=end)
    if trades_pages:
        fetcher.trades(provider, symbol, pages=trades_pages)
    return n


@dataclass
class UpdatePlan:
    """What `update_series` would do for one series."""

    series: Series
    start: datetime | None  # first bar open to ask for (None: nothing to do)
    requests: list[providers.Request] = field(default_factory=list)
    note: str = ""


def plan_update(series: Series, now: datetime) -> UpdatePlan:
    """From OVERLAP_BARS before the last stored close up to `now`."""
    if not series.updatable:
        why = (
            f"not updated: {'/'.join(series.methods)} captures"
            if series.methods != ("api",)
            else f"not updated: unknown provider {series.provider!r}"
        )
        return UpdatePlan(series, None, note=why)
    if series.last_close is None:
        return UpdatePlan(series, None, note="no closed bar stored: use `firm data fetch`")
    step = timedelta(seconds=tf_seconds(series.tf))
    if now < series.last_close + step:
        return UpdatePlan(series, None, note="up to date (next bar not closed yet)")
    start = series.last_close - OVERLAP_BARS * step
    source = providers.get(series.provider)
    requests = source.bar_requests(series.symbol, series.tf, start=start, end=now)
    missing = int((now - series.last_close) / step)
    note = f"~{missing} new bar(s)"
    cap = source.max_bars_per_request
    if series.provider == "kraken" and cap is not None and missing + OVERLAP_BARS > cap:
        note += f"; Kraken serves only the last {cap} bars: a gap will remain"
    return UpdatePlan(series, start, requests, note)


def update_series(fetcher: Fetcher, series: Series, now: datetime) -> UpdatePlan:
    """Run the plan: store each response as a capture. Returns the plan run."""
    p = plan_update(series, now)
    if p.start is not None:
        fetcher.bars(series.provider, series.symbol, series.tf, start=p.start, end=now)
    return p


def build_series(
    root: Path, series: Series, quality: DataQualityConfig | None
) -> tuple[DatasetManifest, list[NormalizedFile]]:
    """Normalise one series and freeze a dataset version of it."""
    files = normalize_bars(
        root, provider=series.provider, provider_symbol=series.symbol, tf=series.tf, quality=quality
    )
    return build_dataset(root, files), files


def gaps(root: Path, files: list[NormalizedFile]) -> int | None:
    """Missing bars between the first and last bar of one series, on a fixed
    grid (None for session-aligned bars, whose grid follows the calendar).
    Quarantined bars count as missing."""
    if not files or files[0].alignment in (None, "session") or files[0].tf is None:
        return None
    import duckdb

    con = duckdb.connect()
    try:
        lo, hi, n = con.execute(
            "SELECT min(open_time_ns), max(open_time_ns), count(*) FROM read_parquet($p)",
            {"p": [str(root / f.path) for f in files]},
        ).fetchone()  # type: ignore[misc]
    finally:
        con.close()
    if not n:
        return None
    step = tf_seconds(files[0].tf) * 1_000_000_000
    return int((hi - lo) // step + 1 - n)


def age(last_close: datetime | None, now: datetime) -> str:
    """Time since the last closed bar, short (`45m`, `5h`, `3d`)."""
    if last_close is None:
        return "-"
    s = max(0, int((now - last_close).total_seconds()))
    if s < 3600:
        return f"{s // 60}m"
    if s < 2 * 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"
