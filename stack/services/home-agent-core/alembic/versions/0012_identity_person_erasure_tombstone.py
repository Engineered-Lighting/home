"""Activate deletion-free identity-person retrieval suppression and replay.

Revision ID: 0012_identity_erasure_e2
Revises: 0011_identity_erasure_e1
Create Date: 2026-07-13

E2 still has no live/manual deletion callable.  It evolves E1's dormant block
into the canonical unique-per-person tombstone, makes ledger-v2 replay able to
restore that tombstone without resurrecting deleted source rows, and records a
mandatory normalized residual set that explicitly denies whole-person cleanup
claims.  Typed semantic reads are suppressed with restrictive RLS and typed
writes are rejected by database triggers.  Privacy controls, erasure evidence,
outbox/ledger state, and migration evidence intentionally remain visible to
their existing governed roles.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0012_identity_erasure_e2"
down_revision: str | None = "0011_identity_erasure_e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KERNEL_ROLE = "home_agent_identity_erasure_kernel"
BLOCK_TABLE = "privacy.subject_retrieval_blocks"
RESIDUAL_TABLE = "privacy.identity_person_erasure_residuals"

RUNTIME_SUPPRESSION_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
)

ALL_MANAGED_ROLES = (
    *RUNTIME_SUPPRESSION_ROLES,
    "home_agent_identity_migration",
    "home_agent_identity_kernel",
    "home_agent_identity_finalizer",
    "home_agent_identity_finalizer_kernel",
    KERNEL_ROLE,
)

MANDATORY_RESIDUALS = (
    ("live_identity_rows_retained", "live"),
    ("semantic_dependencies_not_evaluated", "semantic"),
    ("generic_artifact_lineage_unresolved", "lineage"),
    ("external_cleanup_not_evaluated", "external"),
    ("legacy_cleanup_not_evaluated", "legacy"),
    ("backup_expiry_unverified", "backup"),
    ("preexisting_snapshot_visibility", "transaction"),
)

OPTIONAL_RESIDUALS = (("closure_limit_exceeded", "closure"),)

# (table, restrictive expression, anti-resurrection trigger arguments,
#  whether RLS was already enabled by revision 0011)
SUPPRESSION_TARGETS = (
    (
        "identity.people",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        False,
    ),
    (
        "identity.principals",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        False,
    ),
    (
        "identity.ha_user_bindings",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        True,
    ),
    (
        "identity.principal_binding_proposals",
        "NOT privacy.identity_person_is_blocked(person_id) "
        "AND NOT privacy.identity_principal_is_blocked(result_principal_id)",
        ("person:person_id", "principal:result_principal_id"),
        True,
    ),
    (
        "identity.source_entity_bindings",
        "NOT privacy.identity_person_is_blocked(person_id) "
        "AND NOT privacy.identity_principal_is_blocked(principal_id)",
        ("person:person_id", "principal:principal_id"),
        False,
    ),
    (
        "identity.aliases",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        False,
    ),
    (
        "identity.external_recognition_bindings",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        False,
    ),
    (
        "identity.legacy_role_labels",
        "NOT privacy.identity_person_is_blocked(person_id)",
        ("person:person_id",),
        False,
    ),
    (
        "identity.legacy_relationship_candidates",
        "NOT privacy.identity_person_is_blocked(from_person_id) "
        "AND NOT privacy.identity_person_is_blocked(to_person_id)",
        ("person:from_person_id", "person:to_person_id"),
        False,
    ),
    (
        "knowledge.places",
        "NOT privacy.identity_principal_is_blocked(owner_principal_id)",
        ("principal:owner_principal_id",),
        True,
    ),
    (
        "knowledge.place_locators",
        "EXISTS (SELECT 1 FROM knowledge.places AS e2_place "
        "WHERE e2_place.place_id = place_locators.place_id "
        "AND NOT privacy.identity_principal_is_blocked("
        "e2_place.owner_principal_id))",
        (),
        True,
    ),
    (
        "knowledge.visits",
        "NOT privacy.identity_principal_is_blocked(principal_id)",
        ("principal:principal_id",),
        True,
    ),
    (
        "knowledge.memory_transactions",
        "NOT privacy.identity_principal_is_blocked(principal_id)",
        ("principal:principal_id",),
        True,
    ),
    (
        "knowledge.fact_versions",
        "NOT privacy.identity_fact_is_blocked("
        "subject_type::text, subject_id, predicate::text, object, "
        "perspective_principal_id)",
        ("fact:subject_id",),
        True,
    ),
    (
        "knowledge.fact_support",
        "EXISTS (SELECT 1 FROM knowledge.fact_versions AS e2_fact "
        "WHERE e2_fact.fact_version_id = fact_support.fact_version_id "
        "AND NOT privacy.identity_fact_is_blocked("
        "e2_fact.subject_type::text, e2_fact.subject_id, "
        "e2_fact.predicate::text, e2_fact.object, "
        "e2_fact.perspective_principal_id))",
        (),
        True,
    ),
    (
        "engagement.preferences",
        "NOT privacy.identity_principal_is_blocked(principal_id)",
        ("principal:principal_id",),
        True,
    ),
    (
        "engagement.initiatives",
        "NOT privacy.identity_principal_is_blocked(principal_id) AND ("
        "source_fact_version_id IS NULL OR EXISTS ("
        "SELECT 1 FROM knowledge.fact_versions AS e2_fact "
        "WHERE e2_fact.fact_version_id = initiatives.source_fact_version_id "
        "AND NOT privacy.identity_fact_is_blocked("
        "e2_fact.subject_type::text, e2_fact.subject_id, "
        "e2_fact.predicate::text, e2_fact.object, "
        "e2_fact.perspective_principal_id)))",
        ("principal:principal_id",),
        True,
    ),
    (
        "engagement.presentation_attempts",
        "EXISTS (SELECT 1 FROM engagement.initiatives AS e2_initiative "
        "WHERE e2_initiative.initiative_id = "
        "presentation_attempts.initiative_id)",
        (),
        False,
    ),
    (
        "privacy.artifact_registry",
        "NOT privacy.identity_principal_is_blocked(owner_principal_id)",
        ("principal:owner_principal_id",),
        True,
    ),
)


SUPPRESSION_FUNCTIONS = (
    "privacy.identity_person_is_blocked(uuid)",
    "privacy.identity_principal_is_blocked(uuid)",
    "privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)",
)

TRIGGER_FUNCTIONS = (
    "privacy.reject_tombstoned_identity_write()",
    "privacy.enforce_identity_person_erasure_residual_set()",
)

REPLAY_FUNCTION = "privacy.replay_identity_person_retrieval_block_v2(jsonb)"


def _admit_dormant_e1() -> None:
    """Refuse to invent provenance for manually populated dormant E1 rows."""

    op.execute(
        f"""
        DO $e2_admission$
        DECLARE
          owner_oid oid;
          kernel_oid oid;
          actual_columns text[];
          actual_constraints text[];
        BEGIN
          SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT kernel_oid FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          IF (SELECT version_num FROM public.alembic_version)
             IS DISTINCT FROM '0011_identity_erasure_e1' THEN
            RAISE EXCEPTION 'identity_erasure_e2_revision_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
             WHERE rolname = '{KERNEL_ROLE}'
               AND rolcanlogin = false
               AND rolinherit = false
               AND rolsuper = false
               AND rolcreatedb = false
               AND rolcreaterole = false
               AND rolreplication = false
               AND rolbypassrls = false
               AND rolconnlimit = 0
               AND rolvaliduntil IS NULL
               AND rolconfig IS NULL
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_kernel_role_invalid'
              USING ERRCODE = '42704';
          END IF;
          IF (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_auth_members
             WHERE roleid = kernel_oid
          ) <> 1 OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_auth_members AS membership
             WHERE membership.roleid = kernel_oid
               AND membership.member = owner_oid
               AND membership.admin_option = false
               AND membership.inherit_option = false
               AND membership.set_option = true
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members WHERE member = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_shdepend
             WHERE refobjid = kernel_oid AND deptype = 'o'
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc WHERE proowner = kernel_oid
          ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_database AS database_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace AS namespace_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(namespace_row.nspacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_class AS relation_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(relation_row.relacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_attribute AS attribute
             CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_type AS type_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(type_row.typacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_default_acl AS default_acl
             CROSS JOIN LATERAL
               pg_catalog.aclexplode(default_acl.defaclacl) acl
             WHERE acl.grantee = kernel_oid
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_parameter_acl AS parameter_acl
             CROSS JOIN LATERAL
               pg_catalog.aclexplode(parameter_acl.paracl) acl
             WHERE acl.grantee = kernel_oid
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_kernel_authority_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT pg_catalog.array_agg(
                   attribute.attname::text || ':' ||
                   pg_catalog.format_type(
                     attribute.atttypid, attribute.atttypmod
                   ) || ':' || attribute.attnotnull::text || ':' ||
                   COALESCE(
                     pg_catalog.pg_get_expr(
                       default_value.adbin, default_value.adrelid
                     ),
                     ''
                   )
                   ORDER BY attribute.attnum
                 )
            INTO actual_columns
            FROM pg_catalog.pg_attribute AS attribute
            LEFT JOIN pg_catalog.pg_attrdef AS default_value
              ON default_value.adrelid = attribute.attrelid
             AND default_value.adnum = attribute.attnum
           WHERE attribute.attrelid = '{BLOCK_TABLE}'::regclass
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped;
          IF actual_columns IS DISTINCT FROM ARRAY[
            'block_id:uuid:true:',
            'operation_id:uuid:true:',
            'person_id:uuid:true:',
            'source_kind:character varying(32):true:',
            'erasure_request_id:uuid:true:',
            'block_code:character varying(32):true:',
            'block_commitment:character varying(64):true:',
            'created_at:timestamp with time zone:true:transaction_timestamp()'
          ]::text[] THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_shape_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT pg_catalog.array_agg(
                   constraint_row.conname::text || ':' ||
                   constraint_row.contype::text || ':' ||
                   constraint_row.convalidated::text || ':' ||
                   constraint_row.condeferrable::text || ':' ||
                   constraint_row.condeferred::text
                   ORDER BY constraint_row.conname
                 )
            INTO actual_constraints
            FROM pg_catalog.pg_constraint AS constraint_row
           WHERE constraint_row.conrelid = '{BLOCK_TABLE}'::regclass;
          IF actual_constraints IS DISTINCT FROM ARRAY[
            'ck_subject_retrieval_block_code:c:true:false:false',
            'ck_subject_retrieval_block_commitment:c:true:false:false',
            'ck_subject_retrieval_block_exact_source:c:true:false:false',
            'ck_subject_retrieval_block_uuidv7:c:true:false:false',
            'fk_subject_retrieval_block_operation:f:true:false:false',
            'fk_subject_retrieval_block_request_operation:f:true:false:false',
            'fk_subject_retrieval_block_request_person:f:true:false:false',
            'identity_erasure_block_commitment:u:true:false:false',
            'identity_erasure_block_operation:u:true:false:false',
            'subject_retrieval_blocks_pkey:p:true:false:false'
          ]::text[] OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
             WHERE conrelid = '{BLOCK_TABLE}'::regclass
               AND conname = 'ck_subject_retrieval_block_exact_source'
               AND pg_catalog.regexp_replace(
                     pg_catalog.pg_get_constraintdef(oid, true),
                     '[[:space:]()]', '', 'g'
                   ) =
                   'CHECKsource_kind::text=''erasure_request''::text'
          ) OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
             WHERE conrelid = '{BLOCK_TABLE}'::regclass
               AND conname = 'fk_subject_retrieval_block_operation'
               AND confrelid =
                   'operations.reviewed_identity_migration_erasure_operations'
                   ::regclass
          ) OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
             WHERE conrelid = '{BLOCK_TABLE}'::regclass
               AND conname = 'fk_subject_retrieval_block_request_person'
               AND confrelid = 'privacy.person_erasure_scopes'::regclass
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_constraint_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class AS block
             WHERE block.oid = '{BLOCK_TABLE}'::regclass
               AND block.relkind = 'r'
               AND block.relpersistence = 'p'
               AND block.relowner = owner_oid
               AND block.relrowsecurity
               AND block.relforcerowsecurity
               AND block.relacl::text =
                   '{{home_agent_owner=arwdDxtm/home_agent_owner}}'
               AND NOT EXISTS (
                 SELECT 1 FROM pg_catalog.pg_attribute AS attribute
                  WHERE attribute.attrelid = block.oid
                    AND attribute.attnum > 0
                    AND NOT attribute.attisdropped
                    AND attribute.attacl IS NOT NULL
               )
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_acl_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger
             WHERE tgrelid = '{BLOCK_TABLE}'::regclass
               AND NOT tgisinternal
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_rewrite
             WHERE ev_class = '{BLOCK_TABLE}'::regclass
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_event_trigger
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_execution_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF (SELECT pg_catalog.count(*) FROM pg_catalog.pg_policy
               WHERE polrelid = '{BLOCK_TABLE}'::regclass) <> 2
             OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_policy
                WHERE polrelid = '{BLOCK_TABLE}'::regclass
                  AND polname = 'subject_retrieval_blocks_owner_select'
                  AND polcmd = 'r' AND polpermissive
                  AND polroles = ARRAY[owner_oid]::oid[]
                  AND pg_catalog.regexp_replace(
                        pg_catalog.lower(
                          pg_catalog.pg_get_expr(polqual, polrelid)
                        ), '[[:space:]()]', '', 'g'
                      ) = 'session_user=''home_agent_owner''::name'
                  AND polwithcheck IS NULL
             ) OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_policy
                WHERE polrelid = '{BLOCK_TABLE}'::regclass
                  AND polname = 'subject_retrieval_blocks_owner_insert'
                  AND polcmd = 'a' AND polpermissive
                  AND polroles = ARRAY[owner_oid]::oid[]
                  AND polqual IS NULL
                  AND pg_catalog.regexp_replace(
                        pg_catalog.lower(
                          pg_catalog.pg_get_expr(polwithcheck, polrelid)
                        ), '[[:space:]()]', '', 'g'
                      ) = 'session_user=''home_agent_owner''::name'
             ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_policy_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (SELECT 1 FROM {BLOCK_TABLE}) THEN
            RAISE EXCEPTION 'identity_erasure_e2_existing_block_requires_review'
              USING ERRCODE = '55000';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM operations.reviewed_identity_migration_erasure_receipts
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_existing_receipt_requires_review'
              USING ERRCODE = '55000';
          END IF;
          IF pg_catalog.to_regclass('{RESIDUAL_TABLE}') IS NOT NULL THEN
            RAISE EXCEPTION 'identity_erasure_e2_name_occupied'
              USING ERRCODE = '42P07';
          END IF;
        END
        $e2_admission$
        """
    )
    _validate_exact_e1_block_constraints()


def _validate_exact_e1_block_constraints() -> None:
    """Compare every predecessor constraint with an independently built twin."""

    op.execute(
        """
        CREATE SCHEMA e2_validation AUTHORIZATION home_agent_owner;
        CREATE TABLE e2_validation.expected_subject_retrieval_blocks (
          block_id uuid,
          operation_id uuid NOT NULL,
          person_id uuid NOT NULL,
          source_kind varchar(32) NOT NULL,
          erasure_request_id uuid NOT NULL,
          block_code varchar(32) NOT NULL,
          block_commitment varchar(64) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          CONSTRAINT subject_retrieval_blocks_pkey PRIMARY KEY (block_id),
          CONSTRAINT fk_subject_retrieval_block_operation
            FOREIGN KEY (operation_id)
            REFERENCES operations.reviewed_identity_migration_erasure_operations(
              operation_id
            ),
          CONSTRAINT fk_subject_retrieval_block_request_operation
            FOREIGN KEY (operation_id, erasure_request_id)
            REFERENCES operations.reviewed_identity_migration_erasure_operations(
              operation_id, erasure_request_id
            ),
          CONSTRAINT fk_subject_retrieval_block_request_person
            FOREIGN KEY (erasure_request_id, person_id)
            REFERENCES privacy.person_erasure_scopes(
              erasure_request_id, person_id
            ),
          CONSTRAINT ck_subject_retrieval_block_exact_source
            CHECK (source_kind = 'erasure_request'),
          CONSTRAINT identity_erasure_block_operation UNIQUE (operation_id),
          CONSTRAINT identity_erasure_block_commitment UNIQUE (block_commitment),
          CONSTRAINT ck_subject_retrieval_block_code
            CHECK (block_code = 'identity_person_erasure'),
          CONSTRAINT ck_subject_retrieval_block_uuidv7 CHECK (
            substring(block_id::text from 15 for 1) = '7'
            AND substring(block_id::text from 20 for 1) IN ('8','9','a','b')
            AND substring(operation_id::text from 15 for 1) = '7'
            AND substring(operation_id::text from 20 for 1) IN ('8','9','a','b')
          ),
          CONSTRAINT ck_subject_retrieval_block_commitment
            CHECK (block_commitment ~ '^[0-9a-f]{64}$')
        );

        DO $exact_block_constraints$
        BEGIN
          IF EXISTS (
            SELECT actual.conname, actual.contype,
                   pg_catalog.pg_get_constraintdef(actual.oid, true)
              FROM pg_catalog.pg_constraint AS actual
             WHERE actual.conrelid =
                   'privacy.subject_retrieval_blocks'::regclass
            EXCEPT
            SELECT expected.conname, expected.contype,
                   pg_catalog.pg_get_constraintdef(expected.oid, true)
              FROM pg_catalog.pg_constraint AS expected
             WHERE expected.conrelid =
                   'e2_validation.expected_subject_retrieval_blocks'::regclass
          ) OR EXISTS (
            SELECT expected.conname, expected.contype,
                   pg_catalog.pg_get_constraintdef(expected.oid, true)
              FROM pg_catalog.pg_constraint AS expected
             WHERE expected.conrelid =
                   'e2_validation.expected_subject_retrieval_blocks'::regclass
            EXCEPT
            SELECT actual.conname, actual.contype,
                   pg_catalog.pg_get_constraintdef(actual.oid, true)
              FROM pg_catalog.pg_constraint AS actual
             WHERE actual.conrelid =
                   'privacy.subject_retrieval_blocks'::regclass
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_block_constraint_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $exact_block_constraints$;
        DROP SCHEMA e2_validation CASCADE;
        """
    )


def _evolve_tombstone() -> None:
    op.execute(
        f"""
        ALTER TABLE {BLOCK_TABLE}
          DROP CONSTRAINT ck_subject_retrieval_block_exact_source;
        ALTER TABLE {BLOCK_TABLE}
          ALTER COLUMN operation_id DROP NOT NULL,
          ALTER COLUMN erasure_request_id DROP NOT NULL,
          ADD COLUMN outcome_code varchar(32) NOT NULL
            DEFAULT 'retrieval_block_active',
          ADD COLUMN ledger_epoch bigint,
          ADD COLUMN ledger_outbox_id uuid,
          ADD COLUMN ledger_record_hash varchar(64),
          ADD COLUMN ledger_record_digest varchar(64),
          ADD COLUMN ledger_attached_at timestamptz;
        ALTER TABLE {BLOCK_TABLE}
          ALTER COLUMN outcome_code DROP DEFAULT,
          ADD CONSTRAINT identity_erasure_block_person UNIQUE (person_id),
          ADD CONSTRAINT identity_erasure_block_identity
            UNIQUE (block_id, person_id),
          ADD CONSTRAINT identity_erasure_block_ledger_epoch
            UNIQUE (ledger_epoch),
          ADD CONSTRAINT identity_erasure_block_ledger_outbox
            UNIQUE (ledger_outbox_id),
          ADD CONSTRAINT ck_subject_retrieval_block_exact_source CHECK (
            (source_kind = 'erasure_request'
             AND operation_id IS NOT NULL
             AND erasure_request_id IS NOT NULL
             AND (
               (ledger_epoch IS NULL
                AND ledger_outbox_id IS NULL
                AND ledger_record_hash IS NULL
                AND ledger_record_digest IS NULL
                AND ledger_attached_at IS NULL)
               OR
               (ledger_epoch IS NOT NULL
                AND ledger_outbox_id IS NOT NULL
                AND ledger_record_hash IS NOT NULL
                AND ledger_record_digest IS NOT NULL
                AND ledger_attached_at IS NOT NULL)
             ))
            OR
            (source_kind = 'ledger_replay'
             AND operation_id IS NULL
             AND erasure_request_id IS NULL
             AND ledger_epoch IS NOT NULL
             AND ledger_outbox_id IS NOT NULL
             AND ledger_record_hash IS NOT NULL
             AND ledger_record_digest IS NOT NULL
             AND ledger_attached_at IS NOT NULL)
          ),
          ADD CONSTRAINT ck_subject_retrieval_block_outcome CHECK (
            outcome_code = 'retrieval_block_active'
          ),
          ADD CONSTRAINT ck_subject_retrieval_block_ledger_shape CHECK (
            ledger_epoch IS NULL OR (
              ledger_epoch > 0
              AND ledger_record_hash ~ '^[0-9a-f]{{64}}$'
              AND ledger_record_digest ~ '^[0-9a-f]{{64}}$'
            )
          ),
          ADD CONSTRAINT ck_subject_retrieval_block_e2_uuidv7 CHECK (
            substring(block_id::text from 15 for 1) = '7'
            AND substring(block_id::text from 20 for 1) IN ('8','9','a','b')
            AND (operation_id IS NULL OR (
              substring(operation_id::text from 15 for 1) = '7'
              AND substring(operation_id::text from 20 for 1)
                IN ('8','9','a','b')
            ))
          );
        """
    )


def _install_residual_table() -> None:
    code_class = ",".join(
        f"('{code}','{residual_class}')"
        for code, residual_class in (*MANDATORY_RESIDUALS, *OPTIONAL_RESIDUALS)
    )
    op.execute(
        f"""
        CREATE TABLE {RESIDUAL_TABLE} (
          block_id uuid NOT NULL,
          residual_code varchar(64) NOT NULL,
          person_id uuid NOT NULL,
          residual_class varchar(32) NOT NULL,
          state varchar(16) NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
          resolved_at timestamptz,
          residual_commitment varchar(64) NOT NULL,
          CONSTRAINT pk_identity_person_erasure_residuals
            PRIMARY KEY (block_id, residual_code),
          CONSTRAINT fk_identity_person_erasure_residual_block
            FOREIGN KEY (block_id, person_id)
            REFERENCES {BLOCK_TABLE}(block_id, person_id),
          CONSTRAINT identity_person_erasure_residual_commitment
            UNIQUE (residual_commitment),
          CONSTRAINT ck_identity_person_erasure_residual_code_class CHECK (
            (residual_code, residual_class) IN ({code_class})
          ),
          CONSTRAINT ck_identity_person_erasure_residual_state CHECK (
            (state = 'open' AND resolved_at IS NULL)
            OR
            (state = 'resolved' AND resolved_at IS NOT NULL
             AND resolved_at >= recorded_at)
          ),
          CONSTRAINT ck_identity_person_erasure_residual_commitment CHECK (
            residual_commitment ~ '^[0-9a-f]{{64}}$'
          )
        );

        COMMENT ON TABLE {BLOCK_TABLE} IS
          'Canonical unique-per-person retrieval tombstone; E2 outcome is '
          'retrieval suppression, never a claim of physical whole-person erasure.';
        COMMENT ON TABLE {RESIDUAL_TABLE} IS
          'Normalized mandatory limits on E2 identity-person retrieval suppression.';

        ALTER TABLE {RESIDUAL_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {RESIDUAL_TABLE} FORCE ROW LEVEL SECURITY;
        CREATE POLICY identity_person_erasure_residuals_owner_select
          ON {RESIDUAL_TABLE} FOR SELECT TO home_agent_owner
          USING (session_user = 'home_agent_owner');
        CREATE POLICY identity_person_erasure_residuals_owner_insert
          ON {RESIDUAL_TABLE} FOR INSERT TO home_agent_owner
          WITH CHECK (session_user = 'home_agent_owner');
        """
    )


def _install_kernel_functions() -> None:
    mandatory_codes = ",".join(f"'{code}'" for code, _ in MANDATORY_RESIDUALS)
    mandatory_values = ",".join(
        f"('{code}','{residual_class}')" for code, residual_class in MANDATORY_RESIDUALS
    )
    mandatory_residual_json = (
        "[" + ",".join(f'"{code}"' for code, _ in MANDATORY_RESIDUALS) + "]"
    )
    replay_keys = (
        "backup_expiry_at",
        "block_commitment",
        "block_id",
        "checkpoint_affected",
        "completed_at",
        "erasure_request_id",
        "exact_residual_codes",
        "external_pending_codes",
        "ledger_epoch",
        "ledger_record_digest",
        "ledger_record_hash",
        "legacy_untracked_codes",
        "operation_codes",
        "operation_id",
        "outbox_id",
        "outcome_code",
        "person_id",
        "policy_digest",
        "residual_commitments",
        "source_created_at",
        "subject_kind",
        "version",
    )
    replay_key_array = ",".join(f"'{key}'" for key in replay_keys)

    # E2 gives the NOLOGIN kernel only the two narrow lookup projections used
    # by the suppression predicates.  It receives no residual or semantic
    # table access and no DML authority.
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA identity, privacy TO {KERNEL_ROLE};
        GRANT SELECT (person_id) ON TABLE {BLOCK_TABLE} TO {KERNEL_ROLE};
        GRANT SELECT (principal_id, person_id)
          ON TABLE identity.principals TO {KERNEL_ROLE};
        CREATE POLICY subject_retrieval_blocks_e2_kernel_lookup
          ON {BLOCK_TABLE} FOR SELECT TO {KERNEL_ROLE}
          USING (current_user = '{KERNEL_ROLE}');

        GRANT CREATE ON SCHEMA privacy TO {KERNEL_ROLE};
        SET LOCAL ROLE {KERNEL_ROLE};

        CREATE FUNCTION privacy.identity_person_is_blocked(target_person_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
          SELECT target_person_id IS NOT NULL
             AND EXISTS (
               SELECT 1
                 FROM privacy.subject_retrieval_blocks AS block
                WHERE block.person_id = target_person_id
             )
        $function$;

        CREATE FUNCTION privacy.identity_principal_is_blocked(
          target_principal_id uuid
        ) RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
          SELECT target_principal_id IS NOT NULL
             AND EXISTS (
               SELECT 1
                 FROM identity.principals AS principal
                WHERE principal.principal_id = target_principal_id
                  AND privacy.identity_person_is_blocked(principal.person_id)
             )
        $function$;

        CREATE FUNCTION privacy.identity_fact_is_blocked(
          target_subject_type text,
          target_subject_id uuid,
          target_predicate text,
          target_object jsonb,
          target_perspective_principal_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          object_person_text text;
        BEGIN
          IF privacy.identity_principal_is_blocked(
               target_perspective_principal_id
             ) THEN
            RETURN true;
          END IF;
          IF target_subject_type = 'person'
             AND privacy.identity_person_is_blocked(target_subject_id) THEN
            RETURN true;
          END IF;
          IF target_predicate <> 'parent_of'
             OR pg_catalog.jsonb_typeof(target_object) <> 'object' THEN
            RETURN false;
          END IF;
          object_person_text := target_object ->> 'person_id';
          IF object_person_text IS NULL
             OR object_person_text !~*
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-'
               '[0-9a-f]{{4}}-[0-9a-f]{{12}}$' THEN
            RETURN false;
          END IF;
          RETURN privacy.identity_person_is_blocked(object_person_text::uuid);
        END
        $function$;

        CREATE FUNCTION privacy.reject_tombstoned_identity_write()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          row_value jsonb := pg_catalog.to_jsonb(NEW);
          argument text;
          argument_kind text;
          column_name text;
          identifier_text text;
          blocked boolean := false;
        BEGIN
          FOREACH argument IN ARRAY TG_ARGV LOOP
            argument_kind := pg_catalog.split_part(argument, ':', 1);
            column_name := pg_catalog.split_part(argument, ':', 2);
            identifier_text := row_value ->> column_name;
            IF identifier_text IS NULL THEN
              CONTINUE;
            END IF;
            IF argument_kind = 'person' THEN
              blocked := privacy.identity_person_is_blocked(
                identifier_text::uuid
              );
            ELSIF argument_kind = 'principal' THEN
              blocked := privacy.identity_principal_is_blocked(
                identifier_text::uuid
              );
            ELSIF argument_kind = 'fact' THEN
              blocked := privacy.identity_fact_is_blocked(
                row_value ->> 'subject_type',
                (row_value ->> 'subject_id')::uuid,
                row_value ->> 'predicate',
                row_value -> 'object',
                NULLIF(row_value ->> 'perspective_principal_id', '')::uuid
              );
            ELSE
              RAISE EXCEPTION 'identity_erasure_e2_trigger_argument_invalid'
                USING ERRCODE = '22023';
            END IF;
            IF blocked THEN
              RAISE EXCEPTION 'identity_person_retrieval_block_active'
                USING ERRCODE = '23514';
            END IF;
          END LOOP;
          RETURN NEW;
        END
        $function$;

        CREATE FUNCTION privacy.enforce_identity_person_erasure_residual_set()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          target_block_id uuid := COALESCE(NEW.block_id, OLD.block_id);
          mandatory_open_count integer;
          unexpected_count integer;
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM privacy.subject_retrieval_blocks AS block
             WHERE block.block_id = target_block_id
          ) THEN
            IF TG_OP = 'DELETE' THEN
              RETURN OLD;
            END IF;
            RETURN NEW;
          END IF;
          SELECT
            pg_catalog.count(*) FILTER (
              WHERE residual.residual_code IN ({mandatory_codes})
                AND residual.state = 'open'
                AND residual.resolved_at IS NULL
            ),
            pg_catalog.count(*) FILTER (
              WHERE residual.residual_code NOT IN (
                {mandatory_codes}, 'closure_limit_exceeded'
              )
            )
            INTO mandatory_open_count, unexpected_count
            FROM privacy.identity_person_erasure_residuals AS residual
           WHERE residual.block_id = target_block_id;
          IF mandatory_open_count <> {len(MANDATORY_RESIDUALS)}
             OR unexpected_count <> 0 THEN
            RAISE EXCEPTION 'identity_person_erasure_residual_set_incomplete'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END
        $function$;

        RESET ROLE;
        REVOKE CREATE ON SCHEMA privacy FROM {KERNEL_ROLE};
        """
    )

    # Replay is deliberately owner-owned: the NOLOGIN lookup kernel stays
    # DML-free. FORCE-RLS policies below bind its only caller to the erasure
    # session role and to ledger_replay rows.
    op.execute(
        f"""
        CREATE FUNCTION privacy.replay_identity_person_retrieval_block_v2(
          replay jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        DECLARE
          actual_keys text[];
          residual_count integer;
          residual_distinct_count integer;
          unexpected_residual_count integer;
          block_id_value uuid;
          person_id_value uuid;
          operation_id_value uuid;
          erasure_request_id_value uuid;
          ledger_outbox_id_value uuid;
          ledger_epoch_value bigint;
          block_commitment_value text;
          ledger_record_hash_value text;
          ledger_record_digest_value text;
          source_created_at_value timestamptz;
          completed_at_value timestamptz;
          residual_code_value text;
          residual_class_value text;
          residual_commitment_value text;
          expected_residual_commitment text;
          existing_block record;
          existing_residual record;
          operation_source_exists boolean;
          scope_source_exists boolean;
          operation_source_conflicts boolean;
          scope_source_conflicts boolean;
          stored_source_kind text;
          stored_operation_id uuid;
          stored_erasure_request_id uuid;
        BEGIN
          IF replay IS NULL OR pg_catalog.jsonb_typeof(replay) <> 'object' THEN
            RAISE EXCEPTION 'identity_person_replay_payload_invalid'
              USING ERRCODE = '22023';
          END IF;
          SELECT pg_catalog.array_agg(key ORDER BY key)
            INTO actual_keys
            FROM pg_catalog.jsonb_object_keys(replay) AS replay_key(key);
          IF actual_keys IS DISTINCT FROM ARRAY[{replay_key_array}]::text[] THEN
            RAISE EXCEPTION 'identity_person_replay_payload_invalid'
              USING ERRCODE = '22023';
          END IF;
          IF replay ->> 'version' <> '2'
             OR replay ->> 'subject_kind' <> 'identity_person'
             OR replay ->> 'outcome_code' <> 'retrieval_block_active'
             OR replay -> 'operation_codes'
                <> '["activate_identity_person_retrieval_block"]'::jsonb
             OR replay -> 'checkpoint_affected' <> 'true'::jsonb
             OR replay -> 'backup_expiry_at' <> 'null'::jsonb
             OR pg_catalog.jsonb_typeof(replay -> 'external_pending_codes')
                <> 'array'
             OR pg_catalog.jsonb_typeof(replay -> 'legacy_untracked_codes')
                <> 'array'
             OR pg_catalog.jsonb_typeof(replay -> 'exact_residual_codes')
                <> 'array'
             OR pg_catalog.jsonb_typeof(replay -> 'residual_commitments')
                <> 'object'
             OR replay -> 'exact_residual_codes'
                <> '{mandatory_residual_json}'::jsonb THEN
            RAISE EXCEPTION 'identity_person_replay_payload_invalid'
              USING ERRCODE = '22023';
          END IF;

          SELECT pg_catalog.count(*), pg_catalog.count(DISTINCT code),
                 pg_catalog.count(*) FILTER (
                   WHERE code NOT IN ({mandatory_codes})
                 )
            INTO residual_count, residual_distinct_count,
                 unexpected_residual_count
            FROM pg_catalog.jsonb_array_elements_text(
              replay -> 'exact_residual_codes'
            ) AS residual_code_row(code);
          IF residual_count <> {len(MANDATORY_RESIDUALS)}
             OR residual_distinct_count <> {len(MANDATORY_RESIDUALS)}
             OR unexpected_residual_count <> 0 THEN
            RAISE EXCEPTION 'identity_person_replay_residual_set_invalid'
              USING ERRCODE = '22023';
          END IF;
          SELECT pg_catalog.count(*), pg_catalog.count(*) FILTER (
                   WHERE key NOT IN ({mandatory_codes})
                 )
            INTO residual_count, unexpected_residual_count
            FROM pg_catalog.jsonb_object_keys(
              replay -> 'residual_commitments'
            ) AS residual_key(key);
          IF residual_count <> {len(MANDATORY_RESIDUALS)}
             OR unexpected_residual_count <> 0 THEN
            RAISE EXCEPTION 'identity_person_replay_residual_set_invalid'
              USING ERRCODE = '22023';
          END IF;

          block_id_value := (replay ->> 'block_id')::uuid;
          person_id_value := (replay ->> 'person_id')::uuid;
          operation_id_value := (replay ->> 'operation_id')::uuid;
          erasure_request_id_value := (replay ->> 'erasure_request_id')::uuid;
          ledger_outbox_id_value := (replay ->> 'outbox_id')::uuid;
          ledger_epoch_value := (replay ->> 'ledger_epoch')::bigint;
          block_commitment_value := replay ->> 'block_commitment';
          ledger_record_hash_value := replay ->> 'ledger_record_hash';
          ledger_record_digest_value := replay ->> 'ledger_record_digest';
          source_created_at_value :=
            (replay ->> 'source_created_at')::timestamptz;
          completed_at_value := (replay ->> 'completed_at')::timestamptz;

          IF operation_id_value IS NULL OR erasure_request_id_value IS NULL
             OR ledger_epoch_value <= 0
             OR completed_at_value < source_created_at_value
             OR completed_at_value > transaction_timestamp()
             OR block_commitment_value !~ '^[0-9a-f]{{64}}$'
             OR replay ->> 'policy_digest' !~ '^[0-9a-f]{{64}}$'
             OR ledger_record_hash_value !~ '^[0-9a-f]{{64}}$'
             OR ledger_record_digest_value !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'identity_person_replay_payload_invalid'
              USING ERRCODE = '22023';
          END IF;

          SELECT EXISTS (
                   SELECT 1
                     FROM operations.reviewed_identity_migration_erasure_operations
                          AS operation
                    WHERE operation.operation_id = operation_id_value
                      AND operation.erasure_request_id =
                          erasure_request_id_value
                      AND operation.source_kind = 'erasure_request'
                 ),
                 EXISTS (
                   SELECT 1
                     FROM operations.reviewed_identity_migration_erasure_operations
                          AS operation
                    WHERE operation.operation_id = operation_id_value
                       OR operation.erasure_request_id =
                          erasure_request_id_value
                 )
            INTO operation_source_exists, operation_source_conflicts;
          SELECT EXISTS (
                   SELECT 1
                     FROM privacy.person_erasure_scopes AS scope
                    WHERE scope.erasure_request_id = erasure_request_id_value
                      AND scope.person_id = person_id_value
                 ),
                 EXISTS (
                   SELECT 1
                     FROM privacy.person_erasure_scopes AS scope
                    WHERE scope.erasure_request_id = erasure_request_id_value
                 )
            INTO scope_source_exists, scope_source_conflicts;
          IF operation_source_exists IS DISTINCT FROM scope_source_exists
             OR (
               NOT operation_source_exists
               AND (operation_source_conflicts OR scope_source_conflicts)
             ) THEN
            RAISE EXCEPTION 'identity_person_replay_source_graph_invalid'
              USING ERRCODE = '23503';
          END IF;
          IF operation_source_exists THEN
            stored_source_kind := 'erasure_request';
            stored_operation_id := operation_id_value;
            stored_erasure_request_id := erasure_request_id_value;
          ELSE
            stored_source_kind := 'ledger_replay';
            stored_operation_id := NULL;
            stored_erasure_request_id := NULL;
          END IF;

          BEGIN
            INSERT INTO privacy.subject_retrieval_blocks (
              block_id, operation_id, person_id, source_kind,
              erasure_request_id, block_code, outcome_code,
              block_commitment, ledger_epoch, ledger_outbox_id,
              ledger_record_hash, ledger_record_digest, ledger_attached_at,
              created_at
            ) VALUES (
              block_id_value, stored_operation_id, person_id_value,
              stored_source_kind, stored_erasure_request_id,
              'identity_person_erasure', 'retrieval_block_active',
              block_commitment_value, ledger_epoch_value,
              ledger_outbox_id_value, ledger_record_hash_value,
              ledger_record_digest_value, transaction_timestamp(),
              source_created_at_value
            );
          EXCEPTION WHEN unique_violation THEN
            NULL;
          END;

          SELECT * INTO existing_block
            FROM privacy.subject_retrieval_blocks AS block
           WHERE block.person_id = person_id_value
           FOR UPDATE;
          IF NOT FOUND
             OR existing_block.block_id IS DISTINCT FROM block_id_value
             OR existing_block.source_kind IS DISTINCT FROM stored_source_kind
             OR existing_block.operation_id IS DISTINCT FROM stored_operation_id
             OR existing_block.erasure_request_id IS DISTINCT FROM
                stored_erasure_request_id
             OR existing_block.block_code IS DISTINCT FROM
                'identity_person_erasure'
             OR existing_block.outcome_code IS DISTINCT FROM
                'retrieval_block_active'
             OR existing_block.block_commitment IS DISTINCT FROM
                block_commitment_value
             OR existing_block.ledger_epoch IS DISTINCT FROM ledger_epoch_value
             OR existing_block.ledger_outbox_id IS DISTINCT FROM
                ledger_outbox_id_value
             OR existing_block.ledger_record_hash IS DISTINCT FROM
                ledger_record_hash_value
             OR existing_block.ledger_record_digest IS DISTINCT FROM
                ledger_record_digest_value
             OR existing_block.created_at IS DISTINCT FROM
                source_created_at_value THEN
            RAISE EXCEPTION 'identity_person_replay_conflict'
              USING ERRCODE = '23505';
          END IF;

          FOR residual_code_value, residual_class_value IN
            SELECT * FROM (VALUES {mandatory_values})
              AS required(residual_code, residual_class)
          LOOP
            residual_commitment_value :=
              replay -> 'residual_commitments' ->> residual_code_value;
            expected_residual_commitment := pg_catalog.encode(
              pg_catalog.sha256(pg_catalog.convert_to(
                'identity-person-erasure-residual-v2:'
                || ledger_record_digest_value || ':' || residual_code_value,
                'UTF8'
              )),
              'hex'
            );
            IF residual_commitment_value IS DISTINCT FROM
                 expected_residual_commitment THEN
              RAISE EXCEPTION 'identity_person_replay_residual_commitment_invalid'
                USING ERRCODE = '22023';
            END IF;
            BEGIN
              INSERT INTO privacy.identity_person_erasure_residuals (
                block_id, residual_code, person_id, residual_class, state,
                residual_commitment
              ) VALUES (
                block_id_value, residual_code_value, person_id_value,
                residual_class_value, 'open', residual_commitment_value
              ) ON CONFLICT (block_id, residual_code) DO NOTHING;
            EXCEPTION WHEN unique_violation THEN
              NULL;
            END;
            SELECT * INTO existing_residual
              FROM privacy.identity_person_erasure_residuals AS residual
             WHERE residual.block_id = block_id_value
               AND residual.residual_code = residual_code_value;
            IF NOT FOUND
               OR existing_residual.person_id IS DISTINCT FROM person_id_value
               OR existing_residual.residual_class IS DISTINCT FROM
                  residual_class_value
               OR existing_residual.state IS DISTINCT FROM 'open'
               OR existing_residual.resolved_at IS NOT NULL
               OR existing_residual.residual_commitment IS DISTINCT FROM
                  residual_commitment_value THEN
              RAISE EXCEPTION 'identity_person_replay_conflict'
                USING ERRCODE = '23505';
            END IF;
          END LOOP;

          SET CONSTRAINTS
            privacy.identity_person_erasure_residual_set_from_block,
            privacy.identity_person_erasure_residual_set_from_residual
            IMMEDIATE;
          SET CONSTRAINTS
            privacy.identity_person_erasure_residual_set_from_block,
            privacy.identity_person_erasure_residual_set_from_residual
            DEFERRED;
          RETURN block_id_value;
        END
        $function$;
        """
    )


def _install_exact_set_triggers() -> None:
    op.execute(
        f"""
        CREATE CONSTRAINT TRIGGER
          identity_person_erasure_residual_set_from_block
        AFTER INSERT OR UPDATE ON {BLOCK_TABLE}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
          privacy.enforce_identity_person_erasure_residual_set();
        CREATE CONSTRAINT TRIGGER
          identity_person_erasure_residual_set_from_residual
        AFTER INSERT OR UPDATE OR DELETE ON {RESIDUAL_TABLE}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION
          privacy.enforce_identity_person_erasure_residual_set();
        """
    )


def _install_suppression_boundary() -> None:
    runtime_roles = ", ".join(RUNTIME_SUPPRESSION_ROLES)
    for table, expression, trigger_arguments, predecessor_rls in SUPPRESSION_TARGETS:
        policy_base = table.replace(".", "_")
        if not predecessor_rls:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY {policy_base}_e2_acl_preservation "
                f"ON {table} AS PERMISSIVE FOR ALL TO PUBLIC "
                "USING (true) WITH CHECK (true)"
            )
        else:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy_base}_e2_identity_suppression "
            f"ON {table} AS RESTRICTIVE FOR ALL TO {runtime_roles} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )
        if trigger_arguments:
            arguments = ", ".join(f"'{argument}'" for argument in trigger_arguments)
            op.execute(
                f"CREATE TRIGGER {policy_base}_e2_anti_resurrection "
                f"BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW "
                "EXECUTE FUNCTION privacy.reject_tombstoned_identity_write("
                f"{arguments})"
            )

    # The kernel-owned principal lookup must not recursively inherit the
    # runtime-only restrictive policy.  It has only the two projected columns
    # and this exact SELECT policy; the role is NOLOGIN and has no membership.
    op.execute(
        f"""
        CREATE POLICY principals_e2_erasure_kernel_lookup
          ON identity.principals FOR SELECT TO {KERNEL_ROLE}
          USING (current_user = '{KERNEL_ROLE}');
        """
    )


