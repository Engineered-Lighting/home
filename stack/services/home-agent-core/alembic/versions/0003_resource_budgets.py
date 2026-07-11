"""Add content-free ingest accounting for locked resource budgets.

Revision ID: 0003_resource_budgets
Revises: 0002_people_privacy_cutover
Create Date: 2026-07-11
"""

from __future__ import annotations
from typing import Sequence
from alembic import op

revision: str = "0003_resource_budgets"
down_revision: str | None = "0002_people_privacy_cutover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ingest.envelopes ADD COLUMN IF NOT EXISTS "
        "payload_bytes bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_envelopes_resource_window "
        "ON ingest.envelopes (event_type, ingested_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ingest.ix_envelopes_resource_window")
    op.execute("ALTER TABLE ingest.envelopes DROP COLUMN IF EXISTS payload_bytes")
