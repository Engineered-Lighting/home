from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.db import Database


class SimulatedDatabaseFailure(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def failure(sqlstate: str) -> DBAPIError:
    return DBAPIError(
        "test statement",
        {},
        SimulatedDatabaseFailure(sqlstate),
        connection_invalidated=False,
    )


@pytest.mark.asyncio
async def test_serializable_operation_body_is_replayed_and_bounded() -> None:
    database = Database("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise failure("40001")
        return "committed"

    try:
        assert await database.run_serializable(operation) == "committed"
        assert attempts == 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_non_retryable_database_error_is_not_replayed() -> None:
    database = Database("postgresql+psycopg://unused:unused@127.0.0.1:1/unused")
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise failure("23505")

    try:
        with pytest.raises(DBAPIError):
            await database.run_serializable(operation)
        assert attempts == 1
    finally:
        await database.close()