def _contract_e2_acl() -> None:
    all_roles = ", ".join(ALL_MANAGED_ROLES)
    suppression_roles = ", ".join(RUNTIME_SUPPRESSION_ROLES)

    # FORCE-RLS admits the owner-owned replay function only while it is called
    # by the erasure login. The function has no UPDATE/DELETE statement and
    # both source branches require a complete ledger tuple.
    op.execute(
        f"""
        CREATE POLICY subject_retrieval_blocks_e2_replay_select
          ON {BLOCK_TABLE} FOR SELECT TO home_agent_owner
          USING (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
          );
        CREATE POLICY subject_retrieval_blocks_e2_replay_insert
          ON {BLOCK_TABLE} FOR INSERT TO home_agent_owner
          WITH CHECK (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
            AND source_kind IN ('erasure_request','ledger_replay')
            AND block_code = 'identity_person_erasure'
            AND outcome_code = 'retrieval_block_active'
            AND ledger_epoch IS NOT NULL
            AND ledger_outbox_id IS NOT NULL
            AND ledger_record_hash IS NOT NULL
            AND ledger_record_digest IS NOT NULL
            AND ledger_attached_at IS NOT NULL
          );
        CREATE POLICY identity_person_erasure_residuals_e2_replay_select
          ON {RESIDUAL_TABLE} FOR SELECT TO home_agent_owner
          USING (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
          );
        CREATE POLICY identity_person_erasure_residuals_e2_replay_insert
          ON {RESIDUAL_TABLE} FOR INSERT TO home_agent_owner
          WITH CHECK (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
            AND state = 'open'
            AND resolved_at IS NULL
            AND EXISTS (
              SELECT 1 FROM privacy.subject_retrieval_blocks AS block
               WHERE block.block_id =
                     identity_person_erasure_residuals.block_id
                 AND block.person_id =
                     identity_person_erasure_residuals.person_id
                 AND block.ledger_epoch IS NOT NULL
            )
          );
        CREATE POLICY person_erasure_scopes_e2_replay_select
          ON privacy.person_erasure_scopes FOR SELECT TO home_agent_owner
          USING (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
          );
        CREATE POLICY identity_erasure_operations_e2_replay_select
          ON operations.reviewed_identity_migration_erasure_operations
          FOR SELECT TO home_agent_owner
          USING (
            current_user = 'home_agent_owner'
            AND session_user = 'home_agent_erasure'
          );

        REVOKE ALL PRIVILEGES ON TABLE {RESIDUAL_TABLE}
          FROM PUBLIC, {all_roles};
        GRANT SELECT, INSERT ON TABLE {RESIDUAL_TABLE} TO home_agent_owner;
        REVOKE ALL PRIVILEGES ON TABLE {BLOCK_TABLE}
          FROM PUBLIC, {all_roles};
        GRANT SELECT, INSERT ON TABLE {BLOCK_TABLE} TO home_agent_owner;
        GRANT SELECT (person_id) ON TABLE {BLOCK_TABLE} TO {KERNEL_ROLE};

        REVOKE ALL ON FUNCTION {REPLAY_FUNCTION}
          FROM PUBLIC, {all_roles};
        GRANT EXECUTE ON FUNCTION {REPLAY_FUNCTION} TO home_agent_erasure;
        """
    )
    for function in SUPPRESSION_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC, {all_roles}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {suppression_roles}")
        # The SECURITY DEFINER anti-resurrection trigger runs as this NOLOGIN
        # kernel and dispatches through each helper. PostgreSQL ownership keeps
        # grant options, not ordinary EXECUTE after an explicit self-revoke.
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {KERNEL_ROLE}")
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC, {all_roles}")


