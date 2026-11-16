"""The market-data store: root, path rules and raw captures.

Layout and naming are fixed in ARCHITECTURE.md "Data store". Everything
here is written once and never edited: a correction is a new capture.
The store is git-ignored; this repo is public and data never goes in git.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from firm.core.base import FirmModel, Id, Sha256
from firm.core.timeframes import TIMEFRAME_SECONDS

DATA_ROOT_ENV = "FIRM_DATA_ROOT"
ZONES = ("raw", "normalized", "datasets", "reference", "golden")
KINDS = frozenset({"bars", "trades", "book", "funding", "corporate_actions"})

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StoreError(ValueError):
    """A path, name or capture breaks the store rules."""


def data_root(explicit: Path | str | None = None) -> Path:
    """`explicit`, else `$FIRM_DATA_ROOT`, else `./data` (relative to the cwd)."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(DATA_ROOT_ENV)
    return Path(env) if env else Path.cwd() / "data"


def path_value(value: str) -> str:
    """A partition value: `/` becomes `-` (e.g. `BTC/USDT`), then the charset is checked."""
    v = value.replace("/", "-")
    if not _VALUE.match(v):
        raise StoreError(f"invalid path value {value!r}")
    return v


def partition(**parts: str) -> Path:
    """Hive-style `key=value/...` in the given order."""
    out = Path()
    for key, value in parts.items():
        if not _KEY.match(key):
            raise StoreError(f"invalid partition key {key!r}")
        out /= f"{key}={path_value(value)}"
    return out


def stamp(ts: datetime) -> str:
    """Compact UTC ISO timestamp for file names: 20261116T100200Z."""
    if ts.tzinfo is None:
        raise StoreError("timestamps in the store must be timezone-aware")
    return ts.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, data: bytes) -> bool:
    """Write `data` atomically. Same bytes already there: no-op. Different bytes: error."""
    if path.exists():
        if path.read_bytes() != data:
            raise StoreError(f"refusing to overwrite {path} with different content")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def canonical_json(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()


# --------------------------------------------------------------------------- captures

CaptureMethod = Literal["api", "legacy_import", "synthetic"]


class CaptureMeta(FirmModel):
    """Sidecar of one raw capture (`<stamp>_<sha8>.meta.json`)."""

    provider: Id
    via: Id
    provider_symbol: Id
    kind: Id
    tf: Id | None
    format: Id
    request: dict[str, Any] = Field(default_factory=dict)
    capture_method: CaptureMethod
    captured_at: AwareDatetime | None  # None only for legacy imports (unknown)
    sha256: Sha256
    bytes: int
    price_adjustment: Literal["none", "split_adjusted", "split_and_dividend_adjusted"] = "none"
    licence: str
    tag: Literal["private"] = "private"
    imported_from: str | None = None
    # legacy imports only
    imported_at: str | None = None
    source_mtime: str | None = None
    rows: int | None = None


class Capture(FirmModel):
    path: Path
    meta: CaptureMeta

    def read(self) -> bytes:
        data = self.path.read_bytes()
        if sha256_bytes(data) != self.meta.sha256:
            raise StoreError(f"{self.path}: checksum mismatch")
        return data


def write_capture(
    root: Path,
    payload: bytes,
    *,
    provider: str,
    kind: str,
    provider_symbol: str,
    tf: str | None,
    fmt: str,
    captured_at: datetime,
    request: dict[str, Any],
    via: str,
    licence: str,
    capture_method: CaptureMethod = "api",
    price_adjustment: Literal["none", "split_adjusted", "split_and_dividend_adjusted"] = "none",
) -> Capture:
    """Store one provider response exactly as received, plus its `.meta.json`."""
    if kind not in KINDS:
        raise StoreError(f"unknown kind {kind!r}")
    if tf is not None and tf not in TIMEFRAME_SECONDS:
        raise StoreError(f"unknown timeframe {tf!r}")
    sha = sha256_bytes(payload)
    parts = {"provider": provider, "kind": kind, "symbol": provider_symbol}
    if tf is not None:
        parts["tf"] = tf
    folder = root / "raw" / partition(**parts)
    # The name's hash covers payload AND request: two requests in the same second
    # can return identical bytes (e.g. an empty page) and must not collide.
    # `sha256` in the meta stays the payload's own checksum.
    ident = sha256_bytes(payload + b"\0" + canonical_json(request))
    name = f"{stamp(captured_at)}_{ident[:8]}"
    meta = CaptureMeta(
        provider=provider,
        via=via,
        provider_symbol=provider_symbol,
        kind=kind,
        tf=tf,
        format=fmt,
        request=request,
        capture_method=capture_method,
        captured_at=captured_at,
        sha256=sha,
        bytes=len(payload),
        price_adjustment=price_adjustment,
        licence=licence,
    )
    data_path = folder / f"{name}.{path_value(fmt)}"
    write_once(data_path, payload)
    write_once(folder / f"{name}.meta.json", canonical_json(meta.model_dump(mode="json")))
    return Capture(path=data_path, meta=meta)


def iter_captures(
    root: Path,
    *,
    provider: str | None = None,
    kind: str | None = None,
    provider_symbol: str | None = None,
    tf: str | None = None,
) -> Iterator[Capture]:
    """Captures matching the filters, ordered by file name (capture time, then hash)."""
    base = root / "raw"
    pattern = "/".join(
        f"{key}={path_value(value) if value is not None else '*'}"
        for key, value in (
            ("provider", provider),
            ("kind", kind),
            ("symbol", provider_symbol),
        )
    )
    metas = sorted(base.glob(f"{pattern}/**/*.meta.json"))
    for meta_path in metas:
        meta = CaptureMeta.model_validate_json(meta_path.read_text())
        if tf is not None and meta.tf != tf:
            continue
        stem = meta_path.name.removesuffix(".meta.json")
        data_path = meta_path.with_name(f"{stem}.{path_value(meta.format)}")
        yield Capture(path=data_path, meta=meta)
