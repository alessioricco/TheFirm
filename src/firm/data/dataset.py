"""Dataset versions and the point-in-time reader.

A dataset version is an immutable manifest (`datasets/ds-<sha12>.json`)
listing exact normalized files and reference records with their checksums.
Its ID is the hash of its content, so the same inputs always give the same
version, and any backtest that cites it can be reproduced byte for byte.

Every read takes `as_of` and returns only what was known then: a bar is
known at its close, a trade at its time (golden rule 3).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from firm.core.base import FirmModel, Id, Sha256
from firm.data.normalize import NORMALIZER_VERSION, NormalizedFile
from firm.data.reference import InstrumentRecord, latest_instruments
from firm.data.store import StoreError, canonical_json, sha256_bytes, write_once

BAR_COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
TRADE_COLUMNS = ["time", "trade_id", "price", "size", "taker_side"]


class HiddenPeriodError(PermissionError):
    """A capped (research) view was asked for data inside the hidden period."""


class ReferenceFile(FirmModel):
    path: str
    sha256: Sha256


class DatasetManifest(FirmModel):
    dataset_version: Id
    normalizer_version: Id
    files: tuple[NormalizedFile, ...]
    reference: tuple[ReferenceFile, ...]


def _content(
    files: Sequence[NormalizedFile], reference: Sequence[ReferenceFile]
) -> dict[str, object]:
    return {
        "normalizer_version": NORMALIZER_VERSION,
        "files": [f.model_dump(mode="json") for f in sorted(files, key=lambda f: f.path)],
        "reference": [r.model_dump(mode="json") for r in sorted(reference, key=lambda r: r.path)],
    }


def build_dataset(root: Path, files: Iterable[NormalizedFile]) -> DatasetManifest:
    """Freeze `files` plus the current instrument master into a dataset version."""
    files = list(files)
    if not files:
        raise StoreError("a dataset needs at least one file")
    venues_instruments = {(f.venue, f.instrument) for f in files}
    refs = []
    for key, (path, _rec) in sorted(latest_instruments(root).items()):
        if key in venues_instruments:
            refs.append(
                ReferenceFile(
                    path=path.relative_to(root).as_posix(), sha256=sha256_bytes(path.read_bytes())
                )
            )
    missing = venues_instruments - {k for k in latest_instruments(root)}
    if missing:
        raise StoreError(f"no instrument record for {sorted(missing)}")
    content = _content(files, refs)
    version = "ds-" + sha256_bytes(canonical_json(content))[:12]
    manifest = DatasetManifest.model_validate({"dataset_version": version, **content})
    write_once(
        root / "datasets" / f"{version}.json", canonical_json(manifest.model_dump(mode="json"))
    )
    return manifest


def _query(sql: str, **params: object) -> pd.DataFrame:
    with duckdb.connect() as con:
        return con.execute(sql, params).df()


def _ns(ts: datetime) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("as_of / start must be timezone-aware")
    return int(t.value)


class Dataset:
    """Read-only, point-in-time view of one dataset version."""

    def __init__(self, root: Path, manifest: DatasetManifest, *, verify: bool = True) -> None:
        self.root = root
        self.manifest = manifest
        if verify:
            for f in [*manifest.files, *manifest.reference]:
                if sha256_bytes((root / f.path).read_bytes()) != f.sha256:
                    raise StoreError(f"{f.path}: checksum mismatch for {manifest.dataset_version}")
        self._instruments = {
            (r.venue, r.instrument): r
            for r in (
                InstrumentRecord.model_validate_json((root / ref.path).read_text())
                for ref in manifest.reference
            )
        }

    @property
    def version(self) -> str:
        return self.manifest.dataset_version

    def _files(
        self,
        kind: str,
        venue: str,
        instrument: str,
        tf: str | None,
        derived_from: str | None = None,
    ) -> list[str]:
        want = (kind, venue, instrument, tf, derived_from)
        paths = [
            str(self.root / f.path)
            for f in self.manifest.files
            if (f.kind, f.venue, f.instrument, f.tf, f.derived_from) == want
        ]
        if not paths:
            src = f" derived from {derived_from}" if derived_from else ""
            raise LookupError(f"{self.version}: no {kind} for {venue}:{instrument} {tf or ''}{src}")
        return paths

    def alignment(
        self, venue: str, instrument: str, tf: str, derived_from: str | None = None
    ) -> str | None:
        """How this series' bars are aligned ("utc", "week:<day>", "session")."""
        found = {
            f.alignment
            for f in self.manifest.files
            if (f.kind, f.venue, f.instrument, f.tf, f.derived_from)
            == ("bars", venue, instrument, tf, derived_from)
        }
        return found.pop() if len(found) == 1 else None

    def bars(
        self,
        venue: str,
        instrument: str,
        tf: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
        derived_from: str | None = None,
    ) -> pd.DataFrame:
        """Closed bars with `close_time <= as_of` (and `open_time >= start`), oldest first.

        Native bars by default; `derived_from="1m"` reads bars derived from 1m."""
        files = self._files("bars", venue, instrument, tf, derived_from)
        lo = _ns(start) if start is not None else -(2**63)
        df = _query(
            "SELECT open_time_ns, close_time_ns, open, high, low, close, volume "
            "FROM read_parquet($files) WHERE close_time_ns <= $as_of AND open_time_ns >= $lo "
            "ORDER BY open_time_ns",
            files=files,
            as_of=_ns(as_of),
            lo=lo,
        )
        df.insert(0, "open_time", pd.to_datetime(df.pop("open_time_ns"), unit="ns", utc=True))
        df.insert(1, "close_time", pd.to_datetime(df.pop("close_time_ns"), unit="ns", utc=True))
        return df[BAR_COLUMNS].reset_index(drop=True)

    def trades(
        self, venue: str, instrument: str, *, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        files = self._files("trades", venue, instrument, None)
        lo = _ns(start) if start is not None else -(2**63)
        df = _query(
            "SELECT time_ns, trade_id, price, size, taker_side FROM read_parquet($files) "
            "WHERE time_ns <= $as_of AND time_ns >= $lo ORDER BY time_ns, trade_id",
            files=files,
            as_of=_ns(as_of),
            lo=lo,
        )
        df.insert(0, "time", pd.to_datetime(df.pop("time_ns"), unit="ns", utc=True))
        return df[TRADE_COLUMNS].reset_index(drop=True)

    def book(self, venue: str, instrument: str, *, as_of: datetime) -> pd.DataFrame:
        raise NotImplementedError("order book history: later (paid tick data)")

    def universe(self, venue: str, *, as_of: datetime) -> dict[str, str]:
        """Instrument -> status at `as_of`, delisted ones included."""
        return {
            inst: rec.status(as_of)
            for (v, inst), rec in sorted(self._instruments.items())
            if v == venue and rec.status(as_of) != "not_listed"
        }

    def capped(self, hidden_from: datetime) -> CappedDataset:
        return CappedDataset(self, hidden_from)


class CappedDataset:
    """The research view: refuses any read that could see the hidden period."""

    def __init__(self, inner: Dataset, hidden_from: datetime) -> None:
        self._inner = inner
        self.hidden_from = hidden_from
        self.version = inner.version

    def _check(self, as_of: datetime) -> None:
        if as_of >= self.hidden_from:
            raise HiddenPeriodError(
                f"as_of {as_of} is inside the hidden period ({self.hidden_from}+)"
            )

    def bars(
        self,
        venue: str,
        instrument: str,
        tf: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
        derived_from: str | None = None,
    ) -> pd.DataFrame:
        self._check(as_of)
        return self._inner.bars(
            venue, instrument, tf, as_of=as_of, start=start, derived_from=derived_from
        )

    def trades(
        self, venue: str, instrument: str, *, as_of: datetime, start: datetime | None = None
    ) -> pd.DataFrame:
        self._check(as_of)
        return self._inner.trades(venue, instrument, as_of=as_of, start=start)

    def universe(self, venue: str, *, as_of: datetime) -> dict[str, str]:
        self._check(as_of)
        return self._inner.universe(venue, as_of=as_of)


def open_dataset(root: Path, version: str, *, verify: bool = True) -> Dataset:
    path = root / "datasets" / f"{version}.json"
    if not path.is_file():
        raise LookupError(f"unknown dataset version {version} under {root}")
    manifest = DatasetManifest.model_validate(json.loads(path.read_text()))
    if manifest.dataset_version != version:
        raise StoreError(f"{path}: manifest says {manifest.dataset_version}")
    return Dataset(root, manifest, verify=verify)


def capture_methods(root: Path, version: str) -> dict[str, int]:
    """How the rows of a dataset version were captured: {method: rows}.

    Every normalised row names the capture it came from (`capture_sha8`); this
    looks each one up in `raw/`. Methods: `api` (downloaded or streamed),
    `synthetic`, `legacy_import`, and `unknown` for a capture no longer in the
    store. Derived bars inherit from their native source files, which are in
    the same dataset. A gate that needs real data accepts only `api`.
    """
    manifest = open_dataset(root, version).manifest
    methods: dict[str, str] = {}
    for meta_path in sorted((root / "raw").glob("**/*.meta.json")):
        meta = json.loads(meta_path.read_text())
        methods[meta["sha256"][:8]] = meta["capture_method"]
    out: dict[str, int] = {}
    con = duckdb.connect()
    try:
        for f in manifest.files:
            if f.derived_from is not None:
                continue
            rows = con.execute(
                "SELECT capture_sha8, count(*) FROM read_parquet($p) GROUP BY 1",
                {"p": str(root / f.path)},
            ).fetchall()
            for sha8, n in rows:
                m = methods.get(sha8, "unknown")
                out[m] = out.get(m, 0) + int(n)
    finally:
        con.close()
    return dict(sorted(out.items()))
