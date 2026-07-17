from __future__ import annotations

import asyncio

import pytest

from app import migration_guard


class FakeDatabase:
    def __init__(self, url: str, *, revision: str | None) -> None:
        self.url = url
        self.revision = revision
        self.closed = False

    async def migration_revision(self) -> str | None:
        return self.revision

    async def close(self) -> None:
        self.closed = True


def test_revision_verification_requires_database_url(monkeypatch) -> None:
    monkeypatch.delenv("HOME_AGENT_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="requires the database URL"):
        asyncio.run(
            migration_guard.verify_exact_revision("0006a_worker_lease_arbitration")
        )


def test_exact_revision_verification_accepts_only_the_expected_revision(
    monkeypatch,
) -> None:
    database = FakeDatabase(
        "postgresql+psycopg://owner@postgres/home_agent",
        revision="0006a_worker_lease_arbitration",
    )
    monkeypatch.setenv(
        "HOME_AGENT_DATABASE_URL", "postgresql://owner@postgres/home_agent"
    )
    monkeypatch.setattr(migration_guard, "Database", lambda url: database)

    assert (
        asyncio.run(
            migration_guard.verify_exact_revision("0006a_worker_lease_arbitration")
        )
        == 0
    )
    assert database.closed is True


@pytest.mark.parametrize(
    "revision",
    [None, "0006_worker_maintenance_health", "0012_identity_person_erasure_tombstone"],
)
def test_revision_verification_rejects_missing_ancestor_or_descendant(
    monkeypatch, revision: str | None
) -> None:
    database = FakeDatabase(
        "postgresql+psycopg://owner@postgres/home_agent", revision=revision
    )
    monkeypatch.setenv(
        "HOME_AGENT_DATABASE_URL", "postgresql+psycopg://owner@postgres/home_agent"
    )
    monkeypatch.setattr(migration_guard, "Database", lambda url: database)

    with pytest.raises(SystemExit, match="rebuild descendant"):
        asyncio.run(
            migration_guard.verify_exact_revision("0006a_worker_lease_arbitration")
        )
    assert database.closed is True
