from __future__ import annotations

import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.spool import EncryptedRuntimeSpool


NOW = datetime(2026, 7, 11, 15, 0, tzinfo=UTC)


def test_spool_is_encrypted_idempotent_and_expires(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300, max_bytes=1_000_000)
    payload = {"latitude": -22.4, "longitude": -43.14, "secret": "canary-private"}
    result = spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload=payload,
        metadata={"schema_version": 1},
        now=NOW,
    )
    duplicate = spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload=payload,
        metadata={"schema_version": 1},
        now=NOW,
    )

    assert result.inserted is True
    assert duplicate.inserted is False
    assert spool.get("edge:location", "epoch-1", 1, now=NOW).payload == payload

    raw = path.read_bytes()
    assert b"canary-private" not in raw
    assert b"-22.4" not in raw

    assert (
        spool.get("edge:location", "epoch-1", 1, now=NOW + timedelta(minutes=6)) is None
    )
    spool.close()


def test_spool_overflow_drops_oldest_and_writes_gap_marker(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300, max_bytes=1_048_576)
    first = spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload={"value": "a" * 700_000},
        metadata={},
        now=NOW,
    )
    second = spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=2,
        observed_at=NOW,
        received_at=NOW + timedelta(seconds=1),
        payload={"value": "b" * 700_000},
        metadata={},
        now=NOW,
    )

    assert first.evicted or second.evicted
    assert spool.stats()["gap_markers"] >= 1
    assert spool.stats()["physical_bytes"] <= spool.max_bytes
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gap_markers").fetchone()[0] >= 1
    spool.close()


def test_spool_rolls_back_prune_insert_and_gap_marker_together(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "runtime.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300, max_bytes=1_000_000)
    spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload={"value": "original"},
        metadata={},
        now=NOW,
    )

    def fail_after_gap_marker(now):
        spool._conn.execute(
            "INSERT INTO gap_markers VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "rollback-marker",
                "edge:location",
                "epoch-1",
                2,
                "runtime_spool_overflow",
                spool._iso(now),
                spool._iso(now + spool.ttl),
            ),
        )
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(spool, "_enforce_size_locked", fail_after_gap_marker)
    with pytest.raises(RuntimeError, match="injected"):
        spool.append(
            stream_key="edge:location",
            epoch="epoch-1",
            sequence=2,
            observed_at=NOW + timedelta(minutes=6),
            received_at=NOW + timedelta(minutes=6),
            payload={"value": "replacement"},
            metadata={},
            now=NOW + timedelta(minutes=6),
        )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT sequence FROM raw_observations ORDER BY sequence"
        ).fetchall() == [(1,)]
        assert connection.execute("SELECT COUNT(*) FROM gap_markers").fetchone()[0] == 0
    spool.close()


def test_gap_markers_expire_and_are_included_in_physical_bound(tmp_path) -> None:
    path = tmp_path / "bounded.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300, max_bytes=1_048_576)
    for sequence in range(1, 8):
        spool.append(
            stream_key="edge:location",
            epoch="epoch-1",
            sequence=sequence,
            observed_at=NOW,
            received_at=NOW + timedelta(seconds=sequence),
            payload={"value": str(sequence) * 700_000},
            metadata={},
            now=NOW,
        )
        assert spool.stats()["physical_bytes"] <= spool.max_bytes

    assert spool.stats()["gap_markers"] >= 1
    spool.prune(now=NOW + timedelta(minutes=6))
    assert spool.stats()["gap_markers"] == 0
    assert spool.stats()["physical_bytes"] <= spool.max_bytes
    spool.close()


def test_spool_competing_writers_serialize_complete_operations(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite"
    first = EncryptedRuntimeSpool(
        path, b"s" * 32, ttl_seconds=300, max_bytes=10_000_000
    )
    second = EncryptedRuntimeSpool(
        path, b"s" * 32, ttl_seconds=300, max_bytes=10_000_000
    )
    barrier = threading.Barrier(2)

    def write_batch(spool, start):
        barrier.wait(timeout=5)
        return [
            spool.append(
                stream_key="edge:location",
                epoch="epoch-1",
                sequence=sequence,
                observed_at=NOW,
                received_at=NOW,
                payload={"writer_sequence": sequence},
                metadata={},
                now=NOW,
            )
            for sequence in range(start, start + 25)
        ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(write_batch, first, 1)
        right = executor.submit(write_batch, second, 26)
        results = [*left.result(timeout=10), *right.result(timeout=10)]

    assert all(result.inserted for result in results)
    assert first.stats()["records"] == 50

    duplicate_barrier = threading.Barrier(2)

    def write_duplicate(spool):
        duplicate_barrier.wait(timeout=5)
        return spool.append(
            stream_key="edge:location",
            epoch="epoch-1",
            sequence=51,
            observed_at=NOW,
            received_at=NOW,
            payload={"writer_sequence": 51},
            metadata={},
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(write_duplicate, first)
        right = executor.submit(write_duplicate, second)
        duplicate_results = [left.result(timeout=10), right.result(timeout=10)]
    assert sorted(result.inserted for result in duplicate_results) == [False, True]
    assert first.stats()["records"] == 51
    first.close()
    second.close()


def test_user_scoped_non_entity_row_is_tagged_and_erased_without_plaintext_uuid(
    tmp_path,
) -> None:
    path = tmp_path / "runtime.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300)
    user_id = "12345678-private-ha-user"
    spool.append(
        stream_key="edge:conversation",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload={"conversation": "content-discarded"},
        metadata={"schema_version": 1},
        user_scope_id=user_id,
        now=NOW,
    )

    with sqlite3.connect(path) as connection:
        metadata = connection.execute(
            "SELECT metadata_json FROM raw_observations"
        ).fetchone()[0]
    assert user_id not in metadata
    assert "user_scope_tag" in metadata
    assert spool.delete_for_subjects([], [user_id]) == 1
    assert spool.stats()["records"] == 0
    assert spool.stats()["gap_markers"] == 1
    assert user_id.encode() not in path.read_bytes()
    spool.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_spool_hardens_existing_directory_database_and_sidecars(tmp_path) -> None:
    directory = tmp_path / "broad-runtime"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    path = directory / "runtime.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32)
    spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload={"value": 1},
        metadata={},
        now=NOW,
    )

    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = directory / f"runtime.sqlite{suffix}"
        if sidecar.exists():
            assert sidecar.stat().st_mode & 0o777 == 0o600
    spool.close()
