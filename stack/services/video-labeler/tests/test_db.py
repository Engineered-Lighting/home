"""Migrations apply idempotently; connection pragmas are set."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402

EXPECTED_TABLES = {
    "videos", "video_artifacts", "segments", "video_label_state",
    "analysis_windows", "custom_labels", "jobs", "exclusive_state",
    "embeddings", "cluster_runs", "cluster_members", "exports", "models",
    "calibration_stats", "schema_migrations",
}


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows if not r["name"].startswith("sqlite_")}


def test_migrations_idempotent(cfg):
    conn = db.connect(cfg.db_path)
    try:
        first = db.apply_migrations(conn)
        assert first == ["0001_init.sql"]
        assert EXPECTED_TABLES <= _tables(conn)
        second = db.apply_migrations(conn)
        assert second == []
        assert EXPECTED_TABLES <= _tables(conn)
    finally:
        conn.close()


def test_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_iso_timestamps_sort_lexicographically():
    a = db.iso_at(1000.0)
    b = db.iso_at(2000.0)
    assert a < b
    assert a.endswith("+00:00")
