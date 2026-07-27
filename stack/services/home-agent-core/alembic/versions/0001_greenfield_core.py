"""Create the greenfield home-agent authority.

Revision ID: 0001_greenfield_core
Revises:
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

from app.schema import metadata

revision: str = "0001_greenfield_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = (
    "ingest",
    "identity",
    "knowledge",
    "engagement",
    "privacy",
    "operations",
    "media",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    metadata.create_all(bind=op.get_bind(), checkfirst=False)

    # Reject direct and transitive lineage cycles at the authority boundary.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ingest.reject_artifact_link_cycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            WITH RECURSIVE descendants(artifact_id) AS (
              SELECT NEW.child_artifact_id
              UNION
              SELECT l.child_artifact_id
                FROM ingest.artifact_links l
                JOIN descendants d ON l.parent_artifact_id = d.artifact_id
            )
            SELECT 1 FROM descendants WHERE artifact_id = NEW.parent_artifact_id
          ) THEN
            RAISE EXCEPTION 'artifact lineage cycle' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER artifact_link_acyclic
        BEFORE INSERT OR UPDATE ON ingest.artifact_links
        FOR EACH ROW EXECUTE FUNCTION ingest.reject_artifact_link_cycle();

        REVOKE ALL ON FUNCTION ingest.reject_artifact_link_cycle()
          FROM PUBLIC;
        """
    )

    # The application sets app.principal_id inside every user transaction.
    # Table owners bypass RLS; production runtime roles must not own tables.
    rls_policies = {
        "knowledge.places": "owner_principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "knowledge.place_locators": "EXISTS (SELECT 1 FROM knowledge.places p WHERE p.place_id = place_locators.place_id AND p.owner_principal_id = nullif(current_setting('app.principal_id', true), '')::uuid)",
        "knowledge.visits": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "knowledge.memory_transactions": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "knowledge.fact_versions": "perspective_principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "knowledge.fact_support": "EXISTS (SELECT 1 FROM knowledge.fact_versions f WHERE f.fact_version_id = fact_support.fact_version_id AND f.perspective_principal_id = nullif(current_setting('app.principal_id', true), '')::uuid)",
        "identity.confirmation_artifacts": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "engagement.preferences": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "engagement.initiatives": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "privacy.artifact_registry": "owner_principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "privacy.erasure_requests": "principal_id = nullif(current_setting('app.principal_id', true), '')::uuid",
        "privacy.erasure_tasks": "EXISTS (SELECT 1 FROM privacy.erasure_requests r WHERE r.erasure_request_id = erasure_tasks.erasure_request_id AND r.principal_id = nullif(current_setting('app.principal_id', true), '')::uuid)",
    }
    for table_name, expression in rls_policies.items():
        policy_name = table_name.replace(".", "_") + "_principal"
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy_name} ON {table_name} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
    op.execute("DROP FUNCTION IF EXISTS ingest.reject_artifact_link_cycle() CASCADE")
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
