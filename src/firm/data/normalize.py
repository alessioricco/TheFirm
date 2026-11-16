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
from firm.data.providers import BAR_PARSERS, TRADE_PARSERS, BarRow, TradeRow
from firm.data.reference import latest_instruments, resolve
from firm.data.store import Capture, StoreError, iter_captures, partition, sha256_bytes, write_once

NORMALIZER_VERSION = "1"

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
) -> list[NormalizedFile]:
    by_year: dict[int, list[dict[str, object]]] = {}
    for r in rows:
        t = int(r[time_key])  # type: ignore[call-overload]
        if not 0 <= t < _MAX_NS:
            raise StoreError(f"timestamp out of range: {t}")
        by_year.setdefault(_utc_year(t), []).append(r)
    out = []
    for year in sorted(by_year):
        chunk = by_year[year]
        table = pa.Table.from_pylist(chunk, schema=schema)
        data = _parquet_bytes(table)
        sha = sha256_bytes(data)
        parts = {"kind": kind, "venue": venue, "instrument": instrument}
        if tf is not None:
            parts["tf"] = tf
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
            )
        )
    return out


def _merge(
    captures: Sequence[Capture], parse: object, key: str, **kw: object
) -> tuple[list[dict[str, object]], int]:
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
    root: Path, *, provider: str, provider_symbol: str, tf: str
) -> list[NormalizedFile]:
    """All bar captures for one provider symbol and timeframe -> Parquet by year."""
    caps = list(
        iter_captures(root, provider=provider, kind="bars", provider_symbol=provider_symbol, tf=tf)
    )
    if not caps:
        raise StoreError(f"no bar captures for {provider}:{provider_symbol} {tf}")
    rec = resolve(latest_instruments(root), provider, provider_symbol)
    rows, revisions = _merge(caps, BAR_PARSERS[provider], "open_time_ns", tf=tf)
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
    )


def normalize_trades(root: Path, *, provider: str, provider_symbol: str) -> list[NormalizedFile]:
    caps = list(
        iter_captures(root, provider=provider, kind="trades", provider_symbol=provider_symbol)
    )
    if not caps:
        raise StoreError(f"no trade captures for {provider}:{provider_symbol}")
    rec = resolve(latest_instruments(root), provider, provider_symbol)
    rows, revisions = _merge(caps, TRADE_PARSERS[provider], "trade_id")
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
