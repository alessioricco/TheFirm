"""Instrument master: firm IDs, provider symbols, listing and delisting dates.

`reference/instruments/venue=<v>/instrument=<id>/<stamp>_<sha8>.json`, one
immutable file per version. Delisted instruments are never removed; their
status is evaluated *as of* a time, so a later delisting can't leak into an
earlier query (survivorship-free and point-in-time).

Firm instrument IDs: crypto `BASE-QUOTE` (`BTC-USD`), shares `TICKER.MIC`
(`NVDA.XNAS`).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, model_validator

from firm.core.base import FirmModel, Id
from firm.data.store import canonical_json, partition, sha256_bytes, stamp, write_once

_CRYPTO_ID = re.compile(r"^[A-Z0-9]{2,10}-[A-Z0-9]{2,10}$")
_SHARE_ID = re.compile(r"^[A-Z0-9.\-]{1,12}\.X[A-Z]{3}$")


class InstrumentRecord(FirmModel):
    venue: Id
    instrument: Id
    asset_class: Literal["crypto", "equity"]
    base: Id
    quote: Id
    provider_symbols: dict[Id, Id]  # provider -> symbol as the provider spells it
    listed_at: AwareDatetime
    delisted_at: AwareDatetime | None = None
    known_at: AwareDatetime  # when this version of the record was written

    @model_validator(mode="after")
    def _check(self) -> Self:
        pattern = _CRYPTO_ID if self.asset_class == "crypto" else _SHARE_ID
        if not pattern.match(self.instrument):
            raise ValueError(f"instrument id {self.instrument!r} breaks the naming rule")
        if self.delisted_at is not None and self.delisted_at <= self.listed_at:
            raise ValueError("delisted_at must be after listed_at")
        return self

    def status(self, as_of: datetime) -> Literal["not_listed", "listed", "delisted"]:
        if as_of < self.listed_at:
            return "not_listed"
        if self.delisted_at is not None and as_of >= self.delisted_at:
            return "delisted"
        return "listed"


def instrument_path(root: Path, rec: InstrumentRecord) -> Path:
    data = canonical_json(rec.model_dump(mode="json"))
    folder = (
        root / "reference" / "instruments" / partition(venue=rec.venue, instrument=rec.instrument)
    )
    return folder / f"{stamp(rec.known_at)}_{sha256_bytes(data)[:8]}.json"


def write_instrument(root: Path, rec: InstrumentRecord) -> Path:
    path = instrument_path(root, rec)
    write_once(path, canonical_json(rec.model_dump(mode="json")))
    return path


def latest_instruments(root: Path) -> dict[tuple[str, str], tuple[Path, InstrumentRecord]]:
    """Latest version of every instrument (by `known_at`, then file name)."""
    out: dict[tuple[str, str], tuple[Path, InstrumentRecord]] = {}
    for path in sorted((root / "reference" / "instruments").glob("*/*/*.json")):
        rec = InstrumentRecord.model_validate_json(path.read_text())
        key = (rec.venue, rec.instrument)
        if key not in out or rec.known_at >= out[key][1].known_at:
            out[key] = (path, rec)
    return out


def resolve(
    records: dict[tuple[str, str], tuple[Path, InstrumentRecord]], provider: str, symbol: str
) -> InstrumentRecord:
    """Provider symbol -> instrument record. Exactly one match or an error."""
    hits = [r for _, r in records.values() if r.provider_symbols.get(provider) == symbol]
    if len(hits) != 1:
        raise LookupError(f"{provider}:{symbol} maps to {len(hits)} instruments")
    return hits[0]
