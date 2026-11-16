"""Derive coarser timeframes from finer bars, on the firm's canonical grid.

Why: providers disagree on bar boundaries (Kraken's weeks start Thursday,
Binance's Monday; Coinbase has no 4h or 1w at all), and comparing their
native bars would compare different weeks. Deriving 1h/4h/1d/1w from finer
bars gives one grid for every source: intraday and days aligned to the Unix
epoch (days 00:00-24:00 UTC), weeks starting Monday 00:00 UTC.

Rules:

- A derived bar is emitted only when its bucket is COMPLETE: every finer bar
  in it is present. A gap leaves the whole bucket out (never a partial bar
  that looks complete).
- Derived bars are built only from closed finer bars, so a derived bar is
  known at its own close: the same point-in-time rule as native bars.
- open = first open, high = max, low = min, close = last close, volume = sum.
- Only "utc"-aligned sources (crypto, 24x7). Session-aligned data (shares)
  needs the exchange calendar to define its buckets: milestone 6.
- Derived series are stored apart from native ones (`derived_from=<tf>` in
  the path and the manifest) and are rebuilt, never edited.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb

from firm.core.timeframes import bucket_start, tf_seconds
from firm.data.normalize import BAR_SCHEMA, NormalizedFile, _write_partitions
from firm.data.store import StoreError

NS = 1_000_000_000


def check_derivable(from_tf: str, to_tf: str) -> int:
    """How many `from_tf` bars make one `to_tf` bar; refuses impossible pairs."""
    lo, hi = tf_seconds(from_tf), tf_seconds(to_tf)
    if hi <= lo or hi % lo:
        raise ValueError(f"can't derive {to_tf} from {from_tf}: not a whole multiple")
    return hi // lo


def aggregate(
    rows: Sequence[dict[str, object]], from_tf: str, to_tf: str
) -> tuple[list[dict[str, object]], int]:
    """Complete `to_tf` buckets from sorted `from_tf` rows; returns (bars, incomplete count)."""
    need = check_derivable(from_tf, to_tf)
    step = tf_seconds(to_tf)
    buckets: dict[int, list[dict[str, object]]] = {}
    for r in rows:
        t = int(r["open_time_ns"]) // NS  # type: ignore[call-overload]
        buckets.setdefault(bucket_start(t, to_tf), []).append(r)
    out, incomplete = [], 0
    for start in sorted(buckets):
        group = sorted(buckets[start], key=lambda r: r["open_time_ns"])  # type: ignore[arg-type,return-value]
        if len(group) != need:
            incomplete += 1
            continue
        out.append(
            {
                "open_time_ns": start * NS,
                "close_time_ns": (start + step) * NS,
                "open": group[0]["open"],
                "high": max(g["high"] for g in group),  # type: ignore[type-var]
                "low": min(g["low"] for g in group),  # type: ignore[type-var]
                "close": group[-1]["close"],
                "volume": sum(g["volume"] for g in group),  # type: ignore[misc]
                "capture_sha8": "derived",
            }
        )
    return out, incomplete


def derive_bars(
    root: Path, sources: Sequence[NormalizedFile], *, to_tf: str
) -> list[NormalizedFile]:
    """Derive `to_tf` bars from one series' normalized files (same venue, instrument, tf)."""
    if not sources:
        raise StoreError("nothing to derive from")
    keys = {(f.kind, f.venue, f.instrument, f.tf, f.alignment, f.derived_from) for f in sources}
    if len(keys) != 1:
        raise StoreError(f"derive from one series at a time, got {sorted(map(str, keys))}")
    kind, venue, instrument, from_tf, alignment, _ = keys.pop()
    if kind != "bars" or from_tf is None:
        raise StoreError("only bars can be derived")
    if alignment != "utc":
        raise StoreError(
            f"{venue}:{instrument} {from_tf} is {alignment!r}-aligned; deriving needs 24x7 "
            "bars on the UTC grid (session-based shares come with the exchange calendar, M6)"
        )
    check_derivable(from_tf, to_tf)
    with duckdb.connect() as con:
        rows = con.execute(
            "SELECT open_time_ns, close_time_ns, open, high, low, close, volume "
            "FROM read_parquet($files) ORDER BY open_time_ns",
            {"files": [str(root / f.path) for f in sources]},
        ).fetchall()
    cols = ["open_time_ns", "close_time_ns", "open", "high", "low", "close", "volume"]
    bars, _incomplete = aggregate([dict(zip(cols, r, strict=True)) for r in rows], from_tf, to_tf)
    if not bars:
        raise StoreError(f"no complete {to_tf} bucket in {venue}:{instrument} {from_tf}")
    canonical = "week:monday" if to_tf == "1w" else "utc"
    return _write_partitions(
        root,
        bars,
        BAR_SCHEMA,
        "open_time_ns",
        kind="bars",
        venue=venue,
        instrument=instrument,
        tf=to_tf,
        revisions=0,
        alignment=canonical,
        derived_from=from_tf,
    )