def _assert_installed_contract() -> None:
    suppression_roles = ",".join(f"'{role}'" for role in RUNTIME_SUPPRESSION_ROLES)
    suppression_functions = ",".join(
        f"'{function}'::regprocedure" for function in SUPPRESSION_FUNCTIONS
    )
    managed_roles = ",".join(f"'{role}'" for role in ALL_MANAGED_ROLES)
    op.execute(
        f"""
        DO $e2_installed$
        DECLARE
          kernel_oid oid;
          owner_oid oid;
        BEGIN
          SELECT oid INTO STRICT kernel_oid FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
           WHERE rolname = 'home_agent_owner';
          IF NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', '{BLOCK_TABLE}', 'person_id', 'SELECT'
             ) OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', 'identity.principals',
               'principal_id', 'SELECT'
             ) OR NOT pg_catalog.has_column_privilege(
               '{KERNEL_ROLE}', 'identity.principals',
               'person_id', 'SELECT'
             ) OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{BLOCK_TABLE}',
               'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
             ) OR pg_catalog.has_any_column_privilege(
               '{KERNEL_ROLE}', '{RESIDUAL_TABLE}',
               'SELECT,INSERT,UPDATE,REFERENCES'
             ) OR pg_catalog.has_table_privilege(
               '{KERNEL_ROLE}', '{RESIDUAL_TABLE}',
               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
             ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_kernel_acl_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM (VALUES
                ('privacy.identity_person_is_blocked(uuid)'::regprocedure,
                 true, kernel_oid, ARRAY['search_path=pg_catalog']::text[]),
                ('privacy.identity_principal_is_blocked(uuid)'::regprocedure,
                 true, kernel_oid, ARRAY['search_path=pg_catalog']::text[]),
                ('privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)'
                   ::regprocedure,
                 true, kernel_oid, ARRAY['search_path=pg_catalog']::text[]),
                ('privacy.reject_tombstoned_identity_write()'::regprocedure,
                 true, kernel_oid, ARRAY['search_path=pg_catalog']::text[]),
                ('privacy.enforce_identity_person_erasure_residual_set()'
                   ::regprocedure,
                 false, kernel_oid, ARRAY['search_path=pg_catalog']::text[]),
                ('privacy.replay_identity_person_retrieval_block_v2(jsonb)'
                   ::regprocedure,
                 true, owner_oid,
                 ARRAY['search_path=pg_catalog, pg_temp']::text[])
              ) AS expected(function_oid, security_definer, owner, settings)
              JOIN pg_catalog.pg_proc AS installed
                ON installed.oid = expected.function_oid
             WHERE installed.prosecdef IS DISTINCT FROM
                     expected.security_definer
                OR installed.proowner IS DISTINCT FROM expected.owner
                OR installed.proconfig IS DISTINCT FROM expected.settings
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_function_contract_invalid'
              USING ERRCODE = '55000';
          END IF;
          IF NOT pg_catalog.has_function_privilege(
               'home_agent_erasure', '{REPLAY_FUNCTION}', 'EXECUTE'
             ) OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(
                   ARRAY[{suppression_functions}]::regprocedure[]
                 ) AS function_row(function_oid)
                WHERE NOT pg_catalog.has_function_privilege(
                  '{KERNEL_ROLE}', function_row.function_oid, 'EXECUTE'
                )
             ) OR EXISTS (
               SELECT 1 FROM pg_catalog.unnest(
                 ARRAY[{managed_roles}]::text[]
               ) AS role_name(name)
                WHERE name <> 'home_agent_erasure'
                  AND pg_catalog.has_function_privilege(
                    name, '{REPLAY_FUNCTION}', 'EXECUTE'
                  )
             ) OR EXISTS (
               SELECT 1
                 FROM (VALUES {','.join(f"('{role}')" for role in RUNTIME_SUPPRESSION_ROLES)})
                      AS runtime(role_name)
                CROSS JOIN pg_catalog.unnest(
                  ARRAY[{suppression_functions}]::regprocedure[]
                ) AS function_row(function_oid)
                WHERE NOT pg_catalog.has_function_privilege(
                  runtime.role_name, function_oid, 'EXECUTE'
                )
             ) OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(
                   ARRAY[{managed_roles}]::text[]
                 ) AS managed(role_name)
                CROSS JOIN pg_catalog.unnest(
                  ARRAY[{suppression_functions}]::regprocedure[]
                ) AS function_row(function_oid)
                WHERE managed.role_name NOT IN ({suppression_roles})
                  AND managed.role_name <> '{KERNEL_ROLE}'
                  AND pg_catalog.has_function_privilege(
                    managed.role_name, function_oid, 'EXECUTE'
                  )
             ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_function_acl_invalid'
              USING ERRCODE = '42501';
          END IF;

          IF EXISTS (
            SELECT 1
              FROM (VALUES
                ('{BLOCK_TABLE}'::regclass),
                ('{RESIDUAL_TABLE}'::regclass)
              ) AS protected(table_oid)
              JOIN pg_catalog.pg_class AS relation
                ON relation.oid = protected.table_oid
             WHERE relation.relowner <> owner_oid
                OR NOT relation.relrowsecurity
                OR NOT relation.relforcerowsecurity
          ) OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_policy
             WHERE polrelid = 'identity.confirmation_artifacts'::regclass
               AND polname LIKE '%e2_identity_suppression'
          ) THEN
            RAISE EXCEPTION 'identity_erasure_e2_table_contract_invalid'
              USING ERRCODE = '55000';
          END IF;
        END
        $e2_installed$
        """
    )


