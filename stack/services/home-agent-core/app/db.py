from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

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
        ha_user_id: str | None = None,
        binding_operator: bool = False,
        serializable: bool = False,
    ) -> AsyncIterator[AsyncConnection]:
        if binding_operator and (ha_user_id is not None or principal_id is not None):
            raise ValueError(
                "subject and binding-operator database scopes are mutually exclusive"
            )
        async with self.engine.connect() as connection:
            if serializable:
                connection = await connection.execution_options(
                    isolation_level="SERIALIZABLE"
                )
            async with connection.begin():
                if binding_operator:
                    # Custom GUCs are caller-controlled and therefore cannot grant
                    # operator authority.  session_user is the authenticated login
                    # role and is not changed by SECURITY DEFINER functions.  The
                    # owner exception exists only for migrations and integration
                    # tests; production API configuration supplies the isolated
                    # home_agent_binding_operator credential.
                    session_user = (
                        await connection.execute(text("SELECT session_user"))
                    ).scalar_one()
                    if session_user not in {
                        "home_agent_binding_operator",
                        "home_agent_owner",
                    }:
                        raise PermissionError(
                            "database session is not the principal-binding operator"
                        )
                if principal_id is not None:
                    await connection.execute(
                        text(
                            "SELECT set_config('app.principal_id', :principal_id, true)"
                        ),
                        {"principal_id": str(principal_id)},
                    )
                if ha_user_id is not None:
                    if (
                        not 1 <= len(ha_user_id) <= 64
                        or any(ord(character) < 32 for character in ha_user_id)
                    ):
                        raise ValueError("HA user ID is invalid for transaction scope")
                    await connection.execute(
                        text("SELECT set_config('app.ha_user_id', :ha_user_id, true)"),
                        {"ha_user_id": ha_user_id},
                    )
                yield connection

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                return bool((await connection.execute(text("SELECT 1"))).scalar_one())
        except Exception:
            return False

    async def current_time(self) -> datetime:
        """Return PostgreSQL's wall clock for cross-process safety boundaries."""

        async with self.transaction() as connection:
            return (
                await connection.execute(text("SELECT clock_timestamp()"))
            ).scalar_one()

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
