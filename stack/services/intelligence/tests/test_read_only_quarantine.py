from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.db import connect, init_db


def test_default_database_connection_is_physically_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "intelligence.sqlite"
    writable = connect(database)
    try:
        init_db(writable)
        writable.execute(
            "INSERT INTO raw_events(id, source, source_id, ingest_ts, source_hash, redacted_payload_json) "
            "VALUES ('fixture', 'test', 'source', 'now', 'hash', '{}')"
        )
        writable.commit()
    finally:
        writable.close()

    monkeypatch.setenv("INTELLIGENCE_DB", str(database))
    monkeypatch.setenv("INTELLIGENCE_READ_ONLY", "1")
    quarantined = connect()
    try:
        assert quarantined.execute("PRAGMA query_only").fetchone()[0] == 1
        assert quarantined.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] == 1
        init_db(quarantined)  # startup/health schema checks are no-ops
        with pytest.raises(sqlite3.OperationalError):
            quarantined.execute("DELETE FROM raw_events")
    finally:
        quarantined.close()


def test_read_only_mode_never_creates_a_missing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "missing.sqlite"
    monkeypatch.setenv("INTELLIGENCE_DB", str(database))
    monkeypatch.setenv("INTELLIGENCE_READ_ONLY", "1")
    with pytest.raises(FileNotFoundError):
        connect()
    assert not database.exists()
