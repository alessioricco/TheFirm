"""The ledger: append-only, hash-chained, idempotent on event IDs, one writer."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from firm.core.domain import LedgerEventType as T
from firm.ledger.store import Ledger, LedgerError
from firm.ledger.writer import LedgerWriter

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _clock():
    t = [T0]

    def now() -> datetime:
        t[0] += timedelta(seconds=1)
        return t[0]

    return now


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite", clock=_clock())


def test_append_chains_and_reads_back(ledger: Ledger) -> None:
    a = ledger.append(T.VERDICT, "first", {"x": 1})
    b = ledger.append(T.RISK_DECISION, "second", {"y": [1, 2.5]})
    assert (a.sequence, a.prev_hash) == (0, None)
    assert (b.sequence, b.prev_hash) == (1, a.hash)
    assert [e.event_id for e in ledger.read()] == [a.event_id, b.event_id]
    assert [e.sequence for e in ledger.read(1)] == [1]
    assert ledger.head() == b and ledger.count() == 2
    assert ledger.verify_chain().ok


def test_every_event_needs_a_reason(ledger: Ledger) -> None:
    with pytest.raises(ValueError):
        ledger.append(T.VERDICT, "", {})


def test_same_event_id_is_idempotent_and_conflicts_are_refused(ledger: Ledger) -> None:
    a = ledger.append(T.TRIAL, "trial", {"id": "t-1"}, event_id="trial:t-1")
    again = ledger.append(T.TRIAL, "trial", {"id": "t-1"}, event_id="trial:t-1")
    assert again == a and ledger.count() == 1
    with pytest.raises(LedgerError, match="different content"):
        ledger.append(T.TRIAL, "trial", {"id": "OTHER"}, event_id="trial:t-1")


def test_update_and_delete_are_refused_by_the_database(ledger: Ledger, tmp_path) -> None:
    ledger.append(T.VERDICT, "v", {"x": 1})
    con = sqlite3.connect(tmp_path / "ledger.sqlite")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE events SET reason = 'edited'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM events")


def test_tampering_breaks_the_chain(ledger: Ledger, tmp_path) -> None:
    for i in range(5):
        ledger.append(T.FILL, f"fill {i}", {"i": i})
    con = sqlite3.connect(tmp_path / "ledger.sqlite")
    con.execute("DROP TRIGGER events_no_update")  # an attacker with file access
    con.execute("UPDATE events SET payload = '{\"i\": 99}' WHERE seq = 2")
    con.commit()
    report = Ledger(tmp_path / "ledger.sqlite").verify_chain()
    assert not report.ok and report.first_bad_sequence == 2


def test_reopening_continues_the_chain(tmp_path) -> None:
    path = tmp_path / "ledger.sqlite"
    first = Ledger(path, clock=_clock()).append(T.VERDICT, "a", {})
    second = Ledger(path, clock=_clock()).append(T.VERDICT, "b", {})
    assert second.prev_hash == first.hash and second.sequence == 1


def test_single_writer_keeps_order_from_many_producers(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite", clock=_clock())

    async def main() -> None:
        writer = LedgerWriter(ledger)
        task = asyncio.create_task(writer.run())

        async def producer(name: str) -> None:
            for i in range(20):
                await writer.submit(T.ORDER_UPDATE, f"{name} {i}", {"p": name, "i": i})

        await asyncio.gather(*(producer(n) for n in ("crypto", "us_equities", "risk")))
        await writer.close()
        await task

    asyncio.run(main())
    events = list(ledger.read())
    assert len(events) == 60 and [e.sequence for e in events] == list(range(60))
    for name in ("crypto", "us_equities", "risk"):  # each producer's own order kept
        assert [e.payload["i"] for e in events if e.payload["p"] == name] == list(range(20))
    assert ledger.verify_chain().ok
