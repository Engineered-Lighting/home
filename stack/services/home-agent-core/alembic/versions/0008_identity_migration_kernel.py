"""Add the manifest-only reviewed identity-migration kernel.

Revision ID: 0008_identity_migration_kernel
Revises: 0007_phase3_identity_authority
Create Date: 2026-07-12

This revision deliberately does not install a semantic finalizer.  Revision
0007 stores keyed-HMAC commitments and Ed25519-shaped attestations, but the
database has neither the commitment key nor a trusted public-key registry and
canonical verifier.  Accepting private projection JSON without that verifier
would let a caller detach semantic content from the reviewed manifest.

The two callable functions below therefore expose only content-free
capabilities and atomic registration of the already-reviewed, content-free
run/source/decision manifest. A third function is non-callable and exists only
as an erasure-impact trigger that creates the MVCC replay fence. They never
write People, privacy, relationship, receipt, finalization, cutover, erasure,
outbox, or knowledge rows. A later revision must add the external payload
verifier before semantic finalization can exist.

The runtime revision pin remains 0006.  This migration also requires two roles
that are provisioned outside Core: a NOLOGIN/non-superuser/non-BYPASSRLS
``home_agent_identity_kernel`` function owner and an exact LOGIN
``home_agent_identity_migration`` session role.  Upgrade fails if either
boundary is absent or unsafe.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0008_identity_migration_kernel"
down_revision: str | None = "0007_phase3_identity_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KERNEL_ROLE = "home_agent_identity_kernel"
CALLER_ROLE = "home_agent_identity_migration"
ADVISORY_LOCK_ID = 7210202608
PER_RUN_ADVISORY_NAMESPACE = 72102026

MANIFEST_TABLES = (
    "operations.reviewed_identity_migration_runs",
    "operations.reviewed_identity_migration_source_items",
    "operations.reviewed_identity_migration_decisions",
)

PHASE3_EVIDENCE_TABLES = (
    *MANIFEST_TABLES,
    "operations.reviewed_identity_migration_item_receipts",
    "operations.reviewed_identity_migration_finalizations",
    "operations.legacy_identity_writer_evidence",
    "operations.privacy_cutover_check_receipts",
    "operations.semantic_authority_cutovers",
    "operations.reviewed_identity_migration_erasure_impacts",
)

APPLICATION_ROLES = (
    "home_agent_owner",
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    KERNEL_ROLE,
    CALLER_ROLE,
)

RUNTIME_TABLE_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    KERNEL_ROLE,
    CALLER_ROLE,
)

FUNCTION_SIGNATURES = (
    "operations.reviewed_identity_migration_capabilities()",
    "operations.register_reviewed_identity_migration(jsonb)",
)

REPLAY_GUARD_TRIGGER_SIGNATURE = (
    "operations.bump_reviewed_identity_migration_replay_guard()"
)


def _require_roles() -> None:
    op.execute(
        f"""
        DO $roles$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS kernel_role
             WHERE kernel_role.rolname = '{KERNEL_ROLE}'
               AND kernel_role.rolcanlogin = false
               AND kernel_role.rolinherit = false
               AND kernel_role.rolsuper = false
               AND kernel_role.rolcreatedb = false
               AND kernel_role.rolcreaterole = false
               AND kernel_role.rolreplication = false
               AND kernel_role.rolbypassrls = false
               AND kernel_role.rolconnlimit = 0
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS kernel_membership
                  WHERE kernel_membership.member = kernel_role.oid
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_shdepend AS kernel_ownership
                  WHERE kernel_ownership.refobjid = kernel_role.oid
                    AND kernel_ownership.deptype = 'o'
               )
               AND (
                 SELECT pg_catalog.count(*)
                   FROM pg_catalog.pg_auth_members AS kernel_member
                  WHERE kernel_member.roleid = kernel_role.oid
               ) = 1
               AND EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS owner_membership
                   JOIN pg_catalog.pg_roles AS owner_role
                     ON owner_role.oid = owner_membership.member
                  WHERE owner_membership.roleid = kernel_role.oid
                    AND owner_role.rolname = 'home_agent_owner'
                    AND owner_membership.admin_option = false
                    AND owner_membership.inherit_option = false
                    AND owner_membership.set_option = true
               )
          ) THEN
            RAISE EXCEPTION 'identity_migration_kernel_role_invalid'
              USING ERRCODE = '42704';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS caller_role
             WHERE caller_role.rolname = '{CALLER_ROLE}'
               AND caller_role.rolcanlogin = true
               AND caller_role.rolinherit = false
               AND caller_role.rolsuper = false
               AND caller_role.rolcreatedb = false
               AND caller_role.rolcreaterole = false
               AND caller_role.rolreplication = false
               AND caller_role.rolbypassrls = false
               AND caller_role.rolconnlimit = 1
               AND caller_role.rolvaliduntil IS NOT NULL
               AND caller_role.rolvaliduntil <=
                   pg_catalog.transaction_timestamp()
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_auth_members AS caller_membership
                  WHERE caller_membership.member = caller_role.oid
               )
               AND NOT EXISTS (
                 SELECT 1
                   FROM pg_catalog.pg_shdepend AS caller_ownership
                  WHERE caller_ownership.refobjid = caller_role.oid
                    AND caller_ownership.deptype = 'o'
               )
          ) THEN
            RAISE EXCEPTION 'identity_migration_caller_role_invalid'
              USING ERRCODE = '42704';
          END IF;
        END
        $roles$
        """
    )


def _install_manifest_rls() -> None:
    expression = (
        "session_user = 'home_agent_identity_migration' "
        "AND current_user = 'home_agent_identity_kernel' "
        "AND NOT pg_catalog.pg_has_role("
        "session_user, 'home_agent_identity_kernel', 'SET')"
    )
    for table in PHASE3_EVIDENCE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table in MANIFEST_TABLES:
        name = table.rsplit(".", 1)[1]
        select_policy = f"{name}_manifest_kernel_select"
        insert_policy = f"{name}_manifest_kernel_insert"
        op.execute(f"DROP POLICY IF EXISTS {select_policy} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {insert_policy} ON {table}")
        op.execute(
            f"CREATE POLICY {select_policy} ON {table} FOR SELECT "
            f"TO {KERNEL_ROLE} USING ({expression})"
        )
        op.execute(
            f"CREATE POLICY {insert_policy} ON {table} FOR INSERT "
            f"TO {KERNEL_ROLE} WITH CHECK ({expression})"
        )
    erasure_table = "operations.reviewed_identity_migration_erasure_impacts"
    erasure_policy = "identity_migration_erasure_impacts_manifest_kernel_select"
    op.execute(f"DROP POLICY IF EXISTS {erasure_policy} ON {erasure_table}")
    op.execute(
        f"CREATE POLICY {erasure_policy} ON {erasure_table} FOR SELECT "
        f"TO {KERNEL_ROLE} USING ({expression})"
    )
    update_policy = "reviewed_identity_migration_runs_replay_guard_update"
    trigger_select_policy = (
        "reviewed_identity_migration_runs_replay_guard_trigger_select"
    )
    update_expression = (
        "current_user = 'home_agent_identity_kernel' AND ("
        "(session_user = 'home_agent_identity_migration' AND NOT "
        "pg_catalog.pg_has_role(session_user, "
        "'home_agent_identity_kernel', 'SET')) OR "
        "(session_user IN ('home_agent_owner','home_agent_erasure') AND "
        "pg_catalog.pg_trigger_depth() = 1))"
    )
    op.execute(
        "DROP POLICY IF EXISTS "
        f"{update_policy} ON operations.reviewed_identity_migration_runs"
    )
    op.execute(
        "DROP POLICY IF EXISTS "
        f"{trigger_select_policy} ON "
        "operations.reviewed_identity_migration_runs"
    )
    op.execute(
        f"CREATE POLICY {trigger_select_policy} ON "
        "operations.reviewed_identity_migration_runs FOR SELECT "
        f"TO {KERNEL_ROLE} USING ("
        "current_user = 'home_agent_identity_kernel' AND "
        "session_user IN ('home_agent_owner','home_agent_erasure') AND "
        "pg_catalog.pg_trigger_depth() = 1)"
    )
    op.execute(
        f"CREATE POLICY {update_policy} ON "
        "operations.reviewed_identity_migration_runs FOR UPDATE "
        f"TO {KERNEL_ROLE} USING ({update_expression}) "
        f"WITH CHECK ({update_expression})"
    )


def _install_privileges() -> None:
    runtime_roles = ", ".join(RUNTIME_TABLE_ROLES)
    for table in PHASE3_EVIDENCE_TABLES:
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {table} " f"FROM PUBLIC, {runtime_roles}"
        )
    for schema_name in (
        "ingest",
        "identity",
        "knowledge",
        "engagement",
        "privacy",
        "operations",
        "media",
    ):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {schema_name} "
            f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
        )
        op.execute(
            f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {schema_name} "
            f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
        )
    op.execute(
        "REVOKE UPDATE (expires_at) ON "
        "operations.reviewed_identity_migration_runs "
        f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
    )
    op.execute(
        "REVOKE SELECT (authorization_id, from_mode, to_mode, rule_version, "
        "policy_version, policy_digest, authorized_at) "
        "ON operations.rollout_authorizations "
        f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
    )
    # PostgreSQL requires the new function owner to hold CREATE on the
    # containing schema at ALTER OWNER time.  It is granted only for that
    # ownership transition and revoked immediately after both functions are
    # secured; the kernel retains USAGE only.
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA operations TO {KERNEL_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA operations TO {CALLER_ROLE}")
    op.execute(
        "GRANT SELECT, INSERT ON TABLE "
        "operations.reviewed_identity_migration_runs, "
        "operations.reviewed_identity_migration_source_items, "
        "operations.reviewed_identity_migration_decisions "
        f"TO {KERNEL_ROLE}"
    )
    op.execute(
        "GRANT SELECT (authorization_id, from_mode, to_mode, rule_version, "
        "policy_version, policy_digest, authorized_at) "
        "ON operations.rollout_authorizations "
        f"TO {KERNEL_ROLE}"
    )
    for table in PHASE3_EVIDENCE_TABLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {CALLER_ROLE}")
    for table in PHASE3_EVIDENCE_TABLES[3:]:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table} FROM {KERNEL_ROLE}")
    op.execute(
        "GRANT SELECT ON TABLE "
        "operations.reviewed_identity_migration_erasure_impacts "
        f"TO {KERNEL_ROLE}"
    )
    op.execute(
        "GRANT UPDATE (expires_at) ON TABLE "
        "operations.reviewed_identity_migration_runs "
        f"TO {KERNEL_ROLE}"
    )


def _assert_shadow_index() -> None:
    op.execute(
        """
        DO $index_contract$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_class AS named_object
              JOIN pg_catalog.pg_namespace AS named_namespace
                ON named_namespace.oid = named_object.relnamespace
             WHERE named_namespace.nspname = 'operations'
               AND named_object.relname =
                   'uq_identity_migration_shadow_authorization_once'
          ) THEN
            IF NOT EXISTS (
              SELECT 1
                FROM pg_catalog.pg_class AS index_class
                JOIN pg_catalog.pg_namespace AS index_namespace
                  ON index_namespace.oid = index_class.relnamespace
                JOIN pg_catalog.pg_index AS index_contract
                  ON index_contract.indexrelid = index_class.oid
                JOIN pg_catalog.pg_class AS table_class
                  ON table_class.oid = index_contract.indrelid
                JOIN pg_catalog.pg_namespace AS table_namespace
                  ON table_namespace.oid = table_class.relnamespace
                JOIN pg_catalog.pg_am AS access_method
                  ON access_method.oid = index_class.relam
               WHERE index_namespace.nspname = 'operations'
                 AND index_class.relname =
                     'uq_identity_migration_shadow_authorization_once'
                 AND index_class.relkind = 'i'
                 AND table_namespace.nspname = 'operations'
                 AND table_class.relname =
                     'reviewed_identity_migration_runs'
                 AND index_contract.indisunique = true
                 AND index_contract.indisvalid = true
                 AND index_contract.indisready = true
                 AND index_contract.indislive = true
                 AND index_contract.indnullsnotdistinct = false
                 AND index_contract.indnkeyatts = 1
                 AND index_contract.indnatts = 1
                 AND index_contract.indpred IS NULL
                 AND index_contract.indexprs IS NULL
                 AND access_method.amname = 'btree'
                 AND pg_catalog.pg_get_indexdef(
                       index_class.oid, 1, true
                     ) = 'shadow_authorization_id'
                 AND pg_catalog.pg_get_userbyid(index_class.relowner) =
                     'home_agent_owner'
                 AND NOT EXISTS (
                   SELECT 1
                     FROM pg_catalog.pg_constraint AS owning_constraint
                    WHERE owning_constraint.conindid = index_class.oid
                 )
            ) THEN
              RAISE EXCEPTION 'identity_migration_shadow_index_invalid'
                USING ERRCODE = '55000';
            END IF;
          ELSE
            RAISE EXCEPTION 'identity_migration_shadow_index_missing'
              USING ERRCODE = '55000';
          END IF;
        END
        $index_contract$
        """
    )


def _install_shadow_uniqueness() -> None:
    op.execute(
        """
        DO $index_name$
        BEGIN
          IF pg_catalog.to_regclass(
               'operations.uq_identity_migration_shadow_authorization_once'
             ) IS NOT NULL THEN
            RAISE EXCEPTION 'identity_migration_shadow_index_name_occupied'
              USING ERRCODE = '55000';
          END IF;
        END
        $index_name$
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX "
        "uq_identity_migration_shadow_authorization_once ON "
        "operations.reviewed_identity_migration_runs "
        "USING btree (shadow_authorization_id)"
    )
    _assert_shadow_index()


