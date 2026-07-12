from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from app import schema
from app.config import Settings
from app.db import Database
from app.maintenance import WorkerMaintenanceStatus
from app.models import IngestBatch, IngestEnvelope
from app.spool import EncryptedRuntimeSpool
from app.store import CoreStore, stable_observation_root


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for transactional ingest assertions",
)


class SequencedMaintenance:
    def __init__(self, *codes: str) -> None:
        self.statuses = [
            WorkerMaintenanceStatus(code)  # type: ignore[arg-type]
            for code in codes
        ]
        self.calls: list[dict] = []

    async def inspect_in_transaction(self, _connection, **kwargs):
        self.calls.append(kwargs)
        if not self.statuses:
            raise AssertionError("unexpected maintenance inspection")
        return self.statuses.pop(0)


def envelope(
    *,
    edge_instance_id: str,
    epoch: uuid.UUID,
    sequence: int,
    payload_value: int,
) -> IngestEnvelope:
    observed_at = datetime.now(UTC) - timedelta(seconds=1)
    return IngestEnvelope(
        edge_instance_id=edge_instance_id,
        source_name="home_assistant",
        epoch=epoch,
        sequence=sequence,
        event_type="conversation_turn",
        entity_id="sensor.private_test",
        source_observed_at=observed_at,
        edge_received_at=observed_at,
        payload={"value": payload_value},
        dependency_domain="home_assistant:conversation",
        coverage="continuous",
        metadata={"source_platform": "home_assistant"},
    )


