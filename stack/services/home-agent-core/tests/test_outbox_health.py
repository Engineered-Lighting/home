from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert

from app import schema
from app.db import Database
from app.restore import outbox_health


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)


@pytest.mark.asyncio
async def test_future_pending_auto_expiry_does_not_block_outbox_health() -> None:
    database = Database(os.environ["TEST_DATABASE_URL"])
    future_auto_id = uuid.uuid4()
    strict_erasure_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        async with database.transaction() as connection:
            await connection.execute(delete(schema.outbox))
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=future_auto_id,
                    topic="privacy.person.auto_expire",
                    aggregate_id=uuid.uuid4(),
                    payload={"version": 1},
                    state="pending",
                    available_at=now + timedelta(days=1),
                )
            )

        future = await outbox_health(database)
        assert future.ready is True
        assert future.code == "current"
        assert future.incomplete_erasure == 0

        async with database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == future_auto_id)
                .values(available_at=now - timedelta(seconds=1))
            )
        due = await outbox_health(database)
        assert due.ready is False
        assert due.code == "erasure_ledger_backlog"
        assert due.incomplete_erasure == 1

        async with database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == future_auto_id)
                .values(
                    state="running",
                    available_at=now + timedelta(days=1),
                )
            )
        running = await outbox_health(database)
        assert running.incomplete_erasure == 1

        async with database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == future_auto_id)
                .values(state="failed")
            )
        failed = await outbox_health(database)
        assert failed.code == "erasure_receipt_failed"
        assert failed.incomplete_erasure == 1
        assert failed.failed_erasure == 1

        async with database.transaction() as connection:
            await connection.execute(
                update(schema.outbox)
                .where(schema.outbox.c.outbox_id == future_auto_id)
                .values(state="complete", completed_at=now)
            )
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=strict_erasure_id,
                    topic="privacy.erasure.completed",
                    aggregate_id=uuid.uuid4(),
                    payload={"version": 1},
                    state="pending",
                    available_at=now + timedelta(days=1),
                )
            )
        strict = await outbox_health(database)
        assert strict.ready is False
        assert strict.code == "erasure_ledger_backlog"
        assert strict.incomplete_erasure == 1
    finally:
        async with database.transaction() as connection:
            await connection.execute(
                delete(schema.outbox).where(
                    schema.outbox.c.outbox_id.in_(
                        [future_auto_id, strict_erasure_id]
                    )
                )
            )
        await database.close()