def _install_capabilities_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION
        operations.reviewed_identity_migration_capabilities()
        RETURNS jsonb
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        DECLARE
          operation_time timestamptz;
        BEGIN
          IF session_user <> '{CALLER_ROLE}'
             OR current_user <> '{KERNEL_ROLE}' THEN
            RAISE EXCEPTION 'identity_migration_role_required'
              USING ERRCODE = '42501';
          END IF;
          operation_time := pg_catalog.clock_timestamp();
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS active_caller
             WHERE active_caller.rolname = '{CALLER_ROLE}'
               AND active_caller.rolvaliduntil > operation_time
               AND active_caller.rolvaliduntil <=
                   operation_time + interval '15 minutes'
               AND NOT pg_catalog.pg_has_role(
                     active_caller.rolname,
                     '{KERNEL_ROLE}',
                     'SET'
                   )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF pg_catalog.current_setting('transaction_isolation')
             IS DISTINCT FROM 'serializable' THEN
            RAISE EXCEPTION 'identity_migration_serializable_required'
              USING ERRCODE = '25001';
          END IF;
          IF operation_time - pg_catalog.transaction_timestamp() >
             interval '5 seconds' THEN
            RAISE EXCEPTION 'identity_migration_stale_transaction'
              USING ERRCODE = '25001';
          END IF;
          PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
          PERFORM pg_catalog.pg_advisory_xact_lock({ADVISORY_LOCK_ID});
          operation_time := pg_catalog.clock_timestamp();
          IF operation_time - pg_catalog.transaction_timestamp() >
             interval '5 seconds' THEN
            RAISE EXCEPTION 'identity_migration_stale_transaction'
              USING ERRCODE = '25001';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_roles AS active_caller
                WHERE active_caller.rolname = '{CALLER_ROLE}'
                  AND active_caller.rolvaliduntil > operation_time
                  AND active_caller.rolvaliduntil <=
                      operation_time + interval '15 minutes'
                  AND NOT pg_catalog.pg_has_role(
                        active_caller.rolname,
                        '{KERNEL_ROLE}',
                        'SET'
                      )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;
          RETURN pg_catalog.jsonb_build_object(
            'contract_version', 'reviewed-identity-migration-kernel-v1',
            'manifest_registration_enabled', true,
            'finalization_enabled', false,
            'finalization_block_reason',
              'external_payload_verifier_not_implemented',
            'direct_table_access', false,
            'required_isolation', 'serializable',
            'manifest_canonical_bytes_max', 4194304,
            'run_header_canonical_bytes_max', 32768,
            'source_items_max', 10000,
            'decisions_max', 10000,
            'activation_window_seconds', 900
          );
        END;
        $function$
        """
    )


def _install_registration_function() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION
        operations.register_reviewed_identity_migration(manifest jsonb)
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        DECLARE
          run_document jsonb;
          source_documents jsonb;
          decision_documents jsonb;
          source_document jsonb;
          decision_document jsonb;
          target_run operations.reviewed_identity_migration_runs%ROWTYPE;
          existing_run operations.reviewed_identity_migration_runs%ROWTYPE;
          source_rows bigint;
          decision_rows bigint;
          minimum_ordinal integer;
          maximum_ordinal integer;
          distinct_ordinals bigint;
          existing_rows bigint;
          operation_time timestamptz;
          erasure_affected boolean;
          existing_run_found boolean;
        BEGIN
          IF session_user <> '{CALLER_ROLE}'
             OR current_user <> '{KERNEL_ROLE}' THEN
            RAISE EXCEPTION 'identity_migration_role_required'
              USING ERRCODE = '42501';
          END IF;
          operation_time := pg_catalog.clock_timestamp();
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS active_caller
             WHERE active_caller.rolname = '{CALLER_ROLE}'
               AND active_caller.rolvaliduntil > operation_time
               AND active_caller.rolvaliduntil <=
                   operation_time + interval '15 minutes'
               AND NOT pg_catalog.pg_has_role(
                     active_caller.rolname,
                     '{KERNEL_ROLE}',
                     'SET'
                   )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF pg_catalog.current_setting('transaction_isolation')
             IS DISTINCT FROM 'serializable' THEN
            RAISE EXCEPTION 'identity_migration_serializable_required'
              USING ERRCODE = '25001';
          END IF;
          IF operation_time - pg_catalog.transaction_timestamp() >
             interval '5 seconds' THEN
            RAISE EXCEPTION 'identity_migration_stale_transaction'
              USING ERRCODE = '25001';
          END IF;
          PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
          PERFORM pg_catalog.pg_advisory_xact_lock({ADVISORY_LOCK_ID});
          operation_time := pg_catalog.clock_timestamp();
          IF operation_time - pg_catalog.transaction_timestamp() >
             interval '5 seconds' THEN
            RAISE EXCEPTION 'identity_migration_stale_transaction'
              USING ERRCODE = '25001';
          END IF;
          IF NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_roles AS active_caller
                WHERE active_caller.rolname = '{CALLER_ROLE}'
                  AND active_caller.rolvaliduntil > operation_time
                  AND active_caller.rolvaliduntil <=
                      operation_time + interval '15 minutes'
                  AND NOT pg_catalog.pg_has_role(
                        active_caller.rolname,
                        '{KERNEL_ROLE}',
                        'SET'
                      )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;

          -- This is the first payload operation.  It caps the canonical JSONB
          -- text representation at 4 MiB before any key or array traversal.
          IF manifest IS NULL
             OR pg_catalog.octet_length(manifest::text) > 4194304 THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;
          IF pg_catalog.jsonb_typeof(manifest) IS DISTINCT FROM 'object'
             OR NOT manifest ?& ARRAY['run','source_items','decisions']
             OR manifest - ARRAY['run','source_items','decisions']
                <> '{{}}'::jsonb THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;
          run_document := manifest->'run';
          source_documents := manifest->'source_items';
          decision_documents := manifest->'decisions';
          IF pg_catalog.jsonb_typeof(run_document) IS DISTINCT FROM 'object'
             OR pg_catalog.octet_length(run_document::text) > 32768
             OR pg_catalog.jsonb_typeof(source_documents)
                IS DISTINCT FROM 'array'
             OR pg_catalog.jsonb_typeof(decision_documents)
                IS DISTINCT FROM 'array'
             OR pg_catalog.jsonb_array_length(source_documents)
                NOT BETWEEN 1 AND 10000
             OR pg_catalog.jsonb_array_length(decision_documents)
                NOT BETWEEN 1 AND 10000 THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF NOT run_document ?& ARRAY[
               'run_id','operator_request_id','contract_version',
               'source_schema_version','source_projection_contract_version',
               'importer_version','canonicalization_version',
               'projection_version','shadow_rule_version',
               'commitment_algorithm','commitment_key_fingerprint',
               'commitment_key_epoch','source_item_count','decision_count',
               'logical_source_manifest_commitment',
               'projection_manifest_commitment',
               'source_projection_contract_digest',
               'review_receipt_commitment','policy_version','policy_digest',
               'shadow_authorization_id','release_manifest_digest',
               'migration_tool_bundle_digest','core_oci_manifest_digest',
               'core_schema_digest','core_capability_digest',
               'signature_algorithm','signing_key_fingerprint',
               'review_signature','expires_at'
             ]
             OR run_document - ARRAY[
               'run_id','operator_request_id','contract_version',
               'source_schema_version','source_projection_contract_version',
               'importer_version','canonicalization_version',
               'projection_version','shadow_rule_version',
               'commitment_algorithm','commitment_key_fingerprint',
               'commitment_key_epoch','source_item_count','decision_count',
               'logical_source_manifest_commitment',
               'projection_manifest_commitment',
               'source_projection_contract_digest',
               'review_receipt_commitment','policy_version','policy_digest',
               'shadow_authorization_id','release_manifest_digest',
               'migration_tool_bundle_digest','core_oci_manifest_digest',
               'core_schema_digest','core_capability_digest',
               'signature_algorithm','signing_key_fingerprint',
               'review_signature','expires_at'
             ] <> '{{}}'::jsonb
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(ARRAY[
                   'run_id','operator_request_id','contract_version',
                   'source_projection_contract_version','importer_version',
                   'canonicalization_version','projection_version',
                   'shadow_rule_version','commitment_algorithm',
                   'commitment_key_fingerprint',
                   'logical_source_manifest_commitment',
                   'projection_manifest_commitment',
                   'source_projection_contract_digest',
                   'review_receipt_commitment','policy_version',
                   'policy_digest','shadow_authorization_id',
                   'release_manifest_digest','migration_tool_bundle_digest',
                   'core_oci_manifest_digest','core_schema_digest',
                   'core_capability_digest','signature_algorithm',
                   'signing_key_fingerprint','review_signature','expires_at'
                 ]) AS required_string(key_name)
                WHERE pg_catalog.jsonb_typeof(
                        run_document->required_string.key_name
                      ) IS DISTINCT FROM 'string'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(ARRAY[
                   'source_schema_version','commitment_key_epoch',
                   'source_item_count','decision_count'
                 ]) AS required_number(key_name)
                WHERE pg_catalog.jsonb_typeof(
                        run_document->required_number.key_name
                      ) IS DISTINCT FROM 'number'
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF run_document->>'run_id' !~
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
             OR run_document->>'operator_request_id' !~
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
             OR run_document->>'shadow_authorization_id' !~
               '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[47][0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
             OR run_document->>'source_schema_version' <> '1'
             OR run_document->>'commitment_key_epoch' !~ '^[1-9][0-9]*$'
             OR run_document->>'source_item_count' !~ '^[1-9][0-9]{{0,3}}$|^10000$'
             OR run_document->>'decision_count' !~ '^[1-9][0-9]{{0,3}}$|^10000$'
             OR run_document->>'contract_version'
                <> 'reviewed-identity-migration-run-v1'
             OR run_document->>'source_projection_contract_version'
                <> 'legacy-identity-source-projection-v1'
             OR run_document->>'importer_version'
                <> 'legacy-identity-importer-v1'
             OR run_document->>'canonicalization_version'
                <> 'identity-canonicalization-v1'
             OR run_document->>'projection_version'
                <> 'semantic-people-projection-v1'
             OR run_document->>'shadow_rule_version'
                <> 'record-only-envelope-worker-gate-v3'
             OR run_document->>'commitment_algorithm' <> 'hmac-sha256-v1'
             OR run_document->>'signature_algorithm' <> 'ed25519'
             OR run_document->>'policy_version'
                !~ '^[a-z0-9][a-z0-9._-]{{0,127}}$'
             OR run_document->>'expires_at' !~
                '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}\\.[0-9]{{6}}Z$'
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.unnest(ARRAY[
                   'commitment_key_fingerprint',
                   'logical_source_manifest_commitment',
                   'projection_manifest_commitment',
                   'source_projection_contract_digest',
                   'review_receipt_commitment','policy_digest',
                   'release_manifest_digest','migration_tool_bundle_digest',
                   'core_oci_manifest_digest','core_schema_digest',
                   'core_capability_digest','signing_key_fingerprint'
                 ]) AS digest_field(key_name)
                WHERE run_document->>digest_field.key_name
                      !~ '^[0-9a-f]{{64}}$'
             )
             OR run_document->>'review_signature' !~ '^[0-9a-f]{{128}}$' THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          BEGIN
            target_run.run_id := (run_document->>'run_id')::uuid;
            target_run.operator_request_id :=
              (run_document->>'operator_request_id')::uuid;
            target_run.contract_version := run_document->>'contract_version';
            target_run.source_schema_version :=
              (run_document->>'source_schema_version')::integer;
            target_run.source_projection_contract_version :=
              run_document->>'source_projection_contract_version';
            target_run.importer_version := run_document->>'importer_version';
            target_run.canonicalization_version :=
              run_document->>'canonicalization_version';
            target_run.projection_version := run_document->>'projection_version';
            target_run.shadow_rule_version :=
              run_document->>'shadow_rule_version';
            target_run.commitment_algorithm :=
              run_document->>'commitment_algorithm';
            target_run.commitment_key_fingerprint :=
              run_document->>'commitment_key_fingerprint';
            target_run.commitment_key_epoch :=
              (run_document->>'commitment_key_epoch')::bigint;
            target_run.source_item_count :=
              (run_document->>'source_item_count')::integer;
            target_run.decision_count :=
              (run_document->>'decision_count')::integer;
            target_run.logical_source_manifest_commitment :=
              run_document->>'logical_source_manifest_commitment';
            target_run.projection_manifest_commitment :=
              run_document->>'projection_manifest_commitment';
            target_run.source_projection_contract_digest :=
              run_document->>'source_projection_contract_digest';
            target_run.review_receipt_commitment :=
              run_document->>'review_receipt_commitment';
            target_run.policy_version := run_document->>'policy_version';
            target_run.policy_digest := run_document->>'policy_digest';
            target_run.shadow_authorization_id :=
              (run_document->>'shadow_authorization_id')::uuid;
            target_run.release_manifest_digest :=
              run_document->>'release_manifest_digest';
            target_run.migration_tool_bundle_digest :=
              run_document->>'migration_tool_bundle_digest';
            target_run.core_oci_manifest_digest :=
              run_document->>'core_oci_manifest_digest';
            target_run.core_schema_digest :=
              run_document->>'core_schema_digest';
            target_run.core_capability_digest :=
              run_document->>'core_capability_digest';
            target_run.signature_algorithm :=
              run_document->>'signature_algorithm';
            target_run.signing_key_fingerprint :=
              run_document->>'signing_key_fingerprint';
            target_run.review_signature := run_document->>'review_signature';
            target_run.expires_at :=
              (run_document->>'expires_at')::timestamptz;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END;
          IF pg_catalog.to_char(
               target_run.expires_at AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
             ) <> run_document->>'expires_at' THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;
          -- Future erasure kernels must take this exact namespace/key lock
          -- before testing or appending a run-level erasure impact.  hashtext
          -- collisions only serialize unrelated runs and cannot weaken safety.
          PERFORM pg_catalog.pg_advisory_xact_lock(
            {PER_RUN_ADVISORY_NAMESPACE},
            pg_catalog.hashtext(target_run.run_id::text)
          );
          operation_time := pg_catalog.clock_timestamp();
          IF operation_time - pg_catalog.transaction_timestamp() >
             interval '5 seconds' THEN
            RAISE EXCEPTION 'identity_migration_stale_transaction'
              USING ERRCODE = '25001';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS active_caller
             WHERE active_caller.rolname = '{CALLER_ROLE}'
               AND active_caller.rolvaliduntil > operation_time
               AND active_caller.rolvaliduntil <=
                   operation_time + interval '15 minutes'
               AND NOT pg_catalog.pg_has_role(
                     active_caller.rolname,
                     '{KERNEL_ROLE}',
                     'SET'
                   )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;
          -- This is an intentional MVCC write, not a data change.  Every
          -- erasure-impact insert performs the same write in its unavoidable
          -- trigger.  A SERIALIZABLE waiter on a committed erasure therefore
          -- fails with 40001 instead of observing a pre-erasure snapshot.
          UPDATE operations.reviewed_identity_migration_runs AS replay_guard
             SET expires_at = replay_guard.expires_at
           WHERE replay_guard.run_id = target_run.run_id
          RETURNING replay_guard.* INTO existing_run;
          existing_run_found := FOUND;
          SELECT EXISTS (
            SELECT 1
              FROM operations.reviewed_identity_migration_erasure_impacts
             WHERE run_id = target_run.run_id
          ) INTO erasure_affected;

          IF target_run.source_item_count <>
               pg_catalog.jsonb_array_length(source_documents)
             OR target_run.decision_count <>
                pg_catalog.jsonb_array_length(decision_documents) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          FOR source_document IN
            SELECT value
              FROM pg_catalog.jsonb_array_elements(source_documents)
                   AS source_element(value)
          LOOP
            IF pg_catalog.jsonb_typeof(source_document)
                 IS DISTINCT FROM 'object'
               OR NOT source_document ?& ARRAY[
                    'source_item_id','ordinal','source_table_kind',
                    'row_key_commitment','allowed_projection_commitment'
                  ]
               OR source_document - ARRAY[
                    'source_item_id','ordinal','source_table_kind',
                    'row_key_commitment','allowed_projection_commitment'
                  ] <> '{{}}'::jsonb
               OR pg_catalog.jsonb_typeof(source_document->'source_item_id')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(source_document->'ordinal')
                  IS DISTINCT FROM 'number'
               OR pg_catalog.jsonb_typeof(source_document->'source_table_kind')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(source_document->'row_key_commitment')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(
                    source_document->'allowed_projection_commitment'
                  ) IS DISTINCT FROM 'string'
               OR source_document->>'source_item_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
               OR source_document->>'ordinal' !~ '^(0|[1-9][0-9]{{0,3}})$'
               OR source_document->>'source_table_kind' NOT IN (
                    'schema_meta','identities','identity_aliases',
                    'enrollments','relationships'
                  )
               OR source_document->>'row_key_commitment'
                  !~ '^[0-9a-f]{{64}}$'
               OR source_document->>'allowed_projection_commitment'
                  !~ '^[0-9a-f]{{64}}$' THEN
              RAISE EXCEPTION 'identity_migration_manifest_invalid'
                USING ERRCODE = '22023';
            END IF;
          END LOOP;

          FOR decision_document IN
            SELECT value
              FROM pg_catalog.jsonb_array_elements(decision_documents)
                   AS decision_element(value)
          LOOP
            IF pg_catalog.jsonb_typeof(decision_document)
                 IS DISTINCT FROM 'object'
               OR NOT decision_document ?& ARRAY[
                    'decision_id','source_item_id','ordinal','decision_kind',
                    'disposition','candidate_commitment',
                    'canonical_apply_decision_id',
                    'canonical_apply_disposition','decision_commitment'
                  ]
               OR decision_document - ARRAY[
                    'decision_id','source_item_id','ordinal','decision_kind',
                    'disposition','candidate_commitment',
                    'canonical_apply_decision_id',
                    'canonical_apply_disposition','decision_commitment'
                  ] <> '{{}}'::jsonb
               OR pg_catalog.jsonb_typeof(decision_document->'decision_id')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(decision_document->'source_item_id')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(decision_document->'ordinal')
                  IS DISTINCT FROM 'number'
               OR pg_catalog.jsonb_typeof(decision_document->'decision_kind')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(decision_document->'disposition')
                  IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(
                    decision_document->'decision_commitment'
                  ) IS DISTINCT FROM 'string'
               OR pg_catalog.jsonb_typeof(
                    decision_document->'candidate_commitment'
                  ) NOT IN ('string','null')
               OR pg_catalog.jsonb_typeof(
                    decision_document->'canonical_apply_decision_id'
                  ) NOT IN ('string','null')
               OR pg_catalog.jsonb_typeof(
                    decision_document->'canonical_apply_disposition'
                  ) NOT IN ('string','null')
               OR decision_document->>'decision_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
               OR decision_document->>'source_item_id' !~
                  '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
               OR decision_document->>'ordinal' !~ '^(0|[1-9][0-9]{{0,3}})$'
               OR decision_document->>'decision_kind' NOT IN (
                    'person','privacy_directive','person_status','alias',
                    'recognition_binding','legacy_role_candidate',
                    'legacy_relationship_candidate','explicit_omission'
                  )
               OR decision_document->>'disposition' NOT IN (
                    'apply','privacy_suppressed','out_of_scope_by_rule',
                    'coalesced_duplicate'
                  )
               OR decision_document->>'decision_commitment'
                  !~ '^[0-9a-f]{{64}}$'
               OR (
                    decision_document->>'candidate_commitment' IS NOT NULL
                    AND decision_document->>'candidate_commitment'
                        !~ '^[0-9a-f]{{64}}$'
                  )
               OR (
                    decision_document->>'canonical_apply_decision_id'
                      IS NOT NULL
                    AND decision_document->>'canonical_apply_decision_id' !~
                      '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$'
                  )
               OR (
                    decision_document->>'disposition' = 'apply'
                    AND NOT (
                      decision_document->>'decision_kind'
                        <> 'explicit_omission'
                      AND decision_document->>'candidate_commitment' IS NOT NULL
                      AND decision_document->>'canonical_apply_decision_id'
                          IS NULL
                      AND decision_document->>'canonical_apply_disposition'
                          IS NULL
                    )
                  )
               OR (
                    decision_document->>'disposition' IN (
                      'privacy_suppressed','out_of_scope_by_rule'
                    )
                    AND NOT (
                      decision_document->>'decision_kind' = 'explicit_omission'
                      AND decision_document->>'candidate_commitment' IS NULL
                      AND decision_document->>'canonical_apply_decision_id'
                          IS NULL
                      AND decision_document->>'canonical_apply_disposition'
                          IS NULL
                    )
                  )
               OR (
                    decision_document->>'disposition' = 'coalesced_duplicate'
                    AND NOT (
                      decision_document->>'decision_kind'
                        <> 'explicit_omission'
                      AND decision_document->>'candidate_commitment' IS NOT NULL
                      AND decision_document->>'canonical_apply_decision_id'
                          IS NOT NULL
                      AND decision_document->>'canonical_apply_decision_id'
                          <> decision_document->>'decision_id'
                      AND decision_document->>'canonical_apply_disposition'
                          = 'apply'
                    )
                  ) THEN
              RAISE EXCEPTION 'identity_migration_manifest_invalid'
                USING ERRCODE = '22023';
            END IF;
          END LOOP;

          SELECT pg_catalog.count(*),
                 pg_catalog.min(source_item.ordinal),
                 pg_catalog.max(source_item.ordinal),
                 pg_catalog.count(DISTINCT source_item.ordinal)
            INTO source_rows, minimum_ordinal, maximum_ordinal,
                 distinct_ordinals
            FROM pg_catalog.jsonb_to_recordset(source_documents)
                 AS source_item(
                   source_item_id uuid,
                   ordinal integer,
                   source_table_kind text,
                   row_key_commitment text,
                   allowed_projection_commitment text
                 );
          IF source_rows <> target_run.source_item_count
             OR minimum_ordinal <> 0
             OR maximum_ordinal <> target_run.source_item_count - 1
             OR distinct_ordinals <> target_run.source_item_count THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;
          SELECT pg_catalog.count(*),
                 pg_catalog.min(decision_item.ordinal),
                 pg_catalog.max(decision_item.ordinal),
                 pg_catalog.count(DISTINCT decision_item.ordinal)
            INTO decision_rows, minimum_ordinal, maximum_ordinal,
                 distinct_ordinals
            FROM pg_catalog.jsonb_to_recordset(decision_documents)
                 AS decision_item(
                   decision_id uuid,
                   source_item_id uuid,
                   ordinal integer,
                   decision_kind text,
                   disposition text,
                   candidate_commitment text,
                   canonical_apply_decision_id uuid,
                   canonical_apply_disposition text,
                   decision_commitment text
                 );
          IF decision_rows <> target_run.decision_count
             OR minimum_ordinal <> 0
             OR maximum_ordinal <> target_run.decision_count - 1
             OR distinct_ordinals <> target_run.decision_count THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF EXISTS (
               SELECT source_item.source_item_id
                 FROM pg_catalog.jsonb_to_recordset(source_documents)
                      AS source_item(
                        source_item_id uuid, ordinal integer,
                        source_table_kind text, row_key_commitment text,
                        allowed_projection_commitment text
                      )
                GROUP BY source_item.source_item_id
               HAVING pg_catalog.count(*) <> 1
             )
             OR EXISTS (
               SELECT source_item.row_key_commitment
                 FROM pg_catalog.jsonb_to_recordset(source_documents)
                      AS source_item(
                        source_item_id uuid, ordinal integer,
                        source_table_kind text, row_key_commitment text,
                        allowed_projection_commitment text
                      )
                GROUP BY source_item.row_key_commitment
               HAVING pg_catalog.count(*) <> 1
             )
             OR EXISTS (
               SELECT decision_item.decision_id
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS decision_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                GROUP BY decision_item.decision_id
               HAVING pg_catalog.count(*) <> 1
             )
             OR EXISTS (
               SELECT decision_item.decision_commitment
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS decision_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                GROUP BY decision_item.decision_commitment
               HAVING pg_catalog.count(*) <> 1
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF EXISTS (
               WITH source_set AS MATERIALIZED (
                 SELECT *
                   FROM pg_catalog.jsonb_to_recordset(source_documents)
                        AS source_item(
                          source_item_id uuid, ordinal integer,
                          source_table_kind text, row_key_commitment text,
                          allowed_projection_commitment text
                        )
               ), decision_set AS MATERIALIZED (
                 SELECT *
                   FROM pg_catalog.jsonb_to_recordset(decision_documents)
                        AS decision_item(
                          decision_id uuid, source_item_id uuid,
                          ordinal integer, decision_kind text,
                          disposition text, candidate_commitment text,
                          canonical_apply_decision_id uuid,
                          canonical_apply_disposition text,
                          decision_commitment text
                        )
               )
               SELECT 1
                 FROM source_set AS source_item
                 LEFT JOIN decision_set AS decision_item
                   ON decision_item.source_item_id = source_item.source_item_id
                WHERE decision_item.decision_id IS NULL
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS decision_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                 LEFT JOIN pg_catalog.jsonb_to_recordset(source_documents)
                      AS source_item(
                        source_item_id uuid, ordinal integer,
                        source_table_kind text, row_key_commitment text,
                        allowed_projection_commitment text
                      )
                   ON source_item.source_item_id = decision_item.source_item_id
                WHERE source_item.source_item_id IS NULL
                   OR NOT (
                     (source_item.source_table_kind = 'schema_meta'
                      AND decision_item.decision_kind = 'explicit_omission')
                     OR (source_item.source_table_kind = 'identities'
                         AND decision_item.decision_kind IN (
                           'person','privacy_directive','person_status',
                           'legacy_role_candidate','explicit_omission'
                         ))
                     OR (source_item.source_table_kind = 'identity_aliases'
                         AND decision_item.decision_kind IN (
                           'alias','explicit_omission'
                         ))
                     OR (source_item.source_table_kind = 'enrollments'
                         AND decision_item.decision_kind IN (
                           'recognition_binding','explicit_omission'
                         ))
                     OR (source_item.source_table_kind = 'relationships'
                         AND decision_item.decision_kind IN (
                           'legacy_relationship_candidate','explicit_omission'
                         ))
                   )
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF EXISTS (
               SELECT decision_item.source_item_id,
                      decision_item.decision_kind
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS decision_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                WHERE decision_item.decision_kind <> 'privacy_directive'
                GROUP BY decision_item.source_item_id,
                         decision_item.decision_kind
               HAVING pg_catalog.count(*) > 1
             )
             OR EXISTS (
               SELECT privacy_item.source_item_id,
                      privacy_item.candidate_commitment
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS privacy_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                WHERE privacy_item.decision_kind = 'privacy_directive'
                GROUP BY privacy_item.source_item_id,
                         privacy_item.candidate_commitment
               HAVING pg_catalog.count(*) > 1
             )
             OR EXISTS (
               SELECT omission_item.source_item_id
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS omission_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                GROUP BY omission_item.source_item_id
               HAVING pg_catalog.bool_or(
                        omission_item.decision_kind = 'explicit_omission'
                      )
                  AND pg_catalog.count(*) <> 1
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          IF EXISTS (
               SELECT decision_item.decision_kind,
                      decision_item.candidate_commitment
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS decision_item(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                WHERE decision_item.disposition = 'apply'
                GROUP BY decision_item.decision_kind,
                         decision_item.candidate_commitment
               HAVING pg_catalog.count(*) <> 1
             )
             OR EXISTS (
               WITH decision_set AS MATERIALIZED (
                 SELECT *
                   FROM pg_catalog.jsonb_to_recordset(decision_documents)
                        AS decision_item(
                          decision_id uuid, source_item_id uuid,
                          ordinal integer, decision_kind text,
                          disposition text, candidate_commitment text,
                          canonical_apply_decision_id uuid,
                          canonical_apply_disposition text,
                          decision_commitment text
                        )
               )
               SELECT 1
                 FROM decision_set AS duplicate_item
                 LEFT JOIN decision_set AS apply_item
                   ON apply_item.decision_id =
                        duplicate_item.canonical_apply_decision_id
                  AND apply_item.decision_kind = duplicate_item.decision_kind
                  AND apply_item.disposition = 'apply'
                  AND apply_item.candidate_commitment =
                        duplicate_item.candidate_commitment
                WHERE duplicate_item.disposition = 'coalesced_duplicate'
                  AND apply_item.decision_id IS NULL
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_invalid'
              USING ERRCODE = '22023';
          END IF;

          PERFORM 1
            FROM operations.rollout_authorizations AS predecessor
           WHERE predecessor.authorization_id =
                 target_run.shadow_authorization_id
             AND predecessor.from_mode = 'record_only'
             AND predecessor.to_mode = 'shadow'
             AND predecessor.rule_version = target_run.shadow_rule_version
             AND predecessor.policy_version = target_run.policy_version
             AND predecessor.policy_digest = target_run.policy_digest
             AND predecessor.authorized_at <=
                 pg_catalog.clock_timestamp();
          IF NOT FOUND THEN
            RAISE EXCEPTION 'identity_migration_predecessor_invalid'
              USING ERRCODE = '55000';
          END IF;

          IF existing_run_found THEN
            IF existing_run.operator_request_id
                 IS DISTINCT FROM target_run.operator_request_id
               OR existing_run.contract_version
                  IS DISTINCT FROM target_run.contract_version
               OR existing_run.source_schema_version
                  IS DISTINCT FROM target_run.source_schema_version
               OR existing_run.source_projection_contract_version
                  IS DISTINCT FROM
                     target_run.source_projection_contract_version
               OR existing_run.importer_version
                  IS DISTINCT FROM target_run.importer_version
               OR existing_run.canonicalization_version
                  IS DISTINCT FROM target_run.canonicalization_version
               OR existing_run.projection_version
                  IS DISTINCT FROM target_run.projection_version
               OR existing_run.shadow_rule_version
                  IS DISTINCT FROM target_run.shadow_rule_version
               OR existing_run.commitment_algorithm
                  IS DISTINCT FROM target_run.commitment_algorithm
               OR existing_run.commitment_key_fingerprint
                  IS DISTINCT FROM target_run.commitment_key_fingerprint
               OR existing_run.commitment_key_epoch
                  IS DISTINCT FROM target_run.commitment_key_epoch
               OR existing_run.source_item_count
                  IS DISTINCT FROM target_run.source_item_count
               OR existing_run.decision_count
                  IS DISTINCT FROM target_run.decision_count
               OR existing_run.logical_source_manifest_commitment
                  IS DISTINCT FROM
                     target_run.logical_source_manifest_commitment
               OR existing_run.projection_manifest_commitment
                  IS DISTINCT FROM target_run.projection_manifest_commitment
               OR existing_run.source_projection_contract_digest
                  IS DISTINCT FROM
                     target_run.source_projection_contract_digest
               OR existing_run.review_receipt_commitment
                  IS DISTINCT FROM target_run.review_receipt_commitment
               OR existing_run.policy_version
                  IS DISTINCT FROM target_run.policy_version
               OR existing_run.policy_digest
                  IS DISTINCT FROM target_run.policy_digest
               OR existing_run.shadow_authorization_id
                  IS DISTINCT FROM target_run.shadow_authorization_id
               OR existing_run.release_manifest_digest
                  IS DISTINCT FROM target_run.release_manifest_digest
               OR existing_run.migration_tool_bundle_digest
                  IS DISTINCT FROM target_run.migration_tool_bundle_digest
               OR existing_run.core_oci_manifest_digest
                  IS DISTINCT FROM target_run.core_oci_manifest_digest
               OR existing_run.core_schema_digest
                  IS DISTINCT FROM target_run.core_schema_digest
               OR existing_run.core_capability_digest
                  IS DISTINCT FROM target_run.core_capability_digest
               OR existing_run.signature_algorithm
                  IS DISTINCT FROM target_run.signature_algorithm
               OR existing_run.signing_key_fingerprint
                  IS DISTINCT FROM target_run.signing_key_fingerprint
               OR existing_run.review_signature
                  IS DISTINCT FROM target_run.review_signature
               OR existing_run.expires_at
                  IS DISTINCT FROM target_run.expires_at THEN
              RAISE EXCEPTION 'identity_migration_manifest_conflict'
                USING ERRCODE = '23505';
            END IF;
            IF erasure_affected THEN
              RAISE EXCEPTION 'identity_migration_erasure_affected'
                USING ERRCODE = '55000';
            END IF;

            SELECT pg_catalog.count(*)
              INTO existing_rows
              FROM operations.reviewed_identity_migration_source_items
             WHERE run_id = target_run.run_id;
            IF existing_rows <> target_run.source_item_count
               OR EXISTS (
                 SELECT 1
                   FROM pg_catalog.jsonb_to_recordset(source_documents)
                        AS expected_source(
                          source_item_id uuid, ordinal integer,
                          source_table_kind text, row_key_commitment text,
                          allowed_projection_commitment text
                        )
                  WHERE NOT EXISTS (
                    SELECT 1
                      FROM operations.reviewed_identity_migration_source_items
                           AS stored_source
                     WHERE stored_source.run_id = target_run.run_id
                       AND stored_source.source_item_id =
                           expected_source.source_item_id
                       AND stored_source.ordinal = expected_source.ordinal
                       AND stored_source.source_table_kind =
                           expected_source.source_table_kind
                       AND stored_source.row_key_commitment =
                           expected_source.row_key_commitment
                       AND stored_source.allowed_projection_commitment =
                           expected_source.allowed_projection_commitment
                  )
               ) THEN
              RAISE EXCEPTION 'identity_migration_manifest_conflict'
                USING ERRCODE = '23505';
            END IF;

            SELECT pg_catalog.count(*)
              INTO existing_rows
              FROM operations.reviewed_identity_migration_decisions
             WHERE run_id = target_run.run_id;
            IF existing_rows <> target_run.decision_count
               OR EXISTS (
                 SELECT 1
                   FROM pg_catalog.jsonb_to_recordset(decision_documents)
                        AS expected_decision(
                          decision_id uuid, source_item_id uuid,
                          ordinal integer, decision_kind text,
                          disposition text, candidate_commitment text,
                          canonical_apply_decision_id uuid,
                          canonical_apply_disposition text,
                          decision_commitment text
                        )
                  WHERE NOT EXISTS (
                    SELECT 1
                      FROM operations.reviewed_identity_migration_decisions
                           AS stored_decision
                     WHERE stored_decision.run_id = target_run.run_id
                       AND stored_decision.decision_id =
                           expected_decision.decision_id
                       AND stored_decision.source_item_id =
                           expected_decision.source_item_id
                       AND stored_decision.ordinal = expected_decision.ordinal
                       AND stored_decision.decision_kind =
                           expected_decision.decision_kind
                       AND stored_decision.disposition =
                           expected_decision.disposition
                       AND stored_decision.candidate_commitment
                           IS NOT DISTINCT FROM
                             expected_decision.candidate_commitment
                       AND stored_decision.canonical_apply_decision_id
                           IS NOT DISTINCT FROM
                             expected_decision.canonical_apply_decision_id
                       AND stored_decision.canonical_apply_disposition
                           IS NOT DISTINCT FROM
                             expected_decision.canonical_apply_disposition
                       AND stored_decision.decision_commitment =
                           expected_decision.decision_commitment
                  )
               ) THEN
              RAISE EXCEPTION 'identity_migration_manifest_conflict'
                USING ERRCODE = '23505';
            END IF;
            operation_time := pg_catalog.clock_timestamp();
            IF NOT EXISTS (
              SELECT 1
                FROM pg_catalog.pg_roles AS active_caller
               WHERE active_caller.rolname = '{CALLER_ROLE}'
                 AND active_caller.rolvaliduntil > operation_time
                 AND active_caller.rolvaliduntil <=
                     operation_time + interval '15 minutes'
                 AND NOT pg_catalog.pg_has_role(
                       active_caller.rolname,
                       '{KERNEL_ROLE}',
                       'SET'
                     )
            ) THEN
              RAISE EXCEPTION 'identity_migration_role_activation_invalid'
                USING ERRCODE = '42501';
            END IF;
            RETURN target_run.run_id;
          END IF;

          IF EXISTS (
               SELECT 1
                 FROM operations.reviewed_identity_migration_runs
                WHERE operator_request_id = target_run.operator_request_id
                   OR review_receipt_commitment =
                      target_run.review_receipt_commitment
                   OR shadow_authorization_id =
                      target_run.shadow_authorization_id
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.jsonb_to_recordset(source_documents)
                      AS candidate_source(
                        source_item_id uuid, ordinal integer,
                        source_table_kind text, row_key_commitment text,
                        allowed_projection_commitment text
                      )
                 JOIN operations.reviewed_identity_migration_source_items
                      AS stored_source
                   ON stored_source.source_item_id =
                      candidate_source.source_item_id
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.jsonb_to_recordset(decision_documents)
                      AS candidate_decision(
                        decision_id uuid, source_item_id uuid,
                        ordinal integer, decision_kind text,
                        disposition text, candidate_commitment text,
                        canonical_apply_decision_id uuid,
                        canonical_apply_disposition text,
                        decision_commitment text
                      )
                 JOIN operations.reviewed_identity_migration_decisions
                      AS stored_decision
                   ON stored_decision.decision_id =
                        candidate_decision.decision_id
                   OR stored_decision.decision_commitment =
                        candidate_decision.decision_commitment
             ) THEN
            RAISE EXCEPTION 'identity_migration_manifest_conflict'
              USING ERRCODE = '23505';
          END IF;

          -- An exact full-set replay remains valid after expiry so a caller
          -- can resolve a lost commit response.  Expiry is an admission gate
          -- for a new run, not a reason to make an admitted receipt unknowable.
          operation_time := pg_catalog.clock_timestamp();
          IF target_run.expires_at <= operation_time
             OR target_run.expires_at > operation_time + interval '24 hours' THEN
            RAISE EXCEPTION 'identity_migration_review_expired'
              USING ERRCODE = '55000';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_roles AS active_caller
             WHERE active_caller.rolname = '{CALLER_ROLE}'
               AND active_caller.rolvaliduntil > operation_time
               AND active_caller.rolvaliduntil <=
                   operation_time + interval '15 minutes'
               AND NOT pg_catalog.pg_has_role(
                     active_caller.rolname,
                     '{KERNEL_ROLE}',
                     'SET'
                   )
          ) THEN
            RAISE EXCEPTION 'identity_migration_role_activation_invalid'
              USING ERRCODE = '42501';
          END IF;

          BEGIN
            INSERT INTO operations.reviewed_identity_migration_runs (
              run_id, operator_request_id, contract_version,
              source_schema_version, source_projection_contract_version,
              importer_version, canonicalization_version, projection_version,
              shadow_rule_version, commitment_algorithm,
              commitment_key_fingerprint, commitment_key_epoch,
              source_item_count, decision_count,
              logical_source_manifest_commitment,
              projection_manifest_commitment,
              source_projection_contract_digest, review_receipt_commitment,
              policy_version, policy_digest, shadow_authorization_id,
              release_manifest_digest, migration_tool_bundle_digest,
              core_oci_manifest_digest, core_schema_digest,
              core_capability_digest, signature_algorithm,
              signing_key_fingerprint, review_signature, expires_at
            ) VALUES (
              target_run.run_id, target_run.operator_request_id,
              target_run.contract_version, target_run.source_schema_version,
              target_run.source_projection_contract_version,
              target_run.importer_version,
              target_run.canonicalization_version,
              target_run.projection_version, target_run.shadow_rule_version,
              target_run.commitment_algorithm,
              target_run.commitment_key_fingerprint,
              target_run.commitment_key_epoch, target_run.source_item_count,
              target_run.decision_count,
              target_run.logical_source_manifest_commitment,
              target_run.projection_manifest_commitment,
              target_run.source_projection_contract_digest,
              target_run.review_receipt_commitment,
              target_run.policy_version, target_run.policy_digest,
              target_run.shadow_authorization_id,
              target_run.release_manifest_digest,
              target_run.migration_tool_bundle_digest,
              target_run.core_oci_manifest_digest,
              target_run.core_schema_digest,
              target_run.core_capability_digest,
              target_run.signature_algorithm,
              target_run.signing_key_fingerprint,
              target_run.review_signature, target_run.expires_at
            );
            INSERT INTO operations.reviewed_identity_migration_source_items (
              source_item_id, run_id, ordinal, source_table_kind,
              row_key_commitment, allowed_projection_commitment
            )
            SELECT source_item.source_item_id, target_run.run_id,
                   source_item.ordinal, source_item.source_table_kind,
                   source_item.row_key_commitment,
                   source_item.allowed_projection_commitment
              FROM pg_catalog.jsonb_to_recordset(source_documents)
                   AS source_item(
                     source_item_id uuid, ordinal integer,
                     source_table_kind text, row_key_commitment text,
                     allowed_projection_commitment text
                   );
            INSERT INTO operations.reviewed_identity_migration_decisions (
              decision_id, run_id, source_item_id, ordinal, decision_kind,
              disposition, candidate_commitment,
              canonical_apply_decision_id, canonical_apply_disposition,
              decision_commitment
            )
            SELECT decision_item.decision_id, target_run.run_id,
                   decision_item.source_item_id, decision_item.ordinal,
                   decision_item.decision_kind, decision_item.disposition,
                   decision_item.candidate_commitment,
                   decision_item.canonical_apply_decision_id,
                   decision_item.canonical_apply_disposition,
                   decision_item.decision_commitment
              FROM pg_catalog.jsonb_to_recordset(decision_documents)
                   AS decision_item(
                     decision_id uuid, source_item_id uuid,
                     ordinal integer, decision_kind text,
                     disposition text, candidate_commitment text,
                     canonical_apply_decision_id uuid,
                     canonical_apply_disposition text,
                     decision_commitment text
                   );
          EXCEPTION
            WHEN unique_violation THEN
              RAISE EXCEPTION 'identity_migration_manifest_conflict'
                USING ERRCODE = '23505';
            WHEN foreign_key_violation OR check_violation THEN
              RAISE EXCEPTION 'identity_migration_manifest_invalid'
                USING ERRCODE = '22023';
          END;
          RETURN target_run.run_id;
        END;
        $function$
        """
    )


