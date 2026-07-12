"""Normalize dormant reviewed-identity erasure-operation sources.

Revision ID: 0010_identity_erasure_source
Revises: 0009_identity_finalizer_base
Create Date: 2026-07-12

This revision is an owner-only schema predecessor for a future erasure
kernel. It adds no callable erasure or semantic-finalizer function and grants
the dormant finalizer roles no authority. Production remains pinned to 0006.

An erasure impact can now be sourced by exactly one principal-authorized
``privacy.erasure_requests`` row or one imported-person
``privacy.auto_expiry_schedules`` row. This removes the false requirement
that an auto-expiring, not-yet-bound imported person first have a principal.
The operation and impact records remain content-minimized and retain no leaf
decision, receipt, name, alias, external identifier, or source payload.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.schema import CreateTable

from app import schema


revision: str = "0010_identity_erasure_source"
down_revision: str | None = "0009_identity_finalizer_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OPERATION_TABLE = "operations.reviewed_identity_migration_erasure_operations"
IMPACT_TABLE = "operations.reviewed_identity_migration_erasure_impacts"
FINALIZER_ROLES = (
    "home_agent_identity_finalizer",
    "home_agent_identity_finalizer_kernel",
)
DOWNGRADE_EVIDENCE_TABLES = (
    "operations.reviewed_identity_migration_item_receipts",
    "operations.reviewed_identity_migration_finalizations",
    "operations.reviewed_identity_migration_projection_lineage",
    "operations.reviewed_identity_migration_projection_subjects",
    "operations.legacy_identity_writer_evidence",
    "operations.privacy_cutover_check_receipts",
    "operations.semantic_authority_cutovers",
    OPERATION_TABLE,
    IMPACT_TABLE,
)
NON_OWNER_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_migration",
    "home_agent_identity_kernel",
    *FINALIZER_ROLES,
)

OLD_IMPACT_COLUMNS = (
    "impact_id|uuid|true",
    "run_id|uuid|true",
    "erasure_request_id|uuid|true",
    "impact_code|character varying(32)|true",
    "readiness_suspension|character varying(16)|true",
    "removed_leaf_commitment_count|integer|true",
    "unlinked_projection_count|integer|true",
    "impact_commitment|character varying(64)|true",
    "recorded_at|timestamp with time zone|true",
)
NEW_IMPACT_COLUMNS = (
    "impact_id|uuid|true",
    "run_id|uuid|true",
    "operation_id|uuid|true",
    "impact_code|character varying(32)|true",
    "readiness_suspension|character varying(16)|true",
    "removed_leaf_commitment_count|integer|true",
    "unlinked_projection_count|integer|true",
    "impact_commitment|character varying(64)|true",
    "recorded_at|timestamp with time zone|true",
)
OPERATION_COLUMNS = (
    "operation_id|uuid|true",
    "source_kind|character varying(32)|true",
    "erasure_request_id|uuid|false",
    "auto_expiry_schedule_id|uuid|false",
    "operation_commitment|character varying(64)|true",
    "recorded_at|timestamp with time zone|true",
)

OLD_IMPACT_CONSTRAINTS = (
    "pk_reviewed_identity_migration_erasure_impacts",
    "fk_identity_migration_erasure_impact_run",
    "fk_identity_migration_erasure_request",
    "identity_erasure_impact_request",
    "uq_reviewed_identity_migration_erasure_impacts_impact_c_6f7b",
    "ck_reviewed_identity_migration_erasure_impacts_uuidv7_ids",
    "ck_reviewed_identity_migration_erasure_impacts_impact_shape",
    "ck_reviewed_identity_migration_erasure_impacts_impact_caps",
    "ck_reviewed_identity_migration_erasure_impacts_impact_c_4c72",
    "ck_reviewed_identity_migration_erasure_impacts_commitment_shape",
)
NEW_IMPACT_CONSTRAINTS = tuple(
    "fk_identity_migration_erasure_operation"
    if item == "fk_identity_migration_erasure_request"
    else "identity_erasure_impact_operation"
    if item == "identity_erasure_impact_request"
    else item
    for item in OLD_IMPACT_CONSTRAINTS
)
OPERATION_CONSTRAINTS = (
    "pk_reviewed_identity_migration_erasure_operations",
    "fk_identity_erasure_operation_request",
    "fk_identity_erasure_operation_auto_expiry",
    "identity_erasure_operation_request",
    "identity_erasure_operation_auto_expiry",
    "identity_erasure_operation_commitment",
    "ck_reviewed_identity_migration_erasure_operations_exact_source",
    "ck_reviewed_identity_migration_erasure_operations_uuidv7_ids",
    "ck_reviewed_identity_migration_erasure_operations_commi_fe29",
)


def _sql_array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join("'" + item.replace("'", "''") + "'" for item in values) + "]::text[]"


def _validate_predecessor_shape() -> bool:
    old_columns = _sql_array(tuple(sorted(OLD_IMPACT_COLUMNS)))
    new_columns = _sql_array(tuple(sorted(NEW_IMPACT_COLUMNS)))
    operation_columns = _sql_array(tuple(sorted(OPERATION_COLUMNS)))
    old_constraints = _sql_array(OLD_IMPACT_CONSTRAINTS)
    new_constraints = _sql_array(NEW_IMPACT_CONSTRAINTS)
    operation_constraints = _sql_array(OPERATION_CONSTRAINTS)
    op.execute(
        f"""
        DO $admission$
        DECLARE
          impact regclass := pg_catalog.to_regclass('{IMPACT_TABLE}');
          operation regclass := pg_catalog.to_regclass('{OPERATION_TABLE}');
          impact_columns text[];
          operation_columns text[];
          impact_constraints text[];
          operation_constraints text[];
          actual_definitions text[];
          expected_definitions text[];
          actual_policies text[];
          expected_policies text[];
          owner_oid oid;
          kernel_oid oid;
          operation_has_rows boolean := false;
        BEGIN
          IF impact IS NULL THEN
            RAISE EXCEPTION 'identity_erasure_impact_predecessor_missing'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (SELECT 1 FROM {IMPACT_TABLE}) THEN
            RAISE EXCEPTION 'identity_erasure_operation_preexisting_evidence'
              USING ERRCODE = '55000';
          END IF;
          IF operation IS NOT NULL THEN
            EXECUTE 'SELECT EXISTS (SELECT 1 FROM {OPERATION_TABLE})'
              INTO operation_has_rows;
            IF operation_has_rows THEN
              RAISE EXCEPTION 'identity_erasure_operation_preexisting_evidence'
                USING ERRCODE = '55000';
            END IF;
          END IF;
          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_identity_kernel';
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS impact_table
             WHERE impact_table.oid = impact
               AND impact_table.relkind = 'r'
               AND impact_table.relpersistence = 'p'
               AND impact_table.relowner = owner_oid
               AND impact_table.relrowsecurity
               AND impact_table.relforcerowsecurity
          ) OR (operation IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS operation_table
             WHERE operation_table.oid = operation
               AND operation_table.relkind = 'r'
               AND operation_table.relpersistence = 'p'
               AND operation_table.relowner = owner_oid
               AND NOT operation_table.relrowsecurity
               AND NOT operation_table.relforcerowsecurity
          )) THEN
            RAISE EXCEPTION 'identity_erasure_operation_predecessor_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS guard_trigger
              JOIN pg_catalog.pg_proc AS guard_function
                ON guard_function.oid = guard_trigger.tgfoid
             WHERE guard_trigger.tgrelid = impact
               AND guard_trigger.tgname =
                   'reviewed_identity_migration_erasure_replay_guard'
               AND guard_trigger.tgenabled = 'O'
               AND NOT guard_trigger.tgisinternal
               AND guard_trigger.tgtype = 7
               AND guard_trigger.tgnargs = 0
               AND guard_function.oid = pg_catalog.to_regprocedure(
                     'operations.'
                     'bump_reviewed_identity_migration_replay_guard()'
                   )
               AND guard_function.proowner = kernel_oid
               AND guard_function.prosecdef
               AND guard_function.proconfig =
                   ARRAY['search_path=pg_catalog, pg_temp']::text[]
               AND guard_function.proacl IS NOT NULL
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.aclexplode(guard_function.proacl) AS acl
                  WHERE acl.privilege_type = 'EXECUTE'
               )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_operation_replay_guard_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT pg_catalog.array_agg(
                   attribute.attname || '|' ||
                   pg_catalog.format_type(
                     attribute.atttypid, attribute.atttypmod
                   ) || '|' || attribute.attnotnull::text
                   ORDER BY attribute.attname
                 )
            INTO impact_columns
            FROM pg_catalog.pg_attribute AS attribute
           WHERE attribute.attrelid = impact
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;
          SELECT pg_catalog.array_agg(constraint_row.conname ORDER BY constraint_row.conname)
            INTO impact_constraints
            FROM pg_catalog.pg_constraint AS constraint_row
           WHERE constraint_row.conrelid = impact;

          IF operation IS NULL THEN
            IF impact_columns IS DISTINCT FROM {old_columns}
               OR impact_constraints IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(value ORDER BY value)
                      FROM pg_catalog.unnest({old_constraints}) AS value
                  ) THEN
              RAISE EXCEPTION 'identity_erasure_operation_predecessor_invalid'
                USING ERRCODE = '55000';
            END IF;
            CREATE TABLE operations.expected_old_identity_erasure_impact (
              impact_id uuid PRIMARY KEY,
              run_id uuid NOT NULL REFERENCES
                operations.reviewed_identity_migration_runs(run_id),
              erasure_request_id uuid NOT NULL REFERENCES
                privacy.erasure_requests(erasure_request_id),
              impact_code varchar(32) NOT NULL,
              readiness_suspension varchar(16) NOT NULL,
              removed_leaf_commitment_count integer NOT NULL,
              unlinked_projection_count integer NOT NULL,
              impact_commitment varchar(64) NOT NULL UNIQUE,
              UNIQUE (run_id, erasure_request_id),
              CHECK (
                substring(impact_id::text from 15 for 1) = '7'
                AND substring(impact_id::text from 20 for 1)
                    IN ('8','9','a','b')
                AND substring(erasure_request_id::text from 15 for 1) = '7'
                AND substring(erasure_request_id::text from 20 for 1)
                    IN ('8','9','a','b')
              ),
              CHECK (
                impact_code IN (
                  'leaf_commitments_removed','linkage_unavailable'
                ) AND readiness_suspension = 'required'
              ),
              CHECK (
                removed_leaf_commitment_count BETWEEN 0 AND 30000
                AND unlinked_projection_count BETWEEN 0 AND 10000
              ),
              CHECK (
                (impact_code = 'leaf_commitments_removed'
                 AND removed_leaf_commitment_count > 0
                 AND unlinked_projection_count = 0)
                OR (impact_code = 'linkage_unavailable'
                    AND unlinked_projection_count > 0)
              ),
              CHECK (impact_commitment ~ '^[0-9a-f]{{64}}$')
            );
            CREATE POLICY reviewed_identity_migration_erasure_impacts_owner_select
              ON operations.expected_old_identity_erasure_impact FOR SELECT
              TO home_agent_owner
              USING (session_user = 'home_agent_owner');
            CREATE POLICY reviewed_identity_migration_erasure_impacts_owner_insert
              ON operations.expected_old_identity_erasure_impact FOR INSERT
              TO home_agent_owner
              WITH CHECK (session_user = 'home_agent_owner');
            CREATE POLICY identity_migration_erasure_impacts_manifest_kernel_select
              ON operations.expected_old_identity_erasure_impact FOR SELECT
              TO home_agent_identity_kernel
              USING (
                session_user = 'home_agent_identity_migration'
                AND current_user = 'home_agent_identity_kernel'
                AND NOT pg_catalog.pg_has_role(
                  session_user, 'home_agent_identity_kernel', 'SET'
                )
              );
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO actual_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = impact;
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO expected_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid =
                   'operations.expected_old_identity_erasure_impact'::regclass;
            IF actual_definitions IS DISTINCT FROM expected_definitions THEN
              RAISE EXCEPTION 'identity_erasure_operation_predecessor_invalid'
                USING ERRCODE = '55000';
            END IF;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO actual_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = impact;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO expected_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid =
                   'operations.expected_old_identity_erasure_impact'::regclass;
            IF actual_policies IS DISTINCT FROM expected_policies THEN
              RAISE EXCEPTION 'identity_erasure_operation_predecessor_invalid'
                USING ERRCODE = '55000';
            END IF;
            DROP TABLE operations.expected_old_identity_erasure_impact;
          ELSE
            SELECT pg_catalog.array_agg(
                     attribute.attname || '|' ||
                     pg_catalog.format_type(
                       attribute.atttypid, attribute.atttypmod
                     ) || '|' || attribute.attnotnull::text
                     ORDER BY attribute.attname
                   )
              INTO operation_columns
              FROM pg_catalog.pg_attribute AS attribute
             WHERE attribute.attrelid = operation
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;
            SELECT pg_catalog.array_agg(constraint_row.conname ORDER BY constraint_row.conname)
              INTO operation_constraints
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = operation;
            IF impact_columns IS DISTINCT FROM {new_columns}
               OR operation_columns IS DISTINCT FROM {operation_columns}
               OR impact_constraints IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(value ORDER BY value)
                      FROM pg_catalog.unnest({new_constraints}) AS value
                  )
               OR operation_constraints IS DISTINCT FROM (
                    SELECT pg_catalog.array_agg(value ORDER BY value)
                      FROM pg_catalog.unnest({operation_constraints}) AS value
                  ) THEN
              RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
                USING ERRCODE = '55000';
            END IF;
            CREATE TABLE operations.expected_identity_erasure_operation (
              operation_id uuid PRIMARY KEY,
              source_kind varchar(32) NOT NULL,
              erasure_request_id uuid UNIQUE REFERENCES
                privacy.erasure_requests(erasure_request_id),
              auto_expiry_schedule_id uuid UNIQUE REFERENCES
                privacy.auto_expiry_schedules(schedule_id),
              operation_commitment varchar(64) NOT NULL UNIQUE,
              CHECK (
                (source_kind = 'erasure_request'
                 AND erasure_request_id IS NOT NULL
                 AND auto_expiry_schedule_id IS NULL)
                OR (source_kind = 'auto_expiry_schedule'
                    AND erasure_request_id IS NULL
                    AND auto_expiry_schedule_id IS NOT NULL)
              ),
              CHECK (
                substring(operation_id::text from 15 for 1) = '7'
                AND substring(operation_id::text from 20 for 1)
                    IN ('8','9','a','b')
                AND (erasure_request_id IS NULL OR (
                  substring(erasure_request_id::text from 15 for 1) = '7'
                  AND substring(erasure_request_id::text from 20 for 1)
                      IN ('8','9','a','b')
                ))
                AND (auto_expiry_schedule_id IS NULL OR (
                  substring(auto_expiry_schedule_id::text from 15 for 1) = '7'
                  AND substring(auto_expiry_schedule_id::text from 20 for 1)
                      IN ('8','9','a','b')
                ))
              ),
              CHECK (operation_commitment ~ '^[0-9a-f]{{64}}$')
            );
            CREATE TABLE operations.expected_new_identity_erasure_impact (
              impact_id uuid PRIMARY KEY,
              run_id uuid NOT NULL REFERENCES
                operations.reviewed_identity_migration_runs(run_id),
              operation_id uuid NOT NULL REFERENCES
                operations.reviewed_identity_migration_erasure_operations(
                  operation_id
                ),
              impact_code varchar(32) NOT NULL,
              readiness_suspension varchar(16) NOT NULL,
              removed_leaf_commitment_count integer NOT NULL,
              unlinked_projection_count integer NOT NULL,
              impact_commitment varchar(64) NOT NULL UNIQUE,
              UNIQUE (run_id, operation_id),
              CHECK (
                substring(impact_id::text from 15 for 1) = '7'
                AND substring(impact_id::text from 20 for 1)
                    IN ('8','9','a','b')
                AND substring(operation_id::text from 15 for 1) = '7'
                AND substring(operation_id::text from 20 for 1)
                    IN ('8','9','a','b')
              ),
              CHECK (
                impact_code IN (
                  'leaf_commitments_removed','linkage_unavailable'
                ) AND readiness_suspension = 'required'
              ),
              CHECK (
                removed_leaf_commitment_count BETWEEN 0 AND 30000
                AND unlinked_projection_count BETWEEN 0 AND 10000
              ),
              CHECK (
                (impact_code = 'leaf_commitments_removed'
                 AND removed_leaf_commitment_count > 0
                 AND unlinked_projection_count = 0)
                OR (impact_code = 'linkage_unavailable'
                    AND unlinked_projection_count > 0)
              ),
              CHECK (impact_commitment ~ '^[0-9a-f]{{64}}$')
            );
            CREATE POLICY reviewed_identity_migration_erasure_impacts_owner_select
              ON operations.expected_new_identity_erasure_impact FOR SELECT
              TO home_agent_owner
              USING (session_user = 'home_agent_owner');
            CREATE POLICY reviewed_identity_migration_erasure_impacts_owner_insert
              ON operations.expected_new_identity_erasure_impact FOR INSERT
              TO home_agent_owner
              WITH CHECK (session_user = 'home_agent_owner');
            CREATE POLICY identity_migration_erasure_impacts_manifest_kernel_select
              ON operations.expected_new_identity_erasure_impact FOR SELECT
              TO home_agent_identity_kernel
              USING (
                session_user = 'home_agent_identity_migration'
                AND current_user = 'home_agent_identity_kernel'
                AND NOT pg_catalog.pg_has_role(
                  session_user, 'home_agent_identity_kernel', 'SET'
                )
              );
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO actual_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = operation;
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO expected_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid =
                   'operations.expected_identity_erasure_operation'::regclass;
            IF actual_definitions IS DISTINCT FROM expected_definitions THEN
              RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
                USING ERRCODE = '55000';
            END IF;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO actual_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = operation;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO expected_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid =
                   'operations.expected_identity_erasure_operation'::regclass;
            IF actual_policies IS DISTINCT FROM expected_policies THEN
              RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
                USING ERRCODE = '55000';
            END IF;
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO actual_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid = impact;
            SELECT pg_catalog.array_agg(
                     pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     ) ORDER BY pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true
                     )
                   )
              INTO expected_definitions
              FROM pg_catalog.pg_constraint AS constraint_row
             WHERE constraint_row.conrelid =
                   'operations.expected_new_identity_erasure_impact'::regclass;
            IF actual_definitions IS DISTINCT FROM expected_definitions THEN
              RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
                USING ERRCODE = '55000';
            END IF;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO actual_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid = impact;
            SELECT pg_catalog.array_agg(
                     policy.polname || ':' || policy.polcmd::text || ':' ||
                     policy.polpermissive::text || ':' ||
                     policy.polroles::text || ':' ||
                     COALESCE(policy.polqual::text, '') || ':' ||
                     COALESCE(policy.polwithcheck::text, '')
                     ORDER BY policy.polname
                   )
              INTO expected_policies
              FROM pg_catalog.pg_policy AS policy
             WHERE policy.polrelid =
                   'operations.expected_new_identity_erasure_impact'::regclass;
            IF actual_policies IS DISTINCT FROM expected_policies THEN
              RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
                USING ERRCODE = '55000';
            END IF;
            DROP TABLE operations.expected_new_identity_erasure_impact;
            DROP TABLE operations.expected_identity_erasure_operation;
          END IF;

          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_attrdef AS default_row
              JOIN pg_catalog.pg_attribute AS default_column
                ON default_column.attrelid = default_row.adrelid
               AND default_column.attnum = default_row.adnum
             WHERE default_row.adrelid = impact
               AND default_column.attname = 'recorded_at'
               AND pg_catalog.pg_get_expr(
                     default_row.adbin, default_row.adrelid
                   ) = 'transaction_timestamp()'
          ) THEN
            RAISE EXCEPTION 'identity_erasure_operation_predecessor_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF operation IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_attrdef AS default_row
              JOIN pg_catalog.pg_attribute AS default_column
                ON default_column.attrelid = default_row.adrelid
               AND default_column.attnum = default_row.adnum
             WHERE default_row.adrelid = operation
               AND default_column.attname = 'recorded_at'
               AND pg_catalog.pg_get_expr(
                     default_row.adbin, default_row.adrelid
                   ) = 'transaction_timestamp()'
          ) THEN
            RAISE EXCEPTION 'identity_erasure_operation_bootstrap_collision'
              USING ERRCODE = '55000';
          END IF;
        END
        $admission$
        """
    )
    bind = op.get_bind()
    return (
        bind.execute(
            text(f"SELECT pg_catalog.to_regclass('{OPERATION_TABLE}')")
        ).scalar_one_or_none()
        is not None
    )


def _install_old_database_shape() -> None:
    op.execute(CreateTable(schema.reviewed_identity_migration_erasure_operations))
    op.execute(
        f"""
        ALTER TABLE {IMPACT_TABLE}
          DROP COLUMN erasure_request_id,
          ADD COLUMN operation_id uuid NOT NULL,
          ADD CONSTRAINT fk_identity_migration_erasure_operation
            FOREIGN KEY (operation_id)
            REFERENCES {OPERATION_TABLE}(operation_id),
          ADD CONSTRAINT identity_erasure_impact_operation
            UNIQUE (run_id, operation_id),
          ADD CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_uuidv7_ids
            CHECK (
              substring(impact_id::text from 15 for 1) = '7'
              AND substring(impact_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1) IN ('8','9','a','b')
            )
        """
    )


def _install_rls_and_acl() -> None:
    operation_comment = (
        "Owner-only, non-authoritative erasure-operation source. Exactly one "
        "privacy erasure request or auto-expiry schedule is referenced."
    )
    impact_comment = (
        "Run-level erasure suspension linked to one normalized erasure "
        "operation after leaf commitments are removed."
    )
    op.execute(f"COMMENT ON TABLE {OPERATION_TABLE} IS '{operation_comment}'")
    op.execute(f"COMMENT ON TABLE {IMPACT_TABLE} IS '{impact_comment}'")
    for table in (OPERATION_TABLE, IMPACT_TABLE):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    for suffix, command in (("select", "SELECT"), ("insert", "INSERT")):
        policy = f"reviewed_identity_migration_erasure_operations_owner_{suffix}"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {OPERATION_TABLE}")
        clause = "USING" if command == "SELECT" else "WITH CHECK"
        op.execute(
            f"CREATE POLICY {policy} ON {OPERATION_TABLE} FOR {command} "
            f"TO home_agent_owner {clause} (session_user = 'home_agent_owner')"
        )

    for suffix, command in (("select", "SELECT"), ("insert", "INSERT")):
        policy = f"reviewed_identity_migration_erasure_impacts_owner_{suffix}"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {IMPACT_TABLE}")
        clause = "USING" if command == "SELECT" else "WITH CHECK"
        op.execute(
            f"CREATE POLICY {policy} ON {IMPACT_TABLE} FOR {command} "
            f"TO home_agent_owner {clause} (session_user = 'home_agent_owner')"
        )
    kernel_policy = "identity_migration_erasure_impacts_manifest_kernel_select"
    op.execute(f"DROP POLICY IF EXISTS {kernel_policy} ON {IMPACT_TABLE}")
    op.execute(
        f"CREATE POLICY {kernel_policy} ON {IMPACT_TABLE} FOR SELECT "
        "TO home_agent_identity_kernel USING ("
        "session_user = 'home_agent_identity_migration' "
        "AND current_user = 'home_agent_identity_kernel' "
        "AND NOT pg_catalog.pg_has_role("
        "session_user, 'home_agent_identity_kernel', 'SET'))"
    )

    roles = ", ".join(NON_OWNER_ROLES)
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {OPERATION_TABLE} "
        f"FROM PUBLIC, {roles}"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {OPERATION_TABLE} TO home_agent_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {IMPACT_TABLE} FROM PUBLIC, {roles}"
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE {IMPACT_TABLE} TO home_agent_owner")
    op.execute(
        f"GRANT SELECT ON TABLE {IMPACT_TABLE} TO home_agent_identity_kernel"
    )


def _assert_installed_contract() -> None:
    finalizer_roles = _sql_array(FINALIZER_ROLES)
    op.execute(
        f"""
        DO $contract$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS table_row
             WHERE table_row.oid = '{OPERATION_TABLE}'::regclass
               AND table_row.relrowsecurity
               AND table_row.relforcerowsecurity
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS table_row
             WHERE table_row.oid = '{IMPACT_TABLE}'::regclass
               AND table_row.relrowsecurity
               AND table_row.relforcerowsecurity
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest({finalizer_roles}) AS role_name
             WHERE pg_catalog.has_table_privilege(
               role_name, '{OPERATION_TABLE}',
               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
             ) OR pg_catalog.has_table_privilege(
               role_name, '{IMPACT_TABLE}',
               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
             )
          ) OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE trigger_row.tgrelid = '{IMPACT_TABLE}'::regclass
               AND trigger_row.tgname =
                   'reviewed_identity_migration_erasure_replay_guard'
               AND trigger_row.tgenabled = 'O'
               AND NOT trigger_row.tgisinternal
          ) THEN
            RAISE EXCEPTION 'identity_erasure_operation_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $contract$
        """
    )


def upgrade() -> None:
    bootstrap_shape = _validate_predecessor_shape()
    if not bootstrap_shape:
        _install_old_database_shape()
    _install_rls_and_acl()
    _assert_installed_contract()


def downgrade() -> None:
    evidence = " OR ".join(
        f"EXISTS (SELECT 1 FROM {table})" for table in DOWNGRADE_EVIDENCE_TABLES
    )
    op.execute(
        f"""
        DO $downgrade$
        BEGIN
          IF {evidence} THEN
            RAISE EXCEPTION 'identity_erasure_operation_downgrade_blocked'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $downgrade$
        """
    )
    op.execute(
        f"""
        ALTER TABLE {IMPACT_TABLE}
          DROP COLUMN operation_id,
          ADD COLUMN erasure_request_id uuid NOT NULL,
          ADD CONSTRAINT fk_identity_migration_erasure_request
            FOREIGN KEY (erasure_request_id)
            REFERENCES privacy.erasure_requests(erasure_request_id),
          ADD CONSTRAINT identity_erasure_impact_request
            UNIQUE (run_id, erasure_request_id),
          ADD CONSTRAINT ck_reviewed_identity_migration_erasure_impacts_uuidv7_ids
            CHECK (
              substring(impact_id::text from 15 for 1) = '7'
              AND substring(impact_id::text from 20 for 1) IN ('8','9','a','b')
              AND substring(erasure_request_id::text from 15 for 1) = '7'
              AND substring(erasure_request_id::text from 20 for 1)
                  IN ('8','9','a','b')
            )
        """
    )
    op.drop_table(
        schema.reviewed_identity_migration_erasure_operations.name,
        schema="operations",
    )
    op.execute(
        f"COMMENT ON TABLE {IMPACT_TABLE} IS "
        "'Run-level erasure suspension after linkable leaf commitments are removed.'"
    )
