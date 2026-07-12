from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402


@pytest.fixture(autouse=True)
def current_unit_migration(monkeypatch):
    """Keep DB-less TestClient scenarios focused on their stated contract.

    PostgreSQL migration tests use direct engine connections. Unit applications
    deliberately point at port 1, so their lifespan receives the pinned current
    revision without weakening the production startup check.
    """

    async def migration_revision(_database: Database) -> str:
        return Settings.model_fields["readiness_migration"].default

    async def current_time(_database: Database) -> datetime:
        return datetime.now(UTC)

    monkeypatch.setattr(Database, "migration_revision", migration_revision)
    monkeypatch.setattr(Database, "current_time", current_time)
