"""The ledger: the append-only, hash-chained record of everything (golden rule 6).

SQLite in WAL mode. The database itself refuses UPDATE and DELETE (triggers),
and every event's hash covers its content and the previous event's hash, so
an edit made around the triggers (e.g. with direct file access) breaks the
chain and `verify_chain()` says where. There is no update or delete in this
API. Appends are idempotent on `event_id`: the same ID with the same content
returns the stored event; the same ID with different content is refused.

One process writes (see `writer.LedgerWriter`); many may read.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firm.core.domain import LedgerEvent, LedgerEventType

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    prev_hash TEXT,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: no UPDATE'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: no DELETE'); END;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class LedgerError(RuntimeError):
    """The ledger refused an operation or found itself inconsistent."""


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    events: int
    first_bad_sequence: int | None = None
    detail: str = ""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(
    *,
    sequence: int,
    event_id: str,
    event_type: str,
    reason: str,
    payload_json: str,
    recorded_at: str,
    prev_hash: str | None,
) -> str:
    body = _canonical(
        {
            "seq": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "reason": reason,
            "payload": payload_json,
            "recorded_at": recorded_at,
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class Ledger:
    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] = _now) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._con = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.executescript(_SCHEMA)
        found = self._con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if found is None:
            self._con.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
        elif found[0] != SCHEMA_VERSION:
            raise LedgerError(f"ledger schema {found[0]}, this code expects {SCHEMA_VERSION}")

    def close(self) -> None:
        self._con.close()

    @staticmethod
    def _row(row: tuple[Any, ...]) -> LedgerEvent:
        seq, event_id, event_type, reason, payload, recorded_at, prev_hash, h = row
        return LedgerEvent(
            sequence=seq,
            event_id=event_id,
            event_type=LedgerEventType(event_type),
            reason=reason,
            payload=json.loads(payload),
            recorded_at=datetime.fromisoformat(recorded_at),
            prev_hash=prev_hash,
            hash=h,
        )

    _COLS = "seq, event_id, event_type, reason, payload, recorded_at, prev_hash, hash"

    def append(
        self,
        event_type: LedgerEventType,
        reason: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        at: datetime | None = None,
    ) -> LedgerEvent:
        if not reason or not reason.strip():
            raise ValueError("every ledger event needs a reason")
        event_type = LedgerEventType(event_type)
        payload_json = _canonical(payload)
        eid = event_id or uuid.uuid4().hex
        con = self._con
        con.execute("BEGIN IMMEDIATE")
        try:
            existing = con.execute(
                f"SELECT {self._COLS} FROM events WHERE event_id = ?", (eid,)
            ).fetchone()
            if existing is not None:
                con.execute("COMMIT")
                stored = self._row(existing)
                if (stored.event_type, stored.reason, _canonical(stored.payload)) != (
                    event_type,
                    reason,
                    payload_json,
                ):
                    raise LedgerError(f"event {eid} already recorded with different content")
                return stored
            head = con.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (0, None) if head is None else (head[0] + 1, head[1])
            recorded_at = (at or self._clock()).astimezone(UTC).isoformat()
            h = event_hash(
                sequence=seq,
                event_id=eid,
                event_type=event_type.value,
                reason=reason,
                payload_json=payload_json,
                recorded_at=recorded_at,
                prev_hash=prev,
            )
            con.execute(
                f"INSERT INTO events ({self._COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, eid, event_type.value, reason, payload_json, recorded_at, prev, h),
            )
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        return LedgerEvent(
            sequence=seq,
            event_id=eid,
            event_type=event_type,
            reason=reason,
            payload=json.loads(payload_json),
            recorded_at=datetime.fromisoformat(recorded_at),
            prev_hash=prev,
            hash=h,
        )

    def read(self, from_sequence: int = 0) -> Iterator[LedgerEvent]:
        cur = self._con.execute(
            f"SELECT {self._COLS} FROM events WHERE seq >= ? ORDER BY seq", (from_sequence,)
        )
        for row in cur:
            yield self._row(row)

    def head(self) -> LedgerEvent | None:
        row = self._con.execute(
            f"SELECT {self._COLS} FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self._row(row)

    def count(self) -> int:
        return int(self._con.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def verify_chain(self) -> ChainReport:
        prev: str | None = None
        n = 0
        cur = self._con.execute(
            "SELECT seq, event_id, event_type, reason, payload, recorded_at, prev_hash, hash "
            "FROM events ORDER BY seq"
        )
        for seq, eid, etype, reason, payload, recorded_at, prev_hash, h in cur:
            if seq != n:
                return ChainReport(False, n, seq, f"gap: expected sequence {n}, found {seq}")
            if prev_hash != prev:
                return ChainReport(False, n, seq, "prev_hash does not match the previous event")
            expected = event_hash(
                sequence=seq,
                event_id=eid,
                event_type=etype,
                reason=reason,
                payload_json=payload,
                recorded_at=recorded_at,
                prev_hash=prev,
            )
            if expected != h:
                return ChainReport(False, n, seq, "content does not match its hash")
            prev, n = h, n + 1
        return ChainReport(True, n)
