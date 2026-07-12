"""Add durable worker-maintenance health and rollout proof.

Revision ID: 0006_worker_maintenance_health
Revises: 0005_principal_binding_proposals
Create Date: 2026-07-12
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0006_worker_maintenance_health"
down_revision: str | None = "0005_principal_binding_proposals"
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


def _add_constraint_unless_present(
    table: str, constraint: str, definition: str
) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = '{table}'::regclass
               AND conname = '{constraint}'
          ) THEN
            ALTER TABLE {table}
              ADD CONSTRAINT {constraint} {definition};
          END IF;
        END
        $$
        """
    )


def _phase2_view(*, exclude_worker_unavailable: bool) -> None:
    worker_filter = (
        "\n          AND metadata ->> 'raw_payload_status' "
        "<> 'suppressed_retention_worker_unavailable'"
        if exclude_worker_unavailable
        else ""
    )
    op.execute(
        f"""
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
          AND ha_context = '{{}}'::jsonb
          AND left(metadata ->> 'raw_payload_status', 11) = 'suppressed_'
          {worker_filter}
        OFFSET 0
        """
    )


def upgrade() -> None:
    # Existing authorizations cannot be assigned a worker proof after the fact.
    # The live deployment is still record-only, so fail rather than fabricate
    # authority if that precondition ever changes.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM operations.rollout_authorizations) THEN
            RAISE EXCEPTION
              'pre-0006 rollout authorization lacks worker maintenance proof'
              USING ERRCODE = '23514';
          END IF;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE UNLOGGED TABLE IF NOT EXISTS operations.worker_maintenance_state (
          state_key varchar(32) NOT NULL,
          worker_instance_id uuid NOT NULL,
          database_started_at timestamptz NOT NULL,
          kernel_version varchar(128) NOT NULL,
          state varchar(16) NOT NULL,
          started_at timestamptz NOT NULL,
          heartbeat_at timestamptz NOT NULL,
          heartbeat_sequence bigint NOT NULL,
          maintenance_attempted_at timestamptz,
          maintenance_succeeded_at timestamptz,
          success_sequence bigint NOT NULL,
          consecutive_failures integer NOT NULL,
          last_error_code varchar(64),
          stopped_at timestamptz,
          spool_rows_pruned bigint,
          binding_retention_receipt jsonb,
          updated_at timestamptz NOT NULL,
          CONSTRAINT pk_worker_maintenance_state PRIMARY KEY (state_key),
          CONSTRAINT ck_worker_maintenance_state_singleton
            CHECK (state_key = 'durable_worker'),
          CONSTRAINT ck_worker_maintenance_state_random_instance
            CHECK (
              substring(worker_instance_id::text from 15 for 1) IN ('4','7')
              AND substring(worker_instance_id::text from 20 for 1)
                IN ('8','9','a','b')
            ),
          CONSTRAINT ck_worker_maintenance_state_kernel_version
            CHECK (kernel_version = 'worker-maintenance-cycle-v1'),
          CONSTRAINT ck_worker_maintenance_state_state_value CHECK (
            state IN ('starting','running','degraded','stopping')
          ),
          CONSTRAINT ck_worker_maintenance_state_sequences
            CHECK (
              heartbeat_sequence >= 1
              AND success_sequence >= 0
              AND consecutive_failures >= 0
            ),
          CONSTRAINT ck_worker_maintenance_state_time_order
            CHECK (
              database_started_at <= started_at
              AND started_at <= heartbeat_at
              AND heartbeat_at <= updated_at
              AND (
                maintenance_attempted_at IS NULL
                OR (
                  maintenance_attempted_at >= started_at
                  AND maintenance_attempted_at <= updated_at
                )
              )
              AND (
                maintenance_succeeded_at IS NULL
                OR (
                  maintenance_succeeded_at >= started_at
                  AND maintenance_succeeded_at <= updated_at
                )
              )
              AND (
                stopped_at IS NULL
                OR (stopped_at >= started_at AND stopped_at <= updated_at)
              )
            ),
          CONSTRAINT ck_worker_maintenance_state_success_shape
            CHECK (
              (
                success_sequence = 0
                AND maintenance_succeeded_at IS NULL
                AND spool_rows_pruned IS NULL
                AND binding_retention_receipt IS NULL
              )
              OR (
                success_sequence > 0
                AND maintenance_succeeded_at IS NOT NULL
                AND spool_rows_pruned IS NOT NULL
                AND spool_rows_pruned >= 0
                AND binding_retention_receipt IS NOT NULL
              )
            ),
          CONSTRAINT ck_worker_maintenance_state_failure_shape
            CHECK (
              (consecutive_failures = 0 AND last_error_code IS NULL)
              OR (
                consecutive_failures > 0
                AND last_error_code IN (
                  'runtime_spool_prune_failed',
                  'binding_retention_failed',
                  'worker_loop_failed'
                )
              )
            ),
          CONSTRAINT ck_worker_maintenance_state_state_shape
            CHECK (
              (
                state = 'starting'
                AND success_sequence = 0
                AND maintenance_attempted_at IS NULL
                AND consecutive_failures = 0
                AND stopped_at IS NULL
              )
              OR (
                state = 'running'
                AND success_sequence > 0
                AND maintenance_attempted_at IS NOT NULL
                AND maintenance_succeeded_at IS NOT NULL
                AND maintenance_attempted_at <= maintenance_succeeded_at
                AND consecutive_failures = 0
                AND stopped_at IS NULL
              )
              OR (
                state = 'degraded'
                AND consecutive_failures > 0
                AND maintenance_attempted_at IS NOT NULL
                AND stopped_at IS NULL
              )
              OR (state = 'stopping' AND stopped_at IS NOT NULL)
            ),
          CONSTRAINT ck_worker_maintenance_state_receipt_shape
            CHECK (
              binding_retention_receipt IS NULL OR (
                jsonb_typeof(binding_retention_receipt) = 'object'
                AND binding_retention_receipt ?& ARRAY[
                  'proposals_expired',
                  'staged_requests_expired',
                  'pending_requests_expired',
                  'requests_expired',
                  'proposals_purged',
                  'requests_purged'
                ]
                AND binding_retention_receipt - ARRAY[
                  'proposals_expired',
                  'staged_requests_expired',
                  'pending_requests_expired',
                  'requests_expired',
                  'proposals_purged',
                  'requests_purged'
                ] = '{}'::jsonb
                AND jsonb_typeof(
                  binding_retention_receipt->'proposals_expired'
                ) = 'number'
                AND (binding_retention_receipt->>'proposals_expired')
                  ~ '^[0-9]+$'
                AND jsonb_typeof(
                  binding_retention_receipt->'staged_requests_expired'
                ) = 'number'
                AND (binding_retention_receipt->>'staged_requests_expired')
                  ~ '^[0-9]+$'
                AND jsonb_typeof(
                  binding_retention_receipt->'pending_requests_expired'
                ) = 'number'
                AND (binding_retention_receipt->>'pending_requests_expired')
                  ~ '^[0-9]+$'
                AND jsonb_typeof(
                  binding_retention_receipt->'requests_expired'
                ) = 'number'
                AND (binding_retention_receipt->>'requests_expired')
                  ~ '^[0-9]+$'
                AND jsonb_typeof(
                  binding_retention_receipt->'proposals_purged'
                ) = 'number'
                AND (binding_retention_receipt->>'proposals_purged')
                  ~ '^[0-9]+$'
                AND jsonb_typeof(
                  binding_retention_receipt->'requests_purged'
                ) = 'number'
                AND (binding_retention_receipt->>'requests_purged')
                  ~ '^[0-9]+$'
                AND (binding_retention_receipt->>'requests_expired')::numeric =
                  (binding_retention_receipt->>'staged_requests_expired')::numeric
                  +
                  (binding_retention_receipt->>'pending_requests_expired')::numeric
              )
            )
        )
        """
    )

    # Current metadata creates this table during revision 0001 on a pristine
    # database. Assert that either path still has the required crash-reset
    # persistence class.
    op.execute("ALTER TABLE operations.worker_maintenance_state SET UNLOGGED")

    for definition in (
        "worker_instance_id uuid",
        "worker_success_sequence bigint",
        "worker_kernel_version varchar(128)",
        "worker_maintenance_succeeded_at timestamptz",
        "worker_proof_digest varchar(64)",
    ):
        op.execute(
            "ALTER TABLE operations.rollout_authorizations "
            f"ADD COLUMN IF NOT EXISTS {definition}"
        )
    for column in (
        "worker_instance_id",
        "worker_success_sequence",
        "worker_kernel_version",
        "worker_maintenance_succeeded_at",
        "worker_proof_digest",
    ):
        op.execute(
            "ALTER TABLE operations.rollout_authorizations "
            f"ALTER COLUMN {column} SET NOT NULL"
        )
    _add_constraint_unless_present(
        "operations.rollout_authorizations",
        "ck_rollout_authorizations_worker_random_instance",
        "CHECK ("
        "substring(worker_instance_id::text from 15 for 1) IN ('4','7') "
        "AND substring(worker_instance_id::text from 20 for 1) "
        "IN ('8','9','a','b'))",
    )
    _add_constraint_unless_present(
        "operations.rollout_authorizations",
        "ck_rollout_authorizations_worker_proof_shape",
        "CHECK (worker_success_sequence > 0 "
        "AND worker_kernel_version = 'worker-maintenance-cycle-v1' "
        "AND worker_proof_digest ~ '^[0-9a-f]{64}$')",
    )
    _add_constraint_unless_present(
        "operations.rollout_authorizations",
        "ck_rollout_authorizations_worker_proof_time",
        "CHECK (worker_maintenance_succeeded_at <= readiness_evaluated_at "
        "AND readiness_evaluated_at <= authorized_at)",
    )

    op.execute(
        "ALTER TABLE operations.worker_maintenance_state "
        "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE operations.worker_maintenance_state "
        "FORCE ROW LEVEL SECURITY"
    )
    for policy in (
        "worker_maintenance_state_read",
        "worker_maintenance_state_insert",
        "worker_maintenance_state_update",
        "worker_maintenance_state_delete",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS {policy} "
            "ON operations.worker_maintenance_state"
        )
    op.execute(
        """
        CREATE POLICY worker_maintenance_state_read
        ON operations.worker_maintenance_state FOR SELECT
        USING (
          session_user IN (
            'home_agent_api',
            'home_agent_ingest',
            'home_agent_worker',
            'home_agent_rollout',
            'home_agent_owner'
          )
        )
        """
    )
    writer_expression = (
        "session_user = 'home_agent_owner' OR "
        "(session_user = 'home_agent_worker' "
        "AND current_user = 'home_agent_owner')"
    )
    op.execute(
        "CREATE POLICY worker_maintenance_state_insert "
        "ON operations.worker_maintenance_state FOR INSERT "
        f"WITH CHECK ({writer_expression})"
    )
    op.execute(
        "CREATE POLICY worker_maintenance_state_update "
        "ON operations.worker_maintenance_state FOR UPDATE "
        f"USING ({writer_expression}) WITH CHECK ({writer_expression})"
    )
    op.execute(
        "CREATE POLICY worker_maintenance_state_delete "
        "ON operations.worker_maintenance_state FOR DELETE "
        f"USING ({writer_expression})"
    )

    op.execute(
        """
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
          SELECT worker_instance_id, database_started_at, state
            INTO existing_instance, existing_database_start, existing_state
            FROM operations.worker_maintenance_state
           WHERE state_key = 'durable_worker'
           FOR UPDATE;
          IF FOUND
             AND existing_instance = target_worker_instance_id
             AND existing_database_start = database_start THEN
            RETURN existing_state <> 'stopping';
          END IF;
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
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION operations.heartbeat_worker_maintenance(
          target_worker_instance_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          operation_time timestamptz;
        BEGIN
          IF session_user <> 'home_agent_worker' THEN
            RAISE EXCEPTION 'worker maintenance role required'
              USING ERRCODE = '42501';
          END IF;
          PERFORM pg_advisory_xact_lock(7210202606);
          operation_time := clock_timestamp();
          UPDATE operations.worker_maintenance_state
             SET heartbeat_at = operation_time,
                 heartbeat_sequence = heartbeat_sequence + 1,
                 updated_at = operation_time
           WHERE state_key = 'durable_worker'
             AND worker_instance_id = target_worker_instance_id
             AND database_started_at = pg_postmaster_start_time()
             AND state <> 'stopping';
          RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION operations.run_worker_maintenance_cycle(
          target_worker_instance_id uuid,
          runtime_spool_rows_pruned bigint
        ) RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          cycle_started timestamptz;
          cycle_succeeded timestamptz;
          retention_receipt jsonb;
        BEGIN
          IF session_user <> 'home_agent_worker' THEN
            RAISE EXCEPTION 'worker maintenance role required'
              USING ERRCODE = '42501';
          END IF;
          IF runtime_spool_rows_pruned IS NULL
             OR runtime_spool_rows_pruned < 0 THEN
            RAISE EXCEPTION 'invalid runtime spool retention receipt'
              USING ERRCODE = '22023';
          END IF;
          PERFORM pg_advisory_xact_lock(7210202606);
          PERFORM 1
            FROM operations.worker_maintenance_state
           WHERE state_key = 'durable_worker'
             AND worker_instance_id = target_worker_instance_id
             AND database_started_at = pg_postmaster_start_time()
             AND state <> 'stopping'
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'worker maintenance instance is not current'
              USING ERRCODE = '55000';
          END IF;

          cycle_started := clock_timestamp();
          SELECT privacy.expire_principal_binding_work(cycle_started)
            INTO retention_receipt;
          IF jsonb_typeof(retention_receipt) IS DISTINCT FROM 'object'
             OR NOT retention_receipt ?& ARRAY[
               'proposals_expired',
               'staged_requests_expired',
               'pending_requests_expired',
               'requests_expired',
               'proposals_purged',
               'requests_purged'
             ]
             OR retention_receipt - ARRAY[
               'proposals_expired',
               'staged_requests_expired',
               'pending_requests_expired',
               'requests_expired',
               'proposals_purged',
               'requests_purged'
             ] <> '{}'::jsonb
             OR jsonb_typeof(retention_receipt->'proposals_expired')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'proposals_expired')
                !~ '^[0-9]+$'
             OR jsonb_typeof(retention_receipt->'staged_requests_expired')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'staged_requests_expired')
                !~ '^[0-9]+$'
             OR jsonb_typeof(retention_receipt->'pending_requests_expired')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'pending_requests_expired')
                !~ '^[0-9]+$'
             OR jsonb_typeof(retention_receipt->'requests_expired')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'requests_expired')
                !~ '^[0-9]+$'
             OR jsonb_typeof(retention_receipt->'proposals_purged')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'proposals_purged')
                !~ '^[0-9]+$'
             OR jsonb_typeof(retention_receipt->'requests_purged')
                IS DISTINCT FROM 'number'
             OR (retention_receipt->>'requests_purged')
                !~ '^[0-9]+$'
             OR (retention_receipt->>'requests_expired')::numeric <>
                (retention_receipt->>'staged_requests_expired')::numeric
                +
                (retention_receipt->>'pending_requests_expired')::numeric THEN
            RAISE EXCEPTION 'binding retention kernel receipt is invalid'
              USING ERRCODE = '23514';
          END IF;
          cycle_succeeded := clock_timestamp();
          UPDATE operations.worker_maintenance_state
             SET state = 'running',
                 heartbeat_at = cycle_succeeded,
                 heartbeat_sequence = heartbeat_sequence + 1,
                 maintenance_attempted_at = cycle_started,
                 maintenance_succeeded_at = cycle_succeeded,
                 success_sequence = success_sequence + 1,
                 consecutive_failures = 0,
                 last_error_code = NULL,
                 stopped_at = NULL,
                 spool_rows_pruned = runtime_spool_rows_pruned,
                 binding_retention_receipt = retention_receipt,
                 updated_at = cycle_succeeded
           WHERE state_key = 'durable_worker'
             AND worker_instance_id = target_worker_instance_id
             AND database_started_at = pg_postmaster_start_time();
          IF NOT FOUND THEN
            RAISE EXCEPTION 'worker maintenance instance changed during cycle'
              USING ERRCODE = '40001';
          END IF;
          RETURN retention_receipt;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION operations.fail_worker_maintenance(
          target_worker_instance_id uuid,
          target_error_code varchar
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          operation_time timestamptz;
        BEGIN
          IF session_user <> 'home_agent_worker' THEN
            RAISE EXCEPTION 'worker maintenance role required'
              USING ERRCODE = '42501';
          END IF;
          IF target_error_code IS NULL OR target_error_code NOT IN (
            'runtime_spool_prune_failed',
            'binding_retention_failed',
            'worker_loop_failed'
          ) THEN
            RAISE EXCEPTION 'invalid worker maintenance error code'
              USING ERRCODE = '22023';
          END IF;
          PERFORM pg_advisory_xact_lock(7210202606);
          operation_time := clock_timestamp();
          UPDATE operations.worker_maintenance_state
             SET state = 'degraded',
                 heartbeat_at = operation_time,
                 heartbeat_sequence = heartbeat_sequence + 1,
                 maintenance_attempted_at = operation_time,
                 consecutive_failures = consecutive_failures + 1,
                 last_error_code = target_error_code,
                 updated_at = operation_time
           WHERE state_key = 'durable_worker'
             AND worker_instance_id = target_worker_instance_id
             AND database_started_at = pg_postmaster_start_time()
             AND state <> 'stopping';
          RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION operations.stop_worker_maintenance(
          target_worker_instance_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
          operation_time timestamptz;
        BEGIN
          IF session_user <> 'home_agent_worker' THEN
            RAISE EXCEPTION 'worker maintenance role required'
              USING ERRCODE = '42501';
          END IF;
          PERFORM pg_advisory_xact_lock(7210202606);
          operation_time := clock_timestamp();
          UPDATE operations.worker_maintenance_state
             SET state = 'stopping',
                 heartbeat_at = operation_time,
                 heartbeat_sequence = heartbeat_sequence + 1,
                 stopped_at = operation_time,
                 updated_at = operation_time
           WHERE state_key = 'durable_worker'
             AND worker_instance_id = target_worker_instance_id
             AND database_started_at = pg_postmaster_start_time()
             AND state <> 'stopping';
          IF FOUND THEN
            RETURN true;
          END IF;
          RETURN EXISTS (
            SELECT 1
              FROM operations.worker_maintenance_state
             WHERE state_key = 'durable_worker'
               AND worker_instance_id = target_worker_instance_id
               AND database_started_at = pg_postmaster_start_time()
               AND state = 'stopping'
          );
        END;
        $$
        """
    )

    function_signatures = (
        "operations.register_worker_maintenance(uuid)",
        "operations.heartbeat_worker_maintenance(uuid)",
        "operations.run_worker_maintenance_cycle(uuid,bigint)",
        "operations.fail_worker_maintenance(uuid,character varying)",
        "operations.stop_worker_maintenance(uuid)",
    )
    roles = ", ".join(WORKER_ROLES)
    op.execute(
        "REVOKE ALL ON TABLE operations.worker_maintenance_state "
        f"FROM PUBLIC, {roles}"
    )
    op.execute(
        "GRANT SELECT ON TABLE operations.worker_maintenance_state "
        "TO home_agent_api, home_agent_ingest, home_agent_worker, "
        "home_agent_rollout"
    )
    for signature in function_signatures:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, {roles}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO home_agent_worker")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "privacy.expire_principal_binding_work(timestamptz) "
        "FROM home_agent_worker"
    )

    _phase2_view(exclude_worker_unavailable=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM operations.rollout_authorizations) THEN
            RAISE EXCEPTION
              'refusing to remove worker proof from rollout authorization'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $$
        """
    )
    _phase2_view(exclude_worker_unavailable=False)
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "privacy.expire_principal_binding_work(timestamptz) "
        "TO home_agent_worker"
    )
    for signature in (
        "operations.stop_worker_maintenance(uuid)",
        "operations.fail_worker_maintenance(uuid,character varying)",
        "operations.run_worker_maintenance_cycle(uuid,bigint)",
        "operations.heartbeat_worker_maintenance(uuid)",
        "operations.register_worker_maintenance(uuid)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for constraint in (
        "ck_rollout_authorizations_worker_proof_time",
        "ck_rollout_authorizations_worker_proof_shape",
        "ck_rollout_authorizations_worker_random_instance",
    ):
        op.execute(
            "ALTER TABLE operations.rollout_authorizations "
            f"DROP CONSTRAINT IF EXISTS {constraint}"
        )
    for column in (
        "worker_proof_digest",
        "worker_maintenance_succeeded_at",
        "worker_kernel_version",
        "worker_success_sequence",
        "worker_instance_id",
    ):
        op.execute(
            "ALTER TABLE operations.rollout_authorizations "
            f"DROP COLUMN IF EXISTS {column}"
        )
    op.execute("DROP TABLE IF EXISTS operations.worker_maintenance_state")