def _remove_suppression_boundary() -> None:
    op.execute(
        "DROP POLICY IF EXISTS principals_e2_erasure_kernel_lookup "
        "ON identity.principals"
    )
    for table, _, trigger_arguments, predecessor_rls in reversed(SUPPRESSION_TARGETS):
        policy_base = table.replace(".", "_")
        if trigger_arguments:
            op.execute(
                f"DROP TRIGGER IF EXISTS {policy_base}_e2_anti_resurrection "
                f"ON {table}"
            )
        op.execute(
            f"DROP POLICY IF EXISTS {policy_base}_e2_identity_suppression "
            f"ON {table}"
        )
        if not predecessor_rls:
            op.execute(
                f"DROP POLICY IF EXISTS {policy_base}_e2_acl_preservation "
                f"ON {table}"
            )
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def _remove_exact_set_triggers() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "identity_person_erasure_residual_set_from_residual "
        f"ON {RESIDUAL_TABLE}"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "identity_person_erasure_residual_set_from_block "
        f"ON {BLOCK_TABLE}"
    )


def _drop_kernel_functions() -> None:
    op.execute(
        f"""
        DROP POLICY IF EXISTS identity_erasure_operations_e2_replay_select
          ON operations.reviewed_identity_migration_erasure_operations;
        DROP POLICY IF EXISTS person_erasure_scopes_e2_replay_select
          ON privacy.person_erasure_scopes;
        DROP POLICY IF EXISTS
          identity_person_erasure_residuals_e2_replay_insert
          ON {RESIDUAL_TABLE};
        DROP POLICY IF EXISTS
          identity_person_erasure_residuals_e2_replay_select
          ON {RESIDUAL_TABLE};
        DROP POLICY IF EXISTS subject_retrieval_blocks_e2_replay_insert
          ON {BLOCK_TABLE};
        DROP POLICY IF EXISTS subject_retrieval_blocks_e2_replay_select
          ON {BLOCK_TABLE};
        DROP POLICY IF EXISTS subject_retrieval_blocks_e2_kernel_lookup
          ON {BLOCK_TABLE};
        DROP FUNCTION IF EXISTS {REPLAY_FUNCTION};
        SET LOCAL ROLE {KERNEL_ROLE};
        DROP FUNCTION IF EXISTS
          privacy.enforce_identity_person_erasure_residual_set();
        DROP FUNCTION IF EXISTS privacy.reject_tombstoned_identity_write();
        DROP FUNCTION IF EXISTS
          privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid);
        DROP FUNCTION IF EXISTS privacy.identity_principal_is_blocked(uuid);
        DROP FUNCTION IF EXISTS privacy.identity_person_is_blocked(uuid);
        RESET ROLE;
        REVOKE SELECT (person_id) ON TABLE {BLOCK_TABLE} FROM {KERNEL_ROLE};
        REVOKE SELECT (principal_id, person_id)
          ON TABLE identity.principals FROM {KERNEL_ROLE};
        REVOKE USAGE ON SCHEMA identity, privacy FROM {KERNEL_ROLE};
        """
    )


