"""Ledger: the append-only, hash-chained record of everything (golden rule 6).

SQLite in WAL mode, one writer fed by a queue. Nothing is edited or deleted.
The trade journal is rebuilt from it. Milestone 2.
"""
