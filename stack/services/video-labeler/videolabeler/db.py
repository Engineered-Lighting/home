"""SQLite helpers: pragmas, migrations, short transactions, wal size.

Constraints that shaped this module:
  * Two writer PROCESSES (API + job subprocess) share one db file — WAL +
    busy_timeout=10000 + BEGIN IMMEDIATE short transactions, never long ones.
  * isolation_level=None so transactions are explicit (tx()); sqlite3's
    implicit transaction management would hold locks across statements.
  * Timestamps are ISO-8601 UTC TEXT in ONE fixed format so lexicographic SQL
    comparison (the recover_stale cutoff) is correct.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def iso_at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")


def iso_now() -> str:
    return iso_at(time.time())


def connect(path) -> sqlite3.Connection:
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextlib.contextmanager
def open_db(path):
    """Per-request / per-use connection; cheap under WAL."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def tx(conn: sqlite3.Connection):
    """Short IMMEDIATE transaction — the write lock is taken up front so two
    processes never deadlock upgrading a shared lock mid-transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def apply_migrations(conn: sqlite3.Connection, migrations_dir=MIGRATIONS_DIR) -> list[str]:
    """Apply *.sql files in name order, once each (tracked in
    schema_migrations). Safe to call on every startup."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                 "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
    done: list[str] = []
    for p in sorted(Path(migrations_dir).glob("*.sql")):
        if p.name in applied:
            continue
        conn.executescript(p.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                     (p.name, iso_now()))
        done.append(p.name)
    return done


def wal_size(db_path) -> int:
    p = str(db_path) + "-wal"
    return os.path.getsize(p) if os.path.exists(p) else 0
