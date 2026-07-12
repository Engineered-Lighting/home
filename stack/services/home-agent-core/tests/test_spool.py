from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.spool import DisabledRuntimeSpool, EncryptedRuntimeSpool


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


def test_effective_clock_high_water_survives_rollback_and_restart(tmp_path) -> None:
    path = tmp_path / "rollback-safe.sqlite"
    ttl = timedelta(minutes=5)
    spool = EncryptedRuntimeSpool(
        path, b"s" * 32, ttl_seconds=int(ttl.total_seconds())
    )
    spool.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=1,
        observed_at=NOW,
        received_at=NOW,
        payload={"value": 1},
        metadata={"entity_id": "device_tracker.private"},
        now=NOW,
    )

    high_water = NOW + timedelta(minutes=4)
    assert spool.get(
        "edge:location", "epoch-1", 1, now=high_water
    ) is not None
    spool.close()

    reopened = EncryptedRuntimeSpool(
        path, b"s" * 32, ttl_seconds=int(ttl.total_seconds())
    )
    rollback = NOW - timedelta(days=2)
    reopened.append(
        stream_key="edge:location",
        epoch="epoch-1",
        sequence=2,
        observed_at=rollback,
        received_at=rollback,
        payload={"value": 2},
        metadata={"entity_id": "device_tracker.private"},
        now=rollback,
    )
    records = reopened.recent_for_entity(
        "device_tracker.private",
        since=rollback - timedelta(days=1),
        now=rollback,
    )
    assert [record.sequence for record in records] == [2, 1]
    assert reopened.prune(now=rollback) == 0

    with sqlite3.connect(path) as connection:
        persisted_clock = datetime.fromisoformat(
            connection.execute(
                "SELECT high_water_at FROM runtime_clock "
                "WHERE clock_key='effective_utc'"
            ).fetchone()[0]
        )
        second_expiry = datetime.fromisoformat(
            connection.execute(
                "SELECT expires_at FROM raw_observations WHERE sequence=2"
            ).fetchone()[0]
        )
    assert persisted_clock == high_water
    assert second_expiry == high_water + ttl

    after_first_expiry = NOW + timedelta(minutes=5, seconds=1)
    assert reopened.prune(now=after_first_expiry) == 1
    assert reopened.get(
        "edge:location", "epoch-1", 1, now=rollback
    ) is None
    assert reopened.delete_for_entities(
        ["device_tracker.private"], now=rollback
    ) == 1
    with sqlite3.connect(path) as connection:
        marker = connection.execute(
            "SELECT created_at, expires_at FROM gap_markers"
        ).fetchone()
    assert datetime.fromisoformat(marker[0]) == after_first_expiry
    assert datetime.fromisoformat(marker[1]) == after_first_expiry + ttl
    assert reopened.prune(now=rollback) == 0
    assert reopened.stats()["gap_markers"] == 1
    assert reopened.prune(now=after_first_expiry + ttl) == 1
    assert reopened.stats()["gap_markers"] == 0
    reopened.close()


def test_duplicate_spool_sequence_detects_conflict_and_can_be_discarded(
    tmp_path,
) -> None:
    spool = EncryptedRuntimeSpool(tmp_path / "conflict.sqlite", b"c" * 32)
    values = {
        "stream_key": "edge:location",
        "epoch": "epoch-1",
        "sequence": 1,
        "observed_at": NOW,
        "received_at": NOW,
        "payload": {"latitude": -22.4},
        "metadata": {
            "entity_id": "device_tracker.private",
            "root_observation_id": str(uuid.uuid4()),
        },
        "now": NOW,
    }

    assert spool.append(**values).inserted
    exact = spool.append(**values)
    assert not exact.inserted
    assert not exact.conflict
    changed_payload = spool.append(
        **{**values, "payload": {"latitude": -23.0}}
    )
    assert changed_payload.conflict
    changed_metadata = spool.append(
        **{**values, "metadata": {**values["metadata"], "coverage": "gap"}}
    )
    assert changed_metadata.conflict
    assert spool.discard("edge:location", "epoch-1", 1)
    assert not spool.discard("edge:location", "epoch-1", 1)
    assert spool.get("edge:location", "epoch-1", 1, now=NOW) is None
    spool.close()


def test_disabled_spool_accepts_optional_effective_clock_arguments() -> None:
    spool = DisabledRuntimeSpool()
    assert spool.prune(now=NOW) == 0
    assert spool.delete_for_entities(["person.private"], now=NOW) == 0
    assert spool.delete_for_subjects([], ["ha-private"], now=NOW) == 0
    with pytest.raises(RuntimeError, match="disabled"):
        spool.get("stream", "epoch", 1, now=NOW)


def test_revision_one_spool_bootstraps_high_water_without_extending_rows(
    tmp_path,
) -> None:
    path = tmp_path / "revision-one.sqlite"
    spool = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300)
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
    spool.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE runtime_clock")
        connection.execute("DELETE FROM schema_version WHERE version=2")

    migrated = EncryptedRuntimeSpool(path, b"s" * 32, ttl_seconds=300)
    assert migrated.get(
        "edge:location", "epoch-1", 1, now=NOW - timedelta(days=1)
    ) is not None
    with sqlite3.connect(path) as connection:
        high_water = datetime.fromisoformat(
            connection.execute(
                "SELECT high_water_at FROM runtime_clock"
            ).fetchone()[0]
        )
        versions = connection.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
    assert high_water == NOW
    assert versions == [(1,), (2,)]
    assert migrated.prune(now=NOW + timedelta(minutes=6)) == 1
    migrated.close()


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
    assert spool.delete_for_subjects([], [user_id], now=NOW) == 1
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