def upgrade() -> None:
    _admit_dormant_e1()
    _evolve_tombstone()
    _install_residual_table()
    _install_kernel_functions()
    _install_exact_set_triggers()
    _install_suppression_boundary()
    _contract_e2_acl()
    _assert_installed_contract()


def downgrade() -> None:
    op.execute(
        f"""
        DO $e2_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM {BLOCK_TABLE})
             OR EXISTS (SELECT 1 FROM {RESIDUAL_TABLE}) THEN
            RAISE EXCEPTION 'identity_erasure_e2_downgrade_blocked'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $e2_downgrade$
        """
    )
    _remove_suppression_boundary()
    _remove_exact_set_triggers()
    _drop_kernel_functions()
    op.drop_table("identity_person_erasure_residuals", schema="privacy")
    op.execute(
        f"""
        ALTER TABLE {BLOCK_TABLE}
          DROP CONSTRAINT ck_subject_retrieval_block_e2_uuidv7,
          DROP CONSTRAINT ck_subject_retrieval_block_ledger_shape,
          DROP CONSTRAINT ck_subject_retrieval_block_outcome,
          DROP CONSTRAINT ck_subject_retrieval_block_exact_source,
          DROP CONSTRAINT identity_erasure_block_ledger_outbox,
          DROP CONSTRAINT identity_erasure_block_ledger_epoch,
          DROP CONSTRAINT identity_erasure_block_identity,
          DROP CONSTRAINT identity_erasure_block_person,
          DROP COLUMN ledger_attached_at,
          DROP COLUMN ledger_record_digest,
          DROP COLUMN ledger_record_hash,
          DROP COLUMN ledger_outbox_id,
          DROP COLUMN ledger_epoch,
          DROP COLUMN outcome_code;
        ALTER TABLE {BLOCK_TABLE}
          ALTER COLUMN operation_id SET NOT NULL,
          ALTER COLUMN erasure_request_id SET NOT NULL,
          ADD CONSTRAINT ck_subject_retrieval_block_exact_source
            CHECK (source_kind = 'erasure_request');
        COMMENT ON TABLE {BLOCK_TABLE} IS
          'Dormant mandatory person-level retrieval-block marker linked to '
          'one exact confirmed manual erasure operation source.';
        """
    )