@pytest.mark.asyncio
async def test_ingest_rechecks_each_raw_write_and_reconciles_orphan_conflict(
    tmp_path,
) -> None:
    database = Database(os.environ["TEST_DATABASE_URL"])
    runtime_key = base64.urlsafe_b64encode(b"g" * 32).decode().rstrip("=")
    knowledge_key = base64.urlsafe_b64encode(b"h" * 32).decode().rstrip("=")
    settings = Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        runtime_spool_path=tmp_path / "gating.sqlite",
        runtime_spool_key=SecretStr(runtime_key),
        knowledge_encryption_key=SecretStr(knowledge_key),
        edge_token=SecretStr("edge-token-with-at-least-32-characters"),
        policy_digest="a" * 64,
        role="ingest",
        rollout_mode="record_only",
    )
    spool = EncryptedRuntimeSpool(settings.runtime_spool_path, b"g" * 32)
    store = CoreStore(database, spool, settings)
    store.maintenance_observed_after = datetime.now(UTC) - timedelta(minutes=1)
    edge_prefix = f"worker-gate-{uuid.uuid4().hex}"

    async def allow_raw(_connection, _envelope, *, precise_location):
        del precise_location
        return True, "retained_runtime_spool", False

    store._retention_decision = allow_raw  # type: ignore[method-assign]
    try:
        batch_epoch = uuid.uuid4()
        first = envelope(
            edge_instance_id=f"{edge_prefix}-batch",
            epoch=batch_epoch,
            sequence=1,
            payload_value=1,
        )
        second = envelope(
            edge_instance_id=f"{edge_prefix}-batch",
            epoch=batch_epoch,
            sequence=2,
            payload_value=2,
        )
        transition = SequencedMaintenance("current", "heartbeat_stale")
        store.maintenance_inspector = transition  # type: ignore[assignment]

        result = await store.ingest(IngestBatch(envelopes=[first, second]))

        assert result.accepted == 2
        assert len(transition.calls) == 2
        assert all(
            call == {"observed_after": store.maintenance_observed_after}
            for call in transition.calls
        )
        assert spool.get(
            f"{edge_prefix}-batch:home_assistant", str(batch_epoch), 1
        ) is not None
        assert spool.get(
            f"{edge_prefix}-batch:home_assistant", str(batch_epoch), 2
        ) is None

        async with database.transaction() as connection:
            batch_headers = (
                (
                    await connection.execute(
                        select(
                            schema.envelopes.c.sequence,
                            schema.envelopes.c.metadata,
                        )
                        .select_from(
                            schema.envelopes.join(
                                schema.source_streams,
                                schema.envelopes.c.stream_id
                                == schema.source_streams.c.stream_id,
                            )
                        )
                        .where(
                            schema.source_streams.c.edge_instance_id
                            == f"{edge_prefix}-batch"
                        )
                        .order_by(schema.envelopes.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
        assert [row["metadata"]["raw_payload_status"] for row in batch_headers] == [
            "retained_runtime_spool",
            "suppressed_retention_worker_unavailable",
        ]

        replay_epoch = uuid.uuid4()
        replay = envelope(
            edge_instance_id=f"{edge_prefix}-replay",
            epoch=replay_epoch,
            sequence=1,
            payload_value=3,
        )
        store.maintenance_inspector = SequencedMaintenance(
            "missing", "current", "current"
        )  # type: ignore[assignment]
        suppressed = await store.ingest(IngestBatch(envelopes=[replay]))
        duplicate = await store.ingest(IngestBatch(envelopes=[replay]))
        changed = await store.ingest(
            IngestBatch(
                envelopes=[replay.model_copy(update={"payload": {"value": 4}})]
            )
        )
        assert suppressed.accepted == 1
        assert duplicate.duplicates == 1
        assert changed.quarantined == 1
        assert spool.get(
            f"{edge_prefix}-replay:home_assistant", str(replay_epoch), 1
        ) is None

        orphan_epoch = uuid.uuid4()
        orphan = envelope(
            edge_instance_id=f"{edge_prefix}-orphan",
            epoch=orphan_epoch,
            sequence=1,
            payload_value=5,
        )
        orphan_stream = f"{edge_prefix}-orphan:home_assistant"
        spool.append(
            stream_key=orphan_stream,
            epoch=str(orphan_epoch),
            sequence=1,
            observed_at=orphan.source_observed_at,
            received_at=orphan.edge_received_at,
            payload=orphan.payload,
            metadata={
                "entity_id": orphan.entity_id,
                "root_observation_id": str(stable_observation_root(orphan)),
                "coverage": orphan.coverage,
                "source_platform": "home_assistant",
            },
        )
        store.maintenance_inspector = SequencedMaintenance(
            "current"
        )  # type: ignore[assignment]
        conflict = await store.ingest(
            IngestBatch(
                envelopes=[orphan.model_copy(update={"payload": {"value": 6}})]
            )
        )
        assert conflict.accepted == 0
        assert conflict.quarantined == 1
        assert len(conflict.opened_gaps) == 1
        assert conflict.acknowledgements[orphan_stream] == 1
        assert spool.get(orphan_stream, str(orphan_epoch), 1) is None

        async with database.transaction() as connection:
            stream_id = (
                await connection.execute(
                    select(schema.source_streams.c.stream_id).where(
                        schema.source_streams.c.edge_instance_id
                        == f"{edge_prefix}-orphan"
                    )
                )
            ).scalar_one()
            assert (
                await connection.execute(
                    select(schema.envelopes.c.envelope_id).where(
                        schema.envelopes.c.stream_id == stream_id
                    )
                )
            ).scalar_one_or_none() is None
            assert (
                await connection.execute(
                    select(schema.quarantine.c.reason_code).where(
                        schema.quarantine.c.edge_instance_id
                        == f"{edge_prefix}-orphan"
                    )
                )
            ).scalar_one() == "runtime_spool_payload_conflict"
            assert (
                await connection.execute(
                    select(schema.coverage_intervals.c.reason_code).where(
                        schema.coverage_intervals.c.stream_id == stream_id
                    )
                )
            ).scalar_one() == "runtime_spool_payload_conflict"
    finally:
        async with database.transaction() as connection:
            stream_ids = (
                await connection.execute(
                    select(schema.source_streams.c.stream_id).where(
                        schema.source_streams.c.edge_instance_id.like(
                            f"{edge_prefix}%"
                        )
                    )
                )
            ).scalars().all()
            if stream_ids:
                await connection.execute(
                    delete(schema.coverage_intervals).where(
                        schema.coverage_intervals.c.stream_id.in_(stream_ids)
                    )
                )
                await connection.execute(
                    delete(schema.envelopes).where(
                        schema.envelopes.c.stream_id.in_(stream_ids)
                    )
                )
                await connection.execute(
                    delete(schema.source_streams).where(
                        schema.source_streams.c.stream_id.in_(stream_ids)
                    )
                )
            await connection.execute(
                delete(schema.quarantine).where(
                    schema.quarantine.c.edge_instance_id.like(f"{edge_prefix}%")
                )
            )
        spool.close()
        await database.close()