def _install_replay_guard_trigger() -> None:
    op.execute(
        f"""
        DO $guard_name$
        BEGIN
          IF pg_catalog.to_regprocedure(
               '{REPLAY_GUARD_TRIGGER_SIGNATURE}'
             ) IS NOT NULL
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS existing_trigger
                WHERE existing_trigger.tgrelid =
                      'operations.reviewed_identity_migration_erasure_impacts'::regclass
                  AND existing_trigger.tgname =
                      'reviewed_identity_migration_erasure_replay_guard'
             ) THEN
            RAISE EXCEPTION 'identity_migration_replay_guard_name_occupied'
              USING ERRCODE = '55000';
          END IF;
        END
        $guard_name$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {REPLAY_GUARD_TRIGGER_SIGNATURE}
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
        BEGIN
          IF current_user <> '{KERNEL_ROLE}'
             OR session_user NOT IN ('home_agent_owner','home_agent_erasure')
             OR TG_OP <> 'INSERT'
             OR TG_TABLE_SCHEMA <> 'operations'
             OR TG_TABLE_NAME <>
                'reviewed_identity_migration_erasure_impacts' THEN
            RAISE EXCEPTION 'identity_migration_replay_guard_context_invalid'
              USING ERRCODE = '42501';
          END IF;
          PERFORM pg_catalog.set_config('lock_timeout', '5s', true);
          PERFORM pg_catalog.pg_advisory_xact_lock(
            {PER_RUN_ADVISORY_NAMESPACE},
            pg_catalog.hashtext(NEW.run_id::text)
          );
          UPDATE operations.reviewed_identity_migration_runs
             SET expires_at = expires_at
           WHERE run_id = NEW.run_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'identity_migration_replay_guard_missing'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        "CREATE TRIGGER reviewed_identity_migration_erasure_replay_guard "
        "BEFORE INSERT ON "
        "operations.reviewed_identity_migration_erasure_impacts "
        "FOR EACH ROW EXECUTE FUNCTION "
        f"{REPLAY_GUARD_TRIGGER_SIGNATURE}"
    )


def _secure_functions() -> None:
    roles = ", ".join(APPLICATION_ROLES)
    for signature in FUNCTION_SIGNATURES:
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {KERNEL_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, {roles}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {CALLER_ROLE}")
    op.execute(
        f"ALTER FUNCTION {REPLAY_GUARD_TRIGGER_SIGNATURE} OWNER TO {KERNEL_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {REPLAY_GUARD_TRIGGER_SIGNATURE} "
        f"FROM PUBLIC, {roles}"
    )
    op.execute(f"REVOKE CREATE ON SCHEMA operations FROM {KERNEL_ROLE}")


def _assert_replay_guard_trigger() -> None:
    op.execute(
        f"""
        DO $guard_contract$
        DECLARE
          guard_function_oid oid;
          kernel_oid oid;
          caller_oid oid;
        BEGIN
          guard_function_oid := pg_catalog.to_regprocedure(
            '{REPLAY_GUARD_TRIGGER_SIGNATURE}'
          );
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          IF guard_function_oid IS NULL
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS guard_function
                WHERE guard_function.oid = guard_function_oid
                  AND guard_function.proowner = kernel_oid
                  AND guard_function.prosecdef = true
                  AND guard_function.proconfig =
                      ARRAY['search_path=pg_catalog, pg_temp']::text[]
                  AND guard_function.proacl IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(guard_function.proacl)
                           AS guard_acl
                     WHERE guard_acl.privilege_type = 'EXECUTE'
                  )
                  AND NOT pg_catalog.has_function_privilege(
                        caller_oid,
                        guard_function.oid,
                        'EXECUTE'
                      )
             )
             OR NOT EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_trigger AS guard_trigger
                WHERE guard_trigger.tgrelid =
                      'operations.reviewed_identity_migration_erasure_impacts'::regclass
                  AND guard_trigger.tgname =
                      'reviewed_identity_migration_erasure_replay_guard'
                  AND guard_trigger.tgfoid = guard_function_oid
                  AND guard_trigger.tgenabled = 'O'
                  AND guard_trigger.tgisinternal = false
                  AND guard_trigger.tgtype = 7
                  AND guard_trigger.tgnargs = 0
             ) THEN
            RAISE EXCEPTION 'identity_migration_replay_guard_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $guard_contract$
        """
    )


def _assert_installed_ownership() -> None:
    op.execute(
        f"""
        DO $ownership_contract$
        DECLARE
          kernel_oid oid;
          caller_oid oid;
          database_oid oid;
          capabilities_oid oid;
          registration_oid oid;
          replay_guard_oid oid;
        BEGIN
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles
           WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT database_oid
            FROM pg_catalog.pg_database
           WHERE datname = pg_catalog.current_database();
          capabilities_oid := pg_catalog.to_regprocedure(
            'operations.reviewed_identity_migration_capabilities()'
          );
          registration_oid := pg_catalog.to_regprocedure(
            'operations.register_reviewed_identity_migration(jsonb)'
          );
          replay_guard_oid := pg_catalog.to_regprocedure(
            '{REPLAY_GUARD_TRIGGER_SIGNATURE}'
          );
          IF capabilities_oid IS NULL OR registration_oid IS NULL
             OR replay_guard_oid IS NULL
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_shdepend AS caller_ownership
                WHERE caller_ownership.refobjid = caller_oid
                  AND caller_ownership.deptype = 'o'
             )
             OR (
               SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_shdepend AS kernel_ownership
                WHERE kernel_ownership.refobjid = kernel_oid
                  AND kernel_ownership.deptype = 'o'
             ) <> 3
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_shdepend AS kernel_ownership
                WHERE kernel_ownership.refobjid = kernel_oid
                  AND kernel_ownership.deptype = 'o'
                  AND NOT (
                    kernel_ownership.dbid = database_oid
                    AND kernel_ownership.classid =
                        'pg_catalog.pg_proc'::regclass
                    AND kernel_ownership.objid IN (
                      capabilities_oid,
                      registration_oid,
                      replay_guard_oid
                    )
                    AND kernel_ownership.objsubid = 0
                  )
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS owned_function
                WHERE owned_function.oid IN (
                        capabilities_oid,
                        registration_oid,
                        replay_guard_oid
                      )
                  AND owned_function.proowner <> kernel_oid
             ) THEN
            RAISE EXCEPTION 'identity_migration_ownership_contract_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $ownership_contract$
        """
    )


def upgrade() -> None:
    _require_roles()
    _install_shadow_uniqueness()
    _install_manifest_rls()
    _install_privileges()
    _install_capabilities_function()
    _install_registration_function()
    _install_replay_guard_trigger()
    _secure_functions()
    _assert_replay_guard_trigger()
    _assert_installed_ownership()


def downgrade() -> None:
    op.execute(
        """
        DO $downgrade$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM operations.reviewed_identity_migration_runs
          ) THEN
            RAISE EXCEPTION 'identity_migration_kernel_downgrade_blocked'
              USING ERRCODE = '2BP01';
          END IF;
        END
        $downgrade$
        """
    )
    _assert_installed_ownership()
    _assert_replay_guard_trigger()
    op.execute(
        "DROP TRIGGER reviewed_identity_migration_erasure_replay_guard ON "
        "operations.reviewed_identity_migration_erasure_impacts"
    )
    op.execute(f"DROP FUNCTION {REPLAY_GUARD_TRIGGER_SIGNATURE}")
    for signature in reversed(FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for table in MANIFEST_TABLES:
        name = table.rsplit(".", 1)[1]
        op.execute(f"DROP POLICY IF EXISTS {name}_manifest_kernel_select ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {name}_manifest_kernel_insert ON {table}")
    op.execute(
        "DROP POLICY IF EXISTS "
        "identity_migration_erasure_impacts_manifest_kernel_select ON "
        "operations.reviewed_identity_migration_erasure_impacts"
    )
    op.execute(
        "DROP POLICY IF EXISTS "
        "reviewed_identity_migration_runs_replay_guard_update ON "
        "operations.reviewed_identity_migration_runs"
    )
    op.execute(
        "DROP POLICY IF EXISTS "
        "reviewed_identity_migration_runs_replay_guard_trigger_select ON "
        "operations.reviewed_identity_migration_runs"
    )
    _assert_shadow_index()
    op.execute(
        "DROP INDEX " "operations.uq_identity_migration_shadow_authorization_once"
    )
    for schema_name in ("operations",):
        op.execute(
            f"REVOKE USAGE ON SCHEMA {schema_name} "
            f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
        )
    for table in PHASE3_EVIDENCE_TABLES:
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {table} "
            f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
        )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE operations.rollout_authorizations "
        f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
    )
    op.execute(
        "REVOKE SELECT (authorization_id, from_mode, to_mode, rule_version, "
        "policy_version, policy_digest, authorized_at) "
        "ON operations.rollout_authorizations "
        f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
    )
    op.execute(
        "REVOKE UPDATE (expires_at) ON "
        "operations.reviewed_identity_migration_runs "
        f"FROM {KERNEL_ROLE}, {CALLER_ROLE}"
    )
