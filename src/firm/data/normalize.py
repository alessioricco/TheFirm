"""raw captures -> normalized Parquet in the firm schema.

Deterministic: the same captures always give the same bytes, so the same
file names (`part-<sha8>.parquet`) and the same dataset version. Rows from
overlapping captures are de-duplicated by key; if two captures disagree on a
closed bar, the later capture wins and the disagreement is counted.

Times are int64 nanoseconds since the epoch, UTC. Prices are unadjusted.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from firm.core.base import FirmModel, Id, Sha256
from firm.core.config import DataQualityConfig
from firm.core.timeframes import is_aligned
from firm.data import providers
from firm.data.providers import BarRow, TradeRow
from firm.data.quality import Implausible, implausible_bars
from firm.data.reference import latest_instruments, resolve
from firm.data.store import Capture, StoreError, iter_captures, partition, sha256_bytes, write_once

NORMALIZER_VERSION = "3"  # 2: bar alignment, derived series; 3: implausible bars quarantined

BAR_SCHEMA = pa.schema(
    [
        ("open_time_ns", pa.int64()),
        ("close_time_ns", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("capture_sha8", pa.string()),
    ]
)
TRADE_SCHEMA = pa.schema(
    [
        ("time_ns", pa.int64()),
        ("trade_id", pa.string()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("taker_side", pa.string()),
        ("capture_sha8", pa.string()),
    ]
)
_MAX_NS = 2**62  # sanity bound for int64 nanosecond timestamps


class NormalizedFile(FirmModel):
    path: str  # relative to the store root
    sha256: Sha256
    rows: int
    kind: Id
    venue: Id
    instrument: Id
    tf: Id | None
    revisions: int = 0  # closed rows on which captures disagreed
    alignment: Id | None = None  # bars: "utc", "week:<day>" or "session"
    derived_from: Id | None = None  # bars built by firm.data.resample from this tf
    quality_rule: Id | None = None  # bars: the data-quality rule applied (None: none was)
    quarantined: tuple[dict[str, str], ...] = ()  # bars left out: {open_time, reason}


def _parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd", write_statistics=True)
    return buf.getvalue()


def _utc_year(ns: int) -> int:
    return datetime.fromtimestamp(ns // 1_000_000_000, tz=UTC).year


def _write_partitions(
    root: Path,
    rows: list[dict[str, object]],
    schema: pa.Schema,
    time_key: str,
    *,
    kind: str,
    venue: str,
    instrument: str,
    tf: str | None,
    revisions: int,
    alignment: str | None = None,
    derived_from: str | None = None,
    quality_rule: str | None = None,
    quarantined: Sequence[Implausible] = (),
) -> list[NormalizedFile]:
    by_year: dict[int, list[dict[str, object]]] = {}
    left_out: dict[int, list[dict[str, str]]] = {}
    for q in quarantined:
        left_out.setdefault(_utc_year(q.open_time_ns), []).append(q.as_dict())
        by_year.setdefault(_utc_year(q.open_time_ns), [])
    for r in rows:
        t = int(r[time_key])  # type: ignore[call-overload]
        if not 0 <= t < _MAX_NS:
            raise StoreError(f"timestamp out of range: {t}")
        by_year.setdefault(_utc_year(t), []).append(r)
    out = []
    for year in sorted(by_year):
        chunk = by_year[year]
        if not chunk:
            continue  # a year whose only bars were quarantined (listed nowhere else)
        table = pa.Table.from_pylist(chunk, schema=schema)
        data = _parquet_bytes(table)
        sha = sha256_bytes(data)
        parts = {"kind": kind, "venue": venue, "instrument": instrument}
        if tf is not None:
            parts["tf"] = tf
        if derived_from is not None:
            parts["derived_from"] = derived_from
        rel = Path("normalized") / partition(**parts, year=str(year)) / f"part-{sha[:8]}.parquet"
        write_once(root / rel, data)
        out.append(
            NormalizedFile(
                path=rel.as_posix(),
                sha256=sha,
                rows=len(chunk),
                kind=kind,
                venue=venue,
                instrument=instrument,
                tf=tf,
                revisions=revisions if year == max(by_year) else 0,
                alignment=alignment,
                derived_from=derived_from,
                quality_rule=quality_rule,
                quarantined=tuple(left_out.get(year, ())),
            )
        )
    return out


def _merge(
    captures: Sequence[Capture], parse: object, key: str, **kw: object
) -> tuple[list[dict[str, object]], int]:
    methods = {cap.meta.capture_method for cap in captures}
    if "synthetic" in methods and len(methods) > 1:
        raise StoreError("refusing to mix synthetic and real captures in one series")
    merged: dict[object, dict[str, object]] = {}
    revisions = 0
    for cap in captures:  # ordered by capture time: later wins
        if cap.meta.captured_at is None:
            raise StoreError(f"{cap.path}: capture time unknown (legacy); can't tell closed bars")
        rows: list[BarRow] | list[TradeRow] = parse(  # type: ignore[operator]
            cap.read(), captured_at=cap.meta.captured_at, **kw
        )
        for row in rows:
            rec = dict(row) | {"capture_sha8": cap.meta.sha256[:8]}
            k = rec[key]
            old = merged.get(k)
            if old is not None:
                same = {a: b for a, b in old.items() if a != "capture_sha8"} == {
                    a: b for a, b in rec.items() if a != "capture_sha8"
                }
                if not same:
                    revisions += 1
            merged[k] = rec
    return [merged[k] for k in sorted(merged)], revisions  # type: ignore[type-var]


def normalize_bars(
    root: Path,
    *,
    provider: str,
    provider_symbol: str,
    tf: str,
    quality: DataQualityConfig | None = None,
) -> list[NormalizedFile]:
    """All bar captures for one provider symbol and timeframe -> Parquet by year.

    With `quality`, implausible bars (firm.data.quality) are left out as gaps
    and listed in each file's `quarantined`; the raw captures are untouched."""
    caps = list(
        iter_captures(root, provider=provider, kind="bars", provider_symbol=provider_symbol, tf=tf)
    )
    if not caps:
        raise StoreError(f"no bar captures for {provider}:{provider_symbol} {tf}")
    rec = resolve(latest_instruments(root), provider, provider_symbol)
    source = providers.get(provider)
    rows, revisions = _merge(caps, source.parse_bars, "open_time_ns", tf=tf)
    alignment = source.alignment(tf)
    for r in rows:
        t = int(r["open_time_ns"]) // 1_000_000_000  # type: ignore[call-overload]
        if not is_aligned(t, tf, alignment):
            when = datetime.fromtimestamp(t, tz=UTC).isoformat()
            raise StoreError(
                f"{provider}:{provider_symbol} {tf} bar at {when} breaks the declared "
                f"alignment {alignment!r}; fix the provider's declaration, don't mix grids"
            )
    left_out = implausible_bars(rows, quality) if quality is not None else []
    if left_out:
        bad = {q.open_time_ns for q in left_out}
        rows = [r for r in rows if r["open_time_ns"] not in bad]
    return _write_partitions(
        root,
        rows,
        BAR_SCHEMA,
        "open_time_ns",
        kind="bars",
        venue=rec.venue,
        instrument=rec.instrument,
        tf=tf,
        revisions=revisions,
        alignment=alignment,
        quality_rule=quality.rule_id if quality is not None else None,
        quarantined=left_out,
    )


def _trade_parser(provider: str, provider_symbol: str):  # type: ignore[no-untyped-def]
    """REST provider's parser, or a stream provider's capture reader (live data)."""
    from firm.data import streams

    if provider in streams.available():
        stream = streams.get(provider)
        return lambda payload, captured_at: stream.parse_capture(
            payload, symbol=provider_symbol, captured_at=captured_at
        )
    return providers.get(provider).parse_trades


def normalize_trades(root: Path, *, provider: str, provider_symbol: str) -> list[NormalizedFile]:
    caps = list(
        iter_captures(root, provider=provider, kind="trades", provider_symbol=provider_symbol)
    )
    if not caps:
        raise StoreError(f"no trade captures for {provider}:{provider_symbol}")
    rec = resolve(latest_instruments(root), provider, provider_symbol)
    rows, revisions = _merge(caps, _trade_parser(provider, provider_symbol), "trade_id")
    rows.sort(key=lambda r: (r["time_ns"], r["trade_id"]))  # type: ignore[arg-type,return-value]
    return _write_partitions(
        root,
        rows,
        TRADE_SCHEMA,
        "time_ns",
        kind="trades",
        venue=rec.venue,
        instrument=rec.instrument,
        tf=None,
        revisions=revisions,
    )
