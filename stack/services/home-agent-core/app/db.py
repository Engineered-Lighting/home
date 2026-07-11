from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
        )

    @asynccontextmanager
    async def transaction(
        self,
        *,
        principal_id: uuid.UUID | None = None,
        serializable: bool = False,
    ) -> AsyncIterator[AsyncConnection]:
        async with self.engine.connect() as connection:
            if serializable:
                connection = await connection.execution_options(
                    isolation_level="SERIALIZABLE"
                )
            async with connection.begin():
                if principal_id is not None:
                    await connection.execute(
                        text(
                            "SELECT set_config('app.principal_id', :principal_id, true)"
                        ),
                        {"principal_id": str(principal_id)},
                    )
                yield connection

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                return bool((await connection.execute(text("SELECT 1"))).scalar_one())
        except Exception:
            return False

    async def migration_revision(self) -> str | None:
        try:
            async with self.engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                return result.scalar_one_or_none()
        except Exception:
            return None

    async def close(self) -> None:
        await self.engine.dispose()

    async def run_serializable(self, operation, *, max_attempts: int = 3):
        """Retry a complete idempotent domain operation on serialization/deadlock.

        Domain methods generate opaque IDs and keep external effects in the
        transactional outbox, so replaying the operation after PostgreSQL rolls
        back is safe.
        """

        for attempt in range(1, max_attempts + 1):
            try:
                return await operation()
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate not in {"40001", "40P01"} or attempt == max_attempts:
                    raise
                await asyncio.sleep(random.uniform(0.01, 0.05) * attempt)
        raise RuntimeError("unreachable serialization retry state")
