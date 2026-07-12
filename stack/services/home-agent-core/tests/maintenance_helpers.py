from __future__ import annotations

import uuid

from sqlalchemy import delete, insert, text

from app import schema
from app.db import Database


ZERO_RETENTION_RECEIPT = {
    "proposals_expired": 0,
    "staged_requests_expired": 0,
    "pending_requests_expired": 0,
    "requests_expired": 0,
    "proposals_purged": 0,
    "requests_purged": 0,
}


async def seed_current_worker_maintenance(database: Database) -> uuid.UUID:
    """Install one content-free current worker proof for domain integration tests."""

    worker_instance_id = uuid.uuid4()
    async with database.transaction() as connection:
        database_started_at, operation_time = (
            await connection.execute(
                text("SELECT pg_postmaster_start_time(), clock_timestamp()")
            )
        ).one()
        await connection.execute(delete(schema.worker_maintenance_state))
        await connection.execute(
            insert(schema.worker_maintenance_state).values(
                state_key="durable_worker",
                worker_instance_id=worker_instance_id,
                database_started_at=database_started_at,
                kernel_version="worker-maintenance-cycle-v1",
                state="running",
                started_at=operation_time,
                heartbeat_at=operation_time,
                heartbeat_sequence=1,
                maintenance_attempted_at=operation_time,
                maintenance_succeeded_at=operation_time,
                success_sequence=1,
                consecutive_failures=0,
                last_error_code=None,
                stopped_at=None,
                spool_rows_pruned=0,
                binding_retention_receipt=ZERO_RETENTION_RECEIPT,
                updated_at=operation_time,
            )
        )
    return worker_instance_id


async def clear_worker_maintenance(database: Database) -> None:
    async with database.transaction() as connection:
        await connection.execute(delete(schema.worker_maintenance_state))
