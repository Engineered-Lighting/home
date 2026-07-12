"""Add content-free rollout promotion authorizations.

Revision ID: 0004_rollout_authorizations
Revises: 0003_resource_budgets
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0004_rollout_authorizations"
down_revision: str | None = "0003_resource_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Revision 0001 creates current metadata on a pristine database, so this
    # revision must also be safe when the table already exists there. Explicit
    # IF NOT EXISTS keeps both online and offline Alembic output replay-safe.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operations.rollout_authorizations (
          authorization_id uuid NOT NULL,
          operator_request_id uuid NOT NULL,
          from_mode varchar(32) NOT NULL,
          to_mode varchar(32) NOT NULL,
          rule_version varchar(128) NOT NULL,
          policy_version varchar(128) NOT NULL,
          policy_digest varchar(64) NOT NULL,
          input_digest varchar(64) NOT NULL,
          readiness_evaluated_at timestamptz NOT NULL,
          authorized_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
          CONSTRAINT pk_rollout_authorizations PRIMARY KEY (authorization_id),
          CONSTRAINT uq_rollout_authorizations_operator_request_id
            UNIQUE (operator_request_id),
          CONSTRAINT rollout_transition_once UNIQUE (from_mode, to_mode),
          CONSTRAINT ck_rollout_authorizations_rollout_forward_transition CHECK (
            from_mode = 'record_only' AND to_mode = 'shadow'
          ),
          CONSTRAINT ck_rollout_authorizations_rollout_policy_digest CHECK (
            policy_digest ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT ck_rollout_authorizations_rollout_input_digest CHECK (
            input_digest ~ '^[0-9a-f]{64}$'
          )
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW operations.phase2_rollout_evidence
        WITH (security_barrier = true)
        AS
        SELECT
          envelope_id,
          stream_id,
          sequence,
          event_type,
          coverage,
          ingested_at,
          dependency_domain,
          metadata ->> 'raw_payload_status' AS raw_payload_status
        FROM ingest.envelopes
        WHERE event_type IN ('state_changed', 'location_fix')
          AND coverage IN ('continuous', 'recorder_reconstructed')
          AND dependency_domain = 'suppressed.precise_location'
          AND entity_id IS NULL
          AND source_event_id IS NULL
          AND ha_context = '{}'::jsonb
          AND left(metadata ->> 'raw_payload_status', 11) = 'suppressed_'
        OFFSET 0
        """
    )
    op.execute(
        "REVOKE ALL ON operations.phase2_rollout_evidence FROM PUBLIC"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ingest.guard_envelope_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
          IF current_user NOT IN ('home_agent_owner', 'home_agent_erasure') THEN
            RAISE EXCEPTION 'ingest.envelopes is append-only'
              USING ERRCODE = '42501';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION ingest.guard_envelope_immutability() FROM PUBLIC"
    )
    op.execute("DROP TRIGGER IF EXISTS guard_envelope_immutability ON ingest.envelopes")
    op.execute(
        """
        CREATE TRIGGER guard_envelope_immutability
        BEFORE UPDATE OR DELETE ON ingest.envelopes
        FOR EACH ROW EXECUTE FUNCTION ingest.guard_envelope_immutability()
        """
    )
    op.execute("DROP TRIGGER IF EXISTS guard_envelope_truncate ON ingest.envelopes")
    op.execute(
        """
        CREATE TRIGGER guard_envelope_truncate
        BEFORE TRUNCATE ON ingest.envelopes
        FOR EACH STATEMENT EXECUTE FUNCTION ingest.guard_envelope_immutability()
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS operations.phase2_rollout_evidence")
    op.execute("DROP TRIGGER IF EXISTS guard_envelope_truncate ON ingest.envelopes")
    op.execute("DROP TRIGGER IF EXISTS guard_envelope_immutability ON ingest.envelopes")
    op.execute("DROP FUNCTION IF EXISTS ingest.guard_envelope_immutability()")
    op.execute("DROP TABLE IF EXISTS operations.rollout_authorizations")
