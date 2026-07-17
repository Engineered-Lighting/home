"""Make worker-maintenance registration non-preemptive while a lease is fresh.

Revision ID: 0006a_worker_lease_arbitration
Revises: 0006_worker_maintenance_health
Create Date: 2026-07-17

This is a deployable runtime hotfix between the live revision 0006 pin and the
dormant Phase 3 schema groundwork.  It prevents a second worker using the same
runtime role from repeatedly replacing a healthy singleton lease.  PostgreSQL
restart recovery remains independent: the state table is unlogged and a row
from a different postmaster start is always replaceable.

The observed July 2026 restart incident was caused by PostgreSQL repeatedly
recovering after reaping a detached archive helper, not by competing workers.
This revision limits secondary churn and closes the split-brain registration
path; it is not an attribution of that incident's root cause.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0006a_worker_lease_arbitration"
down_revision: str | None = "0006_worker_maintenance_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WORKER_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
)
FRESH_LEASE_SECONDS = 45

_FRESH_OWNER_GUARD = f"""\
          IF FOUND
             AND existing_database_start = database_start
             AND existing_state <> 'stopping'
             AND existing_heartbeat_at >=
                 operation_time - interval '{FRESH_LEASE_SECONDS} seconds' THEN
            RETURN false;
          END IF;
"""

_REGISTER_FUNCTION_TEMPLATE = """
        CREATE OR REPLACE FUNCTION operations.register_worker_maintenance(
          target_worker_instance_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          operation_time timestamptz;
          database_start timestamptz;
          existing_instance uuid;
          existing_database_start timestamptz;
          existing_state text;
          existing_heartbeat_at timestamptz;
        BEGIN
          IF session_user <> 'home_agent_worker' THEN
            RAISE EXCEPTION 'worker maintenance role required'
              USING ERRCODE = '42501';
          END IF;
          IF target_worker_instance_id IS NULL
             OR substring(target_worker_instance_id::text from 15 for 1)
                NOT IN ('4','7')
             OR substring(target_worker_instance_id::text from 20 for 1)
                NOT IN ('8','9','a','b') THEN
            RAISE EXCEPTION 'invalid worker instance identifier'
              USING ERRCODE = '22023';
          END IF;
          PERFORM pg_advisory_xact_lock(7210202606);
          operation_time := clock_timestamp();
          database_start := pg_postmaster_start_time();
          SELECT worker_instance_id, database_started_at, state, heartbeat_at
            INTO existing_instance, existing_database_start, existing_state,
                 existing_heartbeat_at
            FROM operations.worker_maintenance_state
           WHERE state_key = 'durable_worker'
           FOR UPDATE;
          IF FOUND
             AND existing_instance = target_worker_instance_id
             AND existing_database_start = database_start THEN
            RETURN existing_state <> 'stopping';
          END IF;
{fresh_owner_guard}
          INSERT INTO operations.worker_maintenance_state(
            state_key, worker_instance_id, database_started_at,
            kernel_version, state, started_at, heartbeat_at,
            heartbeat_sequence, maintenance_attempted_at,
            maintenance_succeeded_at, success_sequence,
            consecutive_failures, last_error_code, stopped_at,
            spool_rows_pruned, binding_retention_receipt, updated_at
          ) VALUES (
            'durable_worker', target_worker_instance_id, database_start,
            'worker-maintenance-cycle-v1', 'starting', operation_time,
            operation_time, 1, NULL, NULL, 0, 0, NULL, NULL, NULL, NULL,
            operation_time
          )
          ON CONFLICT (state_key) DO UPDATE SET
            worker_instance_id = excluded.worker_instance_id,
            database_started_at = excluded.database_started_at,
            kernel_version = excluded.kernel_version,
            state = excluded.state,
            started_at = excluded.started_at,
            heartbeat_at = excluded.heartbeat_at,
            heartbeat_sequence = excluded.heartbeat_sequence,
            maintenance_attempted_at = NULL,
            maintenance_succeeded_at = NULL,
            success_sequence = 0,
            consecutive_failures = 0,
            last_error_code = NULL,
            stopped_at = NULL,
            spool_rows_pruned = NULL,
            binding_retention_receipt = NULL,
            updated_at = excluded.updated_at;
          RETURN true;
        END;
        $$
"""


def _install_registration_function(*, reject_fresh_contender: bool) -> None:
    op.execute(
        _REGISTER_FUNCTION_TEMPLATE.format(
            fresh_owner_guard=(_FRESH_OWNER_GUARD if reject_fresh_contender else "")
        )
    )
    roles = ", ".join(WORKER_ROLES)
    op.execute(
        "REVOKE ALL ON FUNCTION operations.register_worker_maintenance(uuid) "
        f"FROM PUBLIC, {roles}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION operations.register_worker_maintenance(uuid) "
        "TO home_agent_worker"
    )


def upgrade() -> None:
    _install_registration_function(reject_fresh_contender=True)


def downgrade() -> None:
    _install_registration_function(reject_fresh_contender=False)
