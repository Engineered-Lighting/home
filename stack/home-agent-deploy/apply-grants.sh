#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$script_dir/identity-api-acl.sql" ] || {
  echo "identity API ACL contract is missing" >&2
  exit 78
}

export PGPASSWORD="$(tr -d '\r\n' < "$POSTGRES_OWNER_PASSWORD_FILE")"
[ -n "$PGPASSWORD" ] || { echo "empty owner password" >&2; exit 78; }

psql -v ON_ERROR_STOP=1 <<'SQL'
-- First committed statement: remove any stale identity authority before any
-- other grant work can fail. The separate exact ACL file only adds reviewed
-- capabilities after this fail-closed reset succeeds.
DO $identity_api_acl_reset$
DECLARE
  target_table record;
  target_role text;
BEGIN
  FOR target_table IN
    SELECT candidate_table.relname,
           pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           ) AS column_list
      FROM pg_catalog.pg_class AS candidate_table
      JOIN pg_catalog.pg_namespace AS table_namespace
        ON table_namespace.oid = candidate_table.relnamespace
      JOIN pg_catalog.pg_attribute AS attribute
        ON attribute.attrelid = candidate_table.oid
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped
     WHERE table_namespace.nspname = 'identity'
       AND candidate_table.relkind IN ('r','p','v','m','f')
     GROUP BY candidate_table.relname
  LOOP
    FOREACH target_role IN ARRAY ARRAY['home_agent_api','PUBLIC']::text[]
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE identity.%I FROM %s',
        target_table.relname,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE identity.%2$I FROM %3$s',
        target_table.column_list,
        target_table.relname,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
    END LOOP;
  END LOOP;
END
$identity_api_acl_reset$;

-- Commit caller-side E2 function quarantine before any positive grant work.
-- A same-owner SECURITY DEFINER replacement must not retain a previously
-- reviewed EXECUTE grant merely because any later admission step rejects it.
DO $identity_erasure_e2_function_quarantine$
DECLARE
  function_oid regprocedure;
  grantee_sql text;
  target_role text;
  target_signature text;
BEGIN
  FOREACH target_signature IN ARRAY ARRAY[
    'privacy.identity_person_is_blocked(uuid)',
    'privacy.identity_principal_is_blocked(uuid)',
    'privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)',
    'privacy.identity_fact_version_is_visible(uuid)',
    'privacy.reject_tombstoned_identity_write()',
    'privacy.enforce_identity_person_erasure_residual_set()',
    'privacy.replay_identity_person_retrieval_block_v2(jsonb)'
  ]::text[]
  LOOP
    function_oid := pg_catalog.to_regprocedure(target_signature);
    IF function_oid IS NULL THEN
      CONTINUE;
    END IF;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s',
        function_oid, grantee_sql
      );
    END LOOP;
  END LOOP;
END
$identity_erasure_e2_function_quarantine$;

ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
  REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_api;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
  REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;

GRANT USAGE ON SCHEMA ingest TO home_agent_ingest, home_agent_api,
  home_agent_worker, home_agent_erasure;
GRANT USAGE ON SCHEMA identity, knowledge, engagement, privacy, operations
  TO home_agent_api, home_agent_worker, home_agent_erasure;
GRANT USAGE ON SCHEMA identity TO home_agent_binding_operator;
GRANT USAGE ON SCHEMA identity, knowledge, engagement TO home_agent_ingest;
GRANT USAGE ON SCHEMA operations TO home_agent_ingest, home_agent_rollout;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations FROM home_agent_rollout;
-- Start the isolated review credential from an empty ACL on every replay.
-- Only the narrow two-party binding grants below are added back.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations
  FROM home_agent_binding_operator;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA ingest, identity, knowledge,
  engagement, privacy, operations FROM home_agent_binding_operator;
GRANT SELECT ON TABLE public.alembic_version
  TO home_agent_api, home_agent_ingest, home_agent_worker, home_agent_erasure,
  home_agent_rollout, home_agent_binding_operator;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ingest TO home_agent_ingest;
-- Accepted envelope headers are append-only evidence. Projection offsets and
-- stream state may update, but no online ingest credential may backdate or
-- rewrite the rollout boundary after acknowledgement.
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE ingest.envelopes FROM home_agent_ingest;
REVOKE INSERT ON TABLE ingest.envelopes FROM home_agent_ingest;
GRANT SELECT ON TABLE ingest.envelopes TO home_agent_ingest;
GRANT INSERT (
  envelope_id, stream_id, sequence, event_type, payload_bytes, entity_id,
  source_event_id, source_observed_at, edge_received_at, payload_sha256,
  root_observation_id, evidence_family_id, dependency_domain, freshness,
  coverage, clock_state, ha_context, metadata
) ON ingest.envelopes TO home_agent_ingest;
GRANT SELECT ON TABLE identity.source_entity_bindings, identity.privacy_directives,
  identity.edge_privacy_blocks,
  identity.edge_privacy_user_blocks, identity.ha_user_bindings,
  engagement.preferences,
  knowledge.places, knowledge.place_locators, knowledge.visits,
  engagement.initiatives TO home_agent_ingest;
GRANT INSERT, UPDATE ON TABLE knowledge.visits TO home_agent_ingest;
GRANT INSERT ON TABLE engagement.initiatives TO home_agent_ingest;
GRANT UPDATE ON TABLE engagement.initiatives TO home_agent_ingest;
GRANT SELECT ON TABLE operations.erasure_ledger_state, operations.outbox
  TO home_agent_ingest;
GRANT SELECT ON TABLE operations.erasure_ledger_state TO home_agent_rollout;
GRANT SELECT ON TABLE operations.rollout_authorizations TO home_agent_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA ingest TO home_agent_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA knowledge,
  engagement, privacy, operations TO home_agent_api;
-- Principal binding is a two-party workflow. Operator access is scoped by the
-- unforgeable PostgreSQL session_user. Online subject grants are added only by
-- the final identity-api-acl.sql contract.
REVOKE ALL ON TABLE identity.principal_binding_requests,
  identity.principal_binding_proposals
  FROM PUBLIC, home_agent_ingest, home_agent_worker, home_agent_erasure,
  home_agent_rollout, home_agent_binding_operator, home_agent_api;
GRANT SELECT ON TABLE identity.principal_binding_requests
  TO home_agent_binding_operator;
GRANT UPDATE (state, staged_at, expires_at, closed_at)
  ON TABLE identity.principal_binding_requests TO home_agent_binding_operator;
GRANT SELECT, INSERT ON TABLE identity.principal_binding_proposals
  TO home_agent_binding_operator;
GRANT UPDATE (state) ON TABLE identity.principal_binding_proposals
  TO home_agent_binding_operator;
GRANT SELECT ON TABLE identity.people, identity.principals,
  identity.ha_user_bindings, identity.edge_privacy_user_blocks,
  identity.privacy_directives
  TO home_agent_binding_operator;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA operations TO home_agent_worker;
-- A worker may prove maintenance only by calling the fenced database kernels.
-- Revoke schema-wide/default DML again so no online credential can forge the
-- singleton row, including the worker credential itself.
REVOKE ALL ON TABLE operations.worker_maintenance_state
  FROM PUBLIC, home_agent_api, home_agent_ingest, home_agent_worker,
  home_agent_erasure, home_agent_rollout, home_agent_binding_operator;
GRANT SELECT ON TABLE operations.worker_maintenance_state
  TO home_agent_api, home_agent_ingest, home_agent_worker, home_agent_rollout;
REVOKE ALL ON FUNCTION operations.register_worker_maintenance(uuid),
  operations.heartbeat_worker_maintenance(uuid),
  operations.run_worker_maintenance_cycle(uuid,bigint),
  operations.fail_worker_maintenance(uuid,character varying),
  operations.stop_worker_maintenance(uuid)
  FROM PUBLIC, home_agent_api, home_agent_ingest, home_agent_worker,
  home_agent_erasure, home_agent_rollout, home_agent_binding_operator;
GRANT EXECUTE ON FUNCTION operations.register_worker_maintenance(uuid),
  operations.heartbeat_worker_maintenance(uuid),
  operations.run_worker_maintenance_cycle(uuid,bigint),
  operations.fail_worker_maintenance(uuid,character varying),
  operations.stop_worker_maintenance(uuid)
  TO home_agent_worker;
REVOKE INSERT, UPDATE, DELETE ON TABLE operations.rollout_authorizations
  FROM home_agent_worker;
REVOKE INSERT, UPDATE, DELETE ON TABLE operations.rollout_authorizations
  FROM home_agent_api;
GRANT SELECT ON TABLE operations.rollout_authorizations TO home_agent_worker;
GRANT SELECT ON TABLE operations.rollout_authorizations TO home_agent_api;
GRANT SELECT, INSERT ON TABLE operations.rollout_authorizations
  TO home_agent_rollout;
REVOKE ALL ON TABLE operations.phase2_rollout_evidence
  FROM home_agent_api, home_agent_ingest, home_agent_worker, home_agent_rollout;
GRANT SELECT ON TABLE operations.phase2_rollout_evidence
  TO home_agent_api, home_agent_ingest, home_agent_worker, home_agent_rollout;
GRANT SELECT, UPDATE ON TABLE privacy.erasure_requests TO home_agent_worker;
GRANT SELECT ON TABLE identity.privacy_directives TO home_agent_worker;
GRANT SELECT ON TABLE privacy.auto_expiry_schedules TO home_agent_worker;
GRANT UPDATE (state, completed_at) ON TABLE privacy.auto_expiry_schedules
  TO home_agent_worker;
GRANT SELECT, INSERT ON TABLE privacy.auto_expiry_receipts TO home_agent_worker;
GRANT EXECUTE ON FUNCTION privacy.apply_person_auto_expiry(uuid)
  TO home_agent_worker;
REVOKE EXECUTE ON FUNCTION privacy.expire_principal_binding_work(timestamptz)
  FROM home_agent_worker;
-- The restore credential is not a general deletion principal. Start every
-- replay from an empty application-table ACL, including stale column grants,
-- then restore only the tables used by the descriptor/person ledger replay
-- implementation below. SECURITY DEFINER kernels perform identity cascades;
-- the login never receives direct People or binding mutation authority.
DO $erasure_runtime_acl_reset$
DECLARE
  target_table record;
BEGIN
  FOR target_table IN
    SELECT table_namespace.nspname,
           candidate_table.relname,
           pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           ) AS column_list
      FROM pg_catalog.pg_class AS candidate_table
      JOIN pg_catalog.pg_namespace AS table_namespace
        ON table_namespace.oid = candidate_table.relnamespace
      JOIN pg_catalog.pg_attribute AS attribute
        ON attribute.attrelid = candidate_table.oid
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped
     WHERE table_namespace.nspname IN (
       'ingest','identity','knowledge','engagement','privacy','operations'
     )
       AND candidate_table.relkind IN ('r','p','v','m','f')
     GROUP BY table_namespace.nspname, candidate_table.relname
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM home_agent_erasure',
      target_table.nspname, target_table.relname
    );
    EXECUTE pg_catalog.format(
      'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
      'REFERENCES (%1$s) ON TABLE %2$I.%3$I FROM home_agent_erasure',
      target_table.column_list, target_table.nspname, target_table.relname
    );
  END LOOP;
END
$erasure_runtime_acl_reset$;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ingest, identity, knowledge,
  engagement, privacy, operations FROM home_agent_erasure;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA ingest, identity, knowledge,
  engagement, privacy, operations FROM home_agent_erasure;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest, identity,
  knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_erasure;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest, identity,
  knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON SEQUENCES FROM home_agent_erasure;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest, identity,
  knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON FUNCTIONS FROM home_agent_erasure;

GRANT SELECT ON TABLE ingest.artifact_links TO home_agent_erasure;
GRANT SELECT (person_id) ON TABLE identity.people TO home_agent_erasure;
GRANT SELECT (principal_id) ON TABLE identity.principals TO home_agent_erasure;
GRANT SELECT (
  binding_id, proposal_id, ha_user_id, principal_id, person_id,
  confirmed_by_principal_id, confirmed_at, revoked_at, source_artifact_id
) ON TABLE identity.ha_user_bindings TO home_agent_erasure;
GRANT SELECT ON TABLE knowledge.memory_transactions,
  knowledge.fact_versions TO home_agent_erasure;
GRANT UPDATE (
  state, exact_text_ciphertext, exact_text_nonce, exact_text_sha256,
  candidate, preview, updated_at
) ON TABLE knowledge.memory_transactions TO home_agent_erasure;
GRANT UPDATE (object, system_range, resolution)
  ON TABLE knowledge.fact_versions TO home_agent_erasure;
GRANT SELECT ON TABLE engagement.initiatives TO home_agent_erasure;
GRANT UPDATE (state, suppression_reason)
  ON TABLE engagement.initiatives TO home_agent_erasure;
GRANT INSERT (block_id, artifact_id, erasure_request_id)
  ON TABLE privacy.retrieval_blocks TO home_agent_erasure;
GRANT SELECT ON TABLE privacy.artifact_registry,
  privacy.auto_expiry_schedules TO home_agent_erasure;
GRANT UPDATE (status) ON TABLE privacy.artifact_registry TO home_agent_erasure;
GRANT UPDATE (state, completed_at)
  ON TABLE privacy.auto_expiry_schedules TO home_agent_erasure;
GRANT SELECT ON TABLE privacy.auto_expiry_receipts TO home_agent_erasure;
GRANT INSERT (
  receipt_id, schedule_id, outbox_id, ledger_outbox_id, person_id,
  operation_codes, cascade_counts, residual_codes, receipt_sha256, completed_at
) ON TABLE privacy.auto_expiry_receipts TO home_agent_erasure;
GRANT SELECT (
  erasure_request_id, principal_id, scope, state, policy_digest, created_at,
  completed_at
) ON TABLE privacy.erasure_requests TO home_agent_erasure;
GRANT INSERT (
  erasure_request_id, principal_id, scope, state, policy_digest, completed_at
) ON TABLE privacy.erasure_requests TO home_agent_erasure;
GRANT UPDATE (state, completed_at)
  ON TABLE privacy.erasure_requests TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.apply_person_auto_expiry(uuid)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.expire_principal_binding_work(timestamptz)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION
  privacy.cancel_principal_binding_work_for_person(uuid,timestamptz)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.replay_person_auto_expiry(uuid,uuid,uuid)
  TO home_agent_erasure;
GRANT SELECT ON TABLE operations.erasure_replay_receipts
  TO home_agent_erasure;
GRANT INSERT (
  ledger_epoch, outbox_id, erasure_request_id, record_hash, record_digest
) ON TABLE operations.erasure_replay_receipts TO home_agent_erasure;
GRANT SELECT ON TABLE operations.erasure_ledger_state TO home_agent_erasure;
GRANT INSERT (state_key, recorded_epoch, recorded_head_hash)
  ON TABLE operations.erasure_ledger_state TO home_agent_erasure;
GRANT UPDATE (recorded_epoch, recorded_head_hash, updated_at)
  ON TABLE operations.erasure_ledger_state TO home_agent_erasure;
GRANT SELECT ON TABLE operations.outbox TO home_agent_erasure;
GRANT UPDATE (
  state, claim_token, claimed_at, completed_at, last_error_code
) ON TABLE operations.outbox TO home_agent_erasure;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest
  GRANT SELECT, INSERT, UPDATE ON TABLES TO home_agent_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA knowledge,
  engagement, privacy, operations
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO home_agent_api;

-- Revision 0007 is an owner-only schema foundation. Repeat this exact revoke
-- after both schema-wide and default API grants so a grant-runtime replay
-- cannot accidentally expose candidate migration authority to any online,
-- operator, erasure, rollout, or SQL-backup credential. A later reviewed
-- migration must introduce a dedicated writer and its narrow API atomically.
--
-- Production remains pinned to 0006a, where none of these relations exists.
-- Quarantine each relation that does exist in a committed statement before
-- validating the all-or-none 0007 set. A partial/tampered migration therefore
-- loses both table and independently granted column privileges even though the
-- following validation aborts grant replay.
DO $phase3_identity_foundation_acl$
DECLARE
  candidate_tables text[] := ARRAY[
    'operations.reviewed_identity_migration_runs',
    'operations.reviewed_identity_migration_source_items',
    'operations.reviewed_identity_migration_decisions',
    'operations.reviewed_identity_migration_item_receipts',
    'operations.reviewed_identity_migration_finalizations',
    'operations.reviewed_identity_finalizer_admissions',
    'operations.reviewed_identity_migration_projection_lineage',
    'operations.reviewed_identity_migration_projection_subjects',
    'operations.legacy_identity_writer_evidence',
    'operations.privacy_cutover_check_receipts',
    'operations.semantic_authority_cutovers',
    'operations.reviewed_identity_migration_erasure_impacts'
  ]::text[];
  column_list text;
  grantee_sql text;
  target_name text;
  target_role text;
  target_table regclass;
BEGIN
  FOREACH target_name IN ARRAY candidate_tables
  LOOP
    target_table := pg_catalog.to_regclass(target_name);
    IF target_table IS NULL THEN
      CONTINUE;
    END IF;
    SELECT pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           )
      INTO STRICT column_list
      FROM pg_catalog.pg_attribute AS attribute
     WHERE attribute.attrelid = target_table
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    FOREACH target_role IN ARRAY ARRAY[
      'PUBLIC', 'home_agent_api', 'home_agent_binding_operator',
      'home_agent_ingest', 'home_agent_worker', 'home_agent_erasure',
      'home_agent_rollout', 'home_agent_backup',
      'home_agent_identity_migration', 'home_agent_identity_kernel',
      'home_agent_identity_finalizer', 'home_agent_identity_finalizer_kernel'
    ]::text[]
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s',
        target_table, grantee_sql
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s',
        column_list, target_table, grantee_sql
      );
    END LOOP;
  END LOOP;
END
$phase3_identity_foundation_acl$;

DO $phase3_identity_foundation_set_validation$
DECLARE
  candidate_tables text[] := ARRAY[
    'operations.reviewed_identity_migration_runs',
    'operations.reviewed_identity_migration_source_items',
    'operations.reviewed_identity_migration_decisions',
    'operations.reviewed_identity_migration_item_receipts',
    'operations.reviewed_identity_migration_finalizations',
    'operations.legacy_identity_writer_evidence',
    'operations.privacy_cutover_check_receipts',
    'operations.semantic_authority_cutovers',
    'operations.reviewed_identity_migration_erasure_impacts'
  ]::text[];
  present_table_count integer;
BEGIN
  SELECT pg_catalog.count(*)
    INTO STRICT present_table_count
    FROM pg_catalog.unnest(candidate_tables) AS candidate(target_name)
   WHERE pg_catalog.to_regclass(candidate.target_name) IS NOT NULL;
  IF present_table_count NOT IN (
    0, pg_catalog.cardinality(candidate_tables)
  ) THEN
    RAISE EXCEPTION 'partial Phase 3 identity authority table set'
      USING ERRCODE = '55000';
  END IF;
END
$phase3_identity_foundation_set_validation$;

-- Revision 0010 may be absent while production remains pinned to 0006. If
-- its dormant erasure-operation table exists, grant replay must nevertheless
-- restore the owner-only boundary without making the older pin undeployable.
DO $identity_erasure_operation_acl$
BEGIN
  IF pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_erasure_operations'
     ) IS NOT NULL THEN
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
      'operations.reviewed_identity_migration_erasure_operations '
      'FROM PUBLIC, home_agent_api, home_agent_binding_operator, '
      'home_agent_ingest, home_agent_worker, home_agent_erasure, '
      'home_agent_rollout, home_agent_backup, '
      'home_agent_identity_migration, home_agent_identity_kernel, '
      'home_agent_identity_finalizer, home_agent_identity_finalizer_kernel';
  END IF;
END
$identity_erasure_operation_acl$;

-- Revision 0007 provisions an expired-by-default dormant identity-migration
-- login before any future database kernel exists. It receives no schema,
-- base-table, sequence, default-privilege, or function authority. With no
-- application-schema USAGE, and with exact 0008 function ACLs replayed below,
-- it cannot reach unrelated application functions.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations
  FROM home_agent_identity_migration, home_agent_identity_kernel;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations, media
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations, media
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations, media
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
DO $type_acl$
DECLARE
  type_entry record;
BEGIN
  FOR type_entry IN
    SELECT type_namespace.nspname, candidate_type.typname
      FROM pg_catalog.pg_type AS candidate_type
      JOIN pg_catalog.pg_namespace AS type_namespace
        ON type_namespace.oid = candidate_type.typnamespace
     WHERE type_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
       AND candidate_type.typisdefined
       AND candidate_type.typrelid = 0
       AND candidate_type.typelem = 0
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE USAGE ON TYPE %I.%I FROM home_agent_identity_finalizer, '
      'home_agent_identity_finalizer_kernel',
      type_entry.nspname,
      type_entry.typname
    );
  END LOOP;
END
$type_acl$;
REVOKE USAGE, CREATE ON SCHEMA public, ingest, identity, knowledge, engagement,
  privacy, operations, media
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations, media
  REVOKE ALL PRIVILEGES ON TABLES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations, media
  REVOKE ALL PRIVILEGES ON SEQUENCES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations, media
  REVOKE ALL PRIVILEGES ON FUNCTIONS
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations, media
  REVOKE ALL PRIVILEGES ON TYPES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
DO $identity_migration_runs_column_acl$
BEGIN
  IF pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_runs'
     ) IS NOT NULL THEN
    EXECUTE 'REVOKE UPDATE (expires_at) ON TABLE '
      'operations.reviewed_identity_migration_runs '
      'FROM home_agent_identity_migration, home_agent_identity_kernel, '
      'home_agent_identity_finalizer, home_agent_identity_finalizer_kernel';
  END IF;
END
$identity_migration_runs_column_acl$;
REVOKE SELECT (
  authorization_id, from_mode, to_mode, rule_version, policy_version,
  policy_digest, authorized_at
) ON TABLE operations.rollout_authorizations
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
-- Revision 0008 is manifest-only. Reapply its two callable capabilities and
-- non-callable erasure replay-guard trigger only when the exact three-function
-- owner-kernel contract exists; schema 0006/0007 therefore leaves both roles
-- inert. There is deliberately no finalization function, semantic projection,
-- or generic function grant.
--
-- Quarantine each exact signature in its own committed statement before
-- validating the set. A partial/tampered 0008 therefore loses PUBLIC and
-- online-role execution even though the following validation aborts replay.
DO $$
BEGIN
  IF to_regprocedure(
       'operations.reviewed_identity_migration_capabilities()'
     ) IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON FUNCTION '
      'operations.reviewed_identity_migration_capabilities() '
      'FROM PUBLIC, home_agent_api, home_agent_binding_operator, '
      'home_agent_ingest, home_agent_worker, home_agent_erasure, '
      'home_agent_rollout, home_agent_backup, home_agent_identity_kernel, '
      'home_agent_identity_migration, home_agent_identity_finalizer, '
      'home_agent_identity_finalizer_kernel';
  END IF;
  IF to_regprocedure(
       'operations.register_reviewed_identity_migration(jsonb)'
     ) IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON FUNCTION '
      'operations.register_reviewed_identity_migration(jsonb) '
      'FROM PUBLIC, home_agent_api, home_agent_binding_operator, '
      'home_agent_ingest, home_agent_worker, home_agent_erasure, '
      'home_agent_rollout, home_agent_backup, home_agent_identity_kernel, '
      'home_agent_identity_migration, home_agent_identity_finalizer, '
      'home_agent_identity_finalizer_kernel';
  END IF;
  IF to_regprocedure(
       'operations.bump_reviewed_identity_migration_replay_guard()'
     ) IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON FUNCTION '
      'operations.bump_reviewed_identity_migration_replay_guard() '
      'FROM PUBLIC, home_agent_api, home_agent_binding_operator, '
      'home_agent_ingest, home_agent_worker, home_agent_erasure, '
      'home_agent_rollout, home_agent_backup, home_agent_identity_kernel, '
      'home_agent_identity_migration, home_agent_identity_finalizer, '
      'home_agent_identity_finalizer_kernel';
  END IF;
END
$$;

DO $$
DECLARE
  reviewed_kernel_count integer;
  reviewed_capabilities regprocedure;
  reviewed_registration regprocedure;
  reviewed_replay_guard regprocedure;
  kernel_oid oid;
  caller_oid oid;
  database_oid oid;
  evidence_table regclass;
BEGIN
  reviewed_capabilities := to_regprocedure(
    'operations.reviewed_identity_migration_capabilities()'
  );
  reviewed_registration := to_regprocedure(
    'operations.register_reviewed_identity_migration(jsonb)'
  );
  reviewed_replay_guard := to_regprocedure(
    'operations.bump_reviewed_identity_migration_replay_guard()'
  );
  IF num_nonnulls(
       reviewed_capabilities,
       reviewed_registration,
       reviewed_replay_guard
     ) IN (1, 2) THEN
    RAISE EXCEPTION 'partial identity migration kernel function set'
      USING ERRCODE = '42501';
  END IF;
  IF reviewed_capabilities IS NOT NULL THEN
    SELECT count(*)
      INTO reviewed_kernel_count
      FROM pg_proc AS procedure
      JOIN pg_roles AS owner ON owner.oid = procedure.proowner
     WHERE procedure.oid IN (
       reviewed_capabilities,
       reviewed_registration,
       reviewed_replay_guard
     )
       AND owner.rolname = 'home_agent_identity_kernel'
       AND procedure.prosecdef
       AND procedure.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
       AND procedure.proacl IS NOT NULL
       AND encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(procedure.prosrc, 'UTF8')
             ),
             'hex'
           ) = CASE procedure.oid
             WHEN reviewed_capabilities THEN
               '1b26f6a57891eb6b35fc2c822ba4d92148c76c3f89eeff0b77b702225c6c1db2'
             WHEN reviewed_registration THEN
               '07a9d2d776de63360d8c473e674e7feb24c057b5cb308f56f57e2e7758eaf2a7'
             WHEN reviewed_replay_guard THEN
               '627bb84f83baa6183144de5d94ddcc4f0da56dc02e64c544b4b679b5ed3c316b'
           END
       AND NOT EXISTS (
         SELECT 1
           FROM aclexplode(procedure.proacl) AS function_acl
          WHERE function_acl.privilege_type = 'EXECUTE'
       );
    IF reviewed_kernel_count <> 3 THEN
      RAISE EXCEPTION 'identity migration kernel ownership contract mismatch'
        USING ERRCODE = '42501';
    END IF;
    SELECT oid INTO STRICT kernel_oid
      FROM pg_roles WHERE rolname = 'home_agent_identity_kernel';
    SELECT oid INTO STRICT caller_oid
      FROM pg_roles WHERE rolname = 'home_agent_identity_migration';
    SELECT oid INTO STRICT database_oid
      FROM pg_database WHERE datname = current_database();
    IF EXISTS (
         SELECT 1 FROM pg_shdepend AS caller_ownership
          WHERE caller_ownership.refobjid = caller_oid
            AND caller_ownership.deptype = 'o'
       )
       OR (
         SELECT count(*) FROM pg_shdepend AS kernel_ownership
          WHERE kernel_ownership.refobjid = kernel_oid
            AND kernel_ownership.deptype = 'o'
       ) <> 3
       OR EXISTS (
         SELECT 1 FROM pg_shdepend AS kernel_ownership
          WHERE kernel_ownership.refobjid = kernel_oid
            AND kernel_ownership.deptype = 'o'
            AND NOT (
              kernel_ownership.dbid = database_oid
              AND kernel_ownership.classid = 'pg_proc'::regclass
              AND kernel_ownership.objid IN (
                reviewed_capabilities,
                reviewed_registration,
                reviewed_replay_guard
              )
              AND kernel_ownership.objsubid = 0
            )
       ) THEN
      RAISE EXCEPTION 'identity migration kernel ownership dependency mismatch'
        USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
      SELECT 1
        FROM pg_trigger AS guard_trigger
       WHERE guard_trigger.tgrelid =
             'operations.reviewed_identity_migration_erasure_impacts'::regclass
         AND guard_trigger.tgname =
             'reviewed_identity_migration_erasure_replay_guard'
         AND guard_trigger.tgfoid = reviewed_replay_guard
         AND guard_trigger.tgenabled = 'O'
         AND guard_trigger.tgisinternal = false
         AND guard_trigger.tgtype = 7
         AND guard_trigger.tgnargs = 0
    ) THEN
      RAISE EXCEPTION 'identity migration replay guard trigger mismatch'
        USING ERRCODE = '42501';
    END IF;

    -- Reassert the exact FORCE-RLS boundary before restoring any kernel ACL.
    FOREACH evidence_table IN ARRAY ARRAY[
      'operations.reviewed_identity_migration_runs'::regclass,
      'operations.reviewed_identity_migration_source_items'::regclass,
      'operations.reviewed_identity_migration_decisions'::regclass,
      'operations.reviewed_identity_migration_item_receipts'::regclass,
      'operations.reviewed_identity_migration_finalizations'::regclass,
      'operations.legacy_identity_writer_evidence'::regclass,
      'operations.privacy_cutover_check_receipts'::regclass,
      'operations.semantic_authority_cutovers'::regclass,
      'operations.reviewed_identity_migration_erasure_impacts'::regclass
    ] LOOP
      EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', evidence_table);
      EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', evidence_table);
    END LOOP;

    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_runs_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_runs';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_runs_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_runs FOR SELECT '
      'TO home_agent_identity_kernel USING ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_runs_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_runs';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_runs_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_runs FOR INSERT '
      'TO home_agent_identity_kernel WITH CHECK ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_source_items_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_source_items';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_source_items_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_source_items FOR SELECT '
      'TO home_agent_identity_kernel USING ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_source_items_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_source_items';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_source_items_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_source_items FOR INSERT '
      'TO home_agent_identity_kernel WITH CHECK ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_decisions_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_decisions';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_decisions_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_decisions FOR SELECT '
      'TO home_agent_identity_kernel USING ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_decisions_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_decisions';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_decisions_manifest_kernel_insert ON '
      'operations.reviewed_identity_migration_decisions FOR INSERT '
      'TO home_agent_identity_kernel WITH CHECK ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'identity_migration_erasure_impacts_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_erasure_impacts';
    EXECUTE 'CREATE POLICY '
      'identity_migration_erasure_impacts_manifest_kernel_select ON '
      'operations.reviewed_identity_migration_erasure_impacts FOR SELECT '
      'TO home_agent_identity_kernel USING ('
      'session_user = ''home_agent_identity_migration'' AND '
      'current_user = ''home_agent_identity_kernel'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET''))';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_runs_replay_guard_trigger_select ON '
      'operations.reviewed_identity_migration_runs';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_runs_replay_guard_trigger_select ON '
      'operations.reviewed_identity_migration_runs FOR SELECT '
      'TO home_agent_identity_kernel USING ('
      'current_user = ''home_agent_identity_kernel'' AND '
      'session_user IN (''home_agent_owner'', ''home_agent_erasure'') AND '
      'pg_catalog.pg_trigger_depth() = 1)';
    EXECUTE 'DROP POLICY IF EXISTS '
      'reviewed_identity_migration_runs_replay_guard_update ON '
      'operations.reviewed_identity_migration_runs';
    EXECUTE 'CREATE POLICY '
      'reviewed_identity_migration_runs_replay_guard_update ON '
      'operations.reviewed_identity_migration_runs FOR UPDATE '
      'TO home_agent_identity_kernel USING ('
      'current_user = ''home_agent_identity_kernel'' AND ('
      '(session_user = ''home_agent_identity_migration'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET'')) OR '
      '(session_user IN (''home_agent_owner'', ''home_agent_erasure'') AND '
      'pg_catalog.pg_trigger_depth() = 1))) WITH CHECK ('
      'current_user = ''home_agent_identity_kernel'' AND ('
      '(session_user = ''home_agent_identity_migration'' AND NOT '
      'pg_catalog.pg_has_role(session_user, '
      '''home_agent_identity_kernel'', ''SET'')) OR '
      '(session_user IN (''home_agent_owner'', ''home_agent_erasure'') AND '
      'pg_catalog.pg_trigger_depth() = 1)))';

    EXECUTE 'REVOKE ALL ON FUNCTION '
      'operations.reviewed_identity_migration_capabilities(), '
      'operations.register_reviewed_identity_migration(jsonb) '
      'FROM PUBLIC, home_agent_api, home_agent_binding_operator, '
      'home_agent_ingest, home_agent_worker, home_agent_erasure, '
      'home_agent_rollout, home_agent_backup, home_agent_identity_kernel, '
      'home_agent_identity_migration, home_agent_identity_finalizer, '
      'home_agent_identity_finalizer_kernel';
    EXECUTE 'GRANT USAGE ON SCHEMA operations '
      'TO home_agent_identity_migration, home_agent_identity_kernel';
    EXECUTE 'GRANT SELECT, INSERT ON TABLE '
      'operations.reviewed_identity_migration_runs, '
      'operations.reviewed_identity_migration_source_items, '
      'operations.reviewed_identity_migration_decisions '
      'TO home_agent_identity_kernel';
    EXECUTE 'GRANT SELECT ('
      'authorization_id, from_mode, to_mode, rule_version, policy_version, '
      'policy_digest, authorized_at) '
      'ON TABLE operations.rollout_authorizations '
      'TO home_agent_identity_kernel';
    EXECUTE 'GRANT SELECT ON TABLE '
      'operations.reviewed_identity_migration_erasure_impacts '
      'TO home_agent_identity_kernel';
    EXECUTE 'GRANT UPDATE (expires_at) ON TABLE '
      'operations.reviewed_identity_migration_runs '
      'TO home_agent_identity_kernel';
    EXECUTE 'GRANT EXECUTE ON FUNCTION '
      'operations.reviewed_identity_migration_capabilities(), '
      'operations.register_reviewed_identity_migration(jsonb) '
      'TO home_agent_identity_migration';

    IF NOT has_function_privilege(
         caller_oid, reviewed_capabilities, 'EXECUTE'
       )
       OR NOT has_function_privilege(
         caller_oid, reviewed_registration, 'EXECUTE'
       )
       OR has_function_privilege(
         caller_oid, reviewed_replay_guard, 'EXECUTE'
       )
       OR has_table_privilege(
         'home_agent_identity_migration',
         'operations.reviewed_identity_migration_runs',
         'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
       )
       OR NOT has_table_privilege(
         'home_agent_identity_kernel',
         'operations.reviewed_identity_migration_erasure_impacts',
         'SELECT'
       )
       OR has_table_privilege(
         'home_agent_identity_kernel',
         'operations.reviewed_identity_migration_erasure_impacts',
         'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
       )
       OR NOT has_column_privilege(
         'home_agent_identity_kernel',
         'operations.reviewed_identity_migration_runs',
         'expires_at',
         'UPDATE'
       ) THEN
      RAISE EXCEPTION 'identity migration kernel ACL contract mismatch'
        USING ERRCODE = '42501';
    END IF;
  END IF;
END
$$;

-- When E1 exists, undo the schema-wide privacy grants above for its dormant
-- authority tables and for the request's whole-person commitment column.
-- Existing descriptor erasure keeps only its original typed columns.
DO $identity_erasure_e1_acl$
DECLARE
  column_list text;
  target_table text;
  target_role text;
  grantee_sql text;
BEGIN
  -- Remove direct semantic-authority mutation from every non-owner grantee,
  -- including independently granted column privileges.
  FOREACH target_table IN ARRAY ARRAY[
    'identity.principals','identity.ha_user_bindings',
    'identity.confirmation_artifacts'
  ]::text[]
  LOOP
    SELECT pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           )
      INTO STRICT column_list
      FROM pg_catalog.pg_attribute AS attribute
     WHERE attribute.attrelid = target_table::regclass
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname <> 'home_agent_owner'
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s',
        target_table, grantee_sql
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s',
        column_list, target_table, grantee_sql
      );
    END LOOP;
  END LOOP;

  SELECT pg_catalog.string_agg(
           pg_catalog.quote_ident(attribute.attname), ', '
           ORDER BY attribute.attnum
         )
    INTO STRICT column_list
    FROM pg_catalog.pg_attribute AS attribute
   WHERE attribute.attrelid = 'identity.ha_user_bindings'::regclass
     AND attribute.attnum > 0
     AND NOT attribute.attisdropped;
  FOR target_role IN
    SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname <> 'home_agent_owner'
    UNION ALL SELECT 'PUBLIC'
  LOOP
    grantee_sql := CASE WHEN target_role = 'PUBLIC'
      THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
    EXECUTE pg_catalog.format(
      'REVOKE SELECT ON TABLE identity.ha_user_bindings FROM %s', grantee_sql
    );
    EXECUTE pg_catalog.format(
      'REVOKE SELECT (%s) ON TABLE identity.ha_user_bindings FROM %s',
      column_list, grantee_sql
    );
  END LOOP;
  GRANT SELECT ON TABLE identity.principals TO home_agent_binding_operator;
  GRANT SELECT (principal_id) ON TABLE identity.principals
    TO home_agent_erasure;
  GRANT SELECT (
    binding_id, proposal_id, ha_user_id, principal_id, person_id,
    confirmed_by_principal_id, confirmed_at, revoked_at, source_artifact_id
  ) ON TABLE identity.ha_user_bindings
    TO home_agent_binding_operator, home_agent_ingest, home_agent_erasure;

  IF pg_catalog.to_regclass('privacy.person_erasure_scopes') IS NOT NULL THEN
    FOREACH target_table IN ARRAY ARRAY[
      'privacy.person_erasure_scopes',
      'privacy.subject_retrieval_blocks',
      'operations.reviewed_identity_migration_erasure_receipts'
    ]::text[]
    LOOP
      SELECT pg_catalog.string_agg(
               pg_catalog.quote_ident(attribute.attname), ', '
               ORDER BY attribute.attnum
             )
        INTO STRICT column_list
        FROM pg_catalog.pg_attribute AS attribute
       WHERE attribute.attrelid = target_table::regclass
         AND attribute.attnum > 0
         AND NOT attribute.attisdropped;
      FOR target_role IN
        SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
         WHERE role_row.rolname <> 'home_agent_owner'
        UNION ALL SELECT 'PUBLIC'
      LOOP
        grantee_sql := CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
        EXECUTE pg_catalog.format(
          'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s',
          target_table, grantee_sql
        );
        EXECUTE pg_catalog.format(
          'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
          'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s',
          column_list, target_table, grantee_sql
        );
      END LOOP;
    END LOOP;
    EXECUTE 'GRANT SELECT, INSERT ON TABLE '
      'privacy.person_erasure_scopes, '
      'privacy.subject_retrieval_blocks, '
      'operations.reviewed_identity_migration_erasure_receipts '
      'TO home_agent_owner';
  END IF;

  SELECT pg_catalog.string_agg(
           pg_catalog.quote_ident(attribute.attname), ', '
           ORDER BY attribute.attnum
         )
    INTO STRICT column_list
    FROM pg_catalog.pg_attribute AS attribute
   WHERE attribute.attrelid = 'privacy.erasure_requests'::regclass
     AND attribute.attnum > 0
     AND NOT attribute.attisdropped;
  FOR target_role IN
    SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname <> 'home_agent_owner'
    UNION ALL SELECT 'PUBLIC'
  LOOP
    grantee_sql := CASE WHEN target_role = 'PUBLIC'
      THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON TABLE privacy.erasure_requests FROM %s',
      grantee_sql
    );
    EXECUTE pg_catalog.format(
      'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
      'REFERENCES (%1$s) ON TABLE privacy.erasure_requests FROM %2$s',
      column_list, grantee_sql
    );
  END LOOP;
  EXECUTE 'GRANT SELECT ('
    'erasure_request_id, principal_id, scope, state, policy_digest, '
    'created_at, completed_at) ON TABLE privacy.erasure_requests '
    'TO home_agent_api, home_agent_worker, home_agent_erasure';
  EXECUTE 'GRANT INSERT ('
    'erasure_request_id, principal_id, scope, state, policy_digest) '
    'ON TABLE privacy.erasure_requests TO home_agent_api';
  EXECUTE 'GRANT INSERT ('
    'erasure_request_id, principal_id, scope, state, policy_digest, '
    'completed_at) ON TABLE privacy.erasure_requests '
    'TO home_agent_erasure';
  EXECUTE 'GRANT UPDATE (state, completed_at) '
    'ON TABLE privacy.erasure_requests '
    'TO home_agent_api, home_agent_worker, home_agent_erasure';

  -- Keep compatibility grants only on objects that already exist. New
  -- identity/privacy/operations tables must fail closed until an explicit
  -- post-migration grant is reviewed. Scrub arbitrary and PUBLIC owner
  -- defaults, including PostgreSQL 17 MAINTAIN, from every grantee.
  FOREACH target_table IN ARRAY ARRAY[
    'identity','privacy','operations'
  ]::text[]
  LOOP
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname <> 'home_agent_owner'
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA %I '
        'REVOKE ALL PRIVILEGES ON TABLES FROM %s',
        target_table, grantee_sql
      );
    END LOOP;
  END LOOP;
END
$identity_erasure_e1_acl$;

-- E1 reserves a NOLOGIN owner for a later erasure SECURITY DEFINER kernel.
-- Grant replay must restore its global quarantine even after the one-time
-- migration has run. E2, when its complete function/table set exists, starts
-- from this empty boundary and restores only its exact kernel/caller ACLs in
-- the conditional block below.
DO $identity_erasure_kernel_quarantine$
DECLARE
  type_entry record;
  table_entry record;
  kernel_oid oid;
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
     WHERE rolname = 'home_agent_identity_erasure_kernel'
  ) THEN
    SELECT oid INTO STRICT kernel_oid
      FROM pg_catalog.pg_roles
     WHERE rolname = 'home_agent_identity_erasure_kernel';
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON DATABASE %I '
      'FROM home_agent_identity_erasure_kernel',
      pg_catalog.current_database()
    );
    REVOKE USAGE, CREATE ON SCHEMA public, ingest, identity, knowledge,
      engagement, privacy, operations, media
      FROM home_agent_identity_erasure_kernel;
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
      knowledge, engagement, privacy, operations, media
      FROM home_agent_identity_erasure_kernel;
    FOR table_entry IN
      SELECT table_namespace.nspname,
             candidate_table.relname,
             pg_catalog.string_agg(
               pg_catalog.quote_ident(attribute.attname), ', '
               ORDER BY attribute.attnum
             ) AS column_list
        FROM pg_catalog.pg_class AS candidate_table
        JOIN pg_catalog.pg_namespace AS table_namespace
          ON table_namespace.oid = candidate_table.relnamespace
        JOIN pg_catalog.pg_attribute AS attribute
          ON attribute.attrelid = candidate_table.oid
         AND attribute.attnum > 0
         AND NOT attribute.attisdropped
       WHERE table_namespace.nspname IN (
         'public','ingest','identity','knowledge','engagement','privacy',
         'operations','media'
       )
         AND candidate_table.relkind IN ('r','p','v','m','f')
       GROUP BY table_namespace.nspname, candidate_table.relname
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$I.%3$I '
        'FROM home_agent_identity_erasure_kernel',
        table_entry.column_list, table_entry.nspname, table_entry.relname
      );
    END LOOP;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, ingest, identity,
      knowledge, engagement, privacy, operations, media
      FROM home_agent_identity_erasure_kernel;
    REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public, ingest, identity,
      knowledge, engagement, privacy, operations, media
      FROM home_agent_identity_erasure_kernel;

    FOR type_entry IN
      SELECT type_namespace.nspname, candidate_type.typname
        FROM pg_catalog.pg_type AS candidate_type
        JOIN pg_catalog.pg_namespace AS type_namespace
          ON type_namespace.oid = candidate_type.typnamespace
       WHERE type_namespace.nspname IN (
         'public','ingest','identity','knowledge','engagement','privacy',
         'operations','media'
       )
         AND candidate_type.typisdefined
         AND candidate_type.typrelid = 0
         AND candidate_type.typelem = 0
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE USAGE ON TYPE %I.%I '
        'FROM home_agent_identity_erasure_kernel',
        type_entry.nspname,
        type_entry.typname
      );
    END LOOP;

    ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public,
      ingest, identity, knowledge, engagement, privacy, operations, media
      REVOKE ALL PRIVILEGES ON TABLES
      FROM home_agent_identity_erasure_kernel;
    ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public,
      ingest, identity, knowledge, engagement, privacy, operations, media
      REVOKE ALL PRIVILEGES ON SEQUENCES
      FROM home_agent_identity_erasure_kernel;
    ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public,
      ingest, identity, knowledge, engagement, privacy, operations, media
      REVOKE ALL PRIVILEGES ON FUNCTIONS
      FROM home_agent_identity_erasure_kernel;
    ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public,
      ingest, identity, knowledge, engagement, privacy, operations, media
      REVOKE ALL PRIVILEGES ON TYPES
      FROM home_agent_identity_erasure_kernel;

    IF NOT EXISTS (
      SELECT 1
        FROM pg_catalog.pg_roles AS kernel_role
       WHERE kernel_role.oid = kernel_oid
         AND NOT kernel_role.rolcanlogin
         AND NOT kernel_role.rolinherit
         AND NOT kernel_role.rolsuper
         AND NOT kernel_role.rolcreatedb
         AND NOT kernel_role.rolcreaterole
         AND NOT kernel_role.rolreplication
         AND NOT kernel_role.rolbypassrls
         AND kernel_role.rolconnlimit = 0
         AND kernel_role.rolconfig IS NULL
    ) OR EXISTS (
      SELECT 1
        FROM pg_catalog.pg_shdepend AS owned_object
       WHERE owned_object.refobjid = kernel_oid
         AND owned_object.deptype = 'o'
         AND NOT (
           owned_object.classid = 'pg_catalog.pg_proc'::regclass
           AND coalesce(
             owned_object.objid = ANY (ARRAY[
               pg_catalog.to_regprocedure(
                 'privacy.identity_person_is_blocked(uuid)'
               )::oid,
               pg_catalog.to_regprocedure(
                 'privacy.identity_principal_is_blocked(uuid)'
               )::oid,
               pg_catalog.to_regprocedure(
                 'privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)'
               )::oid,
               pg_catalog.to_regprocedure(
                 'privacy.identity_fact_version_is_visible(uuid)'
               )::oid,
               pg_catalog.to_regprocedure(
                 'privacy.reject_tombstoned_identity_write()'
               )::oid,
               pg_catalog.to_regprocedure(
                 'privacy.enforce_identity_person_erasure_residual_set()'
               )::oid
             ]::oid[]),
             false
           )
         )
    ) OR EXISTS (
      SELECT 1
        FROM pg_catalog.pg_auth_members AS membership
        JOIN pg_catalog.pg_roles AS member
          ON member.oid = membership.member
       WHERE membership.roleid = kernel_oid
         AND (
           member.rolname <> 'home_agent_owner'
           OR membership.admin_option
           OR membership.inherit_option
           OR NOT membership.set_option
         )
    ) OR (
      SELECT pg_catalog.count(*)
        FROM pg_catalog.pg_auth_members
       WHERE roleid = kernel_oid
    ) <> 1 THEN
      RAISE EXCEPTION 'identity erasure kernel ownership/membership invalid'
        USING ERRCODE = '42501';
    END IF;
  END IF;
END
$identity_erasure_kernel_quarantine$;

-- E2 is an all-or-none activation of the otherwise quarantined NOLOGIN
-- kernel. The two durable control tables remain inaccessible to every online
-- role. Runtime roles receive only deterministic suppression predicates; the
-- initiative readers additionally receive one principal-scoped boolean
-- helper without fact-table access. The restore login receives only the v2
-- replay entry point.
DO $identity_erasure_e2_acl$
DECLARE
  suppression_functions text[] := ARRAY[
    'privacy.identity_person_is_blocked(uuid)',
    'privacy.identity_principal_is_blocked(uuid)',
    'privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)'
  ]::text[];
  initiative_fact_visibility_function text :=
    'privacy.identity_fact_version_is_visible(uuid)';
  initiative_fact_visibility_body_sha256 constant text :=
    '83bcbf75f10489a4a7517c33a2dbc16bdb7aefc2b1f237ae1720b71a38f0fa82';
  initiative_fact_visibility_roles text[] := ARRAY[
    'home_agent_api', 'home_agent_ingest', 'home_agent_erasure'
  ]::text[];
  all_suppression_functions text[];
  trigger_functions text[] := ARRAY[
    'privacy.reject_tombstoned_identity_write()',
    'privacy.enforce_identity_person_erasure_residual_set()'
  ]::text[];
  replay_function text :=
    'privacy.replay_identity_person_retrieval_block_v2(jsonb)';
  all_functions text[];
  column_list text;
  function_count integer;
  function_oid regprocedure;
  grantee_sql text;
  kernel_oid oid;
  owner_oid oid;
  target_role text;
  target_signature text;
  target_table text;
BEGIN
  all_suppression_functions := suppression_functions
    || ARRAY[initiative_fact_visibility_function]::text[];
  all_functions := all_suppression_functions || trigger_functions
    || ARRAY[replay_function]::text[];
  SELECT pg_catalog.count(*)
    INTO STRICT function_count
    FROM pg_catalog.unnest(all_functions) AS signature(signature_text)
   WHERE pg_catalog.to_regprocedure(signature.signature_text) IS NOT NULL;

  IF function_count = 0
     AND pg_catalog.to_regclass(
       'privacy.identity_person_erasure_residuals'
     ) IS NULL THEN
    RETURN;
  END IF;
  IF function_count <> pg_catalog.cardinality(all_functions)
     OR pg_catalog.to_regclass('privacy.subject_retrieval_blocks') IS NULL
     OR pg_catalog.to_regclass(
       'privacy.identity_person_erasure_residuals'
     ) IS NULL THEN
    RAISE EXCEPTION 'partial identity erasure E2 object set'
      USING ERRCODE = '55000';
  END IF;

  SELECT oid INTO STRICT kernel_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_erasure_kernel';
  SELECT oid INTO STRICT owner_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_owner';
  IF EXISTS (
    SELECT 1
      FROM pg_catalog.unnest(all_suppression_functions)
           AS signature(signature_text)
      JOIN pg_catalog.pg_proc AS function_row
        ON function_row.oid = pg_catalog.to_regprocedure(
          signature.signature_text
        )
     WHERE function_row.proowner <> kernel_oid
       OR NOT function_row.prosecdef
       OR function_row.prokind <> 'f'
  ) OR NOT EXISTS (
    SELECT 1
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = pg_catalog.to_regprocedure(
             initiative_fact_visibility_function
           )
       AND function_row.proowner = kernel_oid
       AND function_row.prolang = (
         SELECT language_row.oid
           FROM pg_catalog.pg_language AS language_row
          WHERE language_row.lanname = 'sql'
       )
       AND function_row.prorettype = 'boolean'::regtype
       AND NOT function_row.proretset
       AND function_row.prokind = 'f'
       AND function_row.prosecdef
       AND function_row.provolatile = 's'
       AND NOT function_row.proisstrict
       AND NOT function_row.proleakproof
       AND function_row.proparallel = 'u'
       AND function_row.proconfig = ARRAY[
             'search_path=pg_catalog', 'row_security=on'
           ]::text[]
       AND pg_catalog.encode(
             pg_catalog.sha256(
               pg_catalog.convert_to(function_row.prosrc, 'UTF8')
             ),
             'hex'
           ) = initiative_fact_visibility_body_sha256
  ) OR EXISTS (
    SELECT 1
      FROM pg_catalog.unnest(trigger_functions) AS signature(signature_text)
      JOIN pg_catalog.pg_proc AS function_row
        ON function_row.oid = pg_catalog.to_regprocedure(
          signature.signature_text
        )
     WHERE function_row.proowner <> kernel_oid
        OR function_row.prokind <> 'f'
        OR function_row.prosecdef IS DISTINCT FROM (
          signature.signature_text =
            'privacy.reject_tombstoned_identity_write()'
        )
  ) OR EXISTS (
    SELECT 1
      FROM pg_catalog.pg_proc AS replay_row
     WHERE replay_row.oid = pg_catalog.to_regprocedure(replay_function)
       AND (
         replay_row.proowner <> owner_oid
         OR NOT replay_row.prosecdef
         OR replay_row.prokind <> 'f'
       )
  ) THEN
    RAISE EXCEPTION 'identity erasure E2 function ownership invalid'
      USING ERRCODE = '42501';
  END IF;

  FOREACH target_table IN ARRAY ARRAY[
    'privacy.subject_retrieval_blocks',
    'privacy.identity_person_erasure_residuals'
  ]::text[]
  LOOP
    SELECT pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           )
      INTO STRICT column_list
      FROM pg_catalog.pg_attribute AS attribute
     WHERE attribute.attrelid = target_table::regclass
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname <> 'home_agent_owner'
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s',
        target_table, grantee_sql
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s',
        column_list, target_table, grantee_sql
      );
    END LOOP;
    EXECUTE pg_catalog.format(
      'GRANT SELECT, INSERT ON TABLE %s TO home_agent_owner',
      target_table
    );
  END LOOP;

  GRANT USAGE ON SCHEMA identity, knowledge, privacy
    TO home_agent_identity_erasure_kernel;
  GRANT SELECT (principal_id, person_id) ON TABLE identity.principals
    TO home_agent_identity_erasure_kernel;
  GRANT SELECT (person_id) ON TABLE privacy.subject_retrieval_blocks
    TO home_agent_identity_erasure_kernel;
  GRANT SELECT (
    fact_version_id, subject_type, subject_id, predicate, object,
    perspective_principal_id
  ) ON TABLE knowledge.fact_versions
    TO home_agent_identity_erasure_kernel;

  FOREACH target_signature IN ARRAY all_functions
  LOOP
    function_oid := pg_catalog.to_regprocedure(target_signature);
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s',
        function_oid, grantee_sql
      );
    END LOOP;
  END LOOP;

  GRANT USAGE ON SCHEMA privacy TO home_agent_api,
    home_agent_binding_operator, home_agent_ingest, home_agent_worker,
    home_agent_erasure, home_agent_rollout, home_agent_backup;
  FOREACH target_signature IN ARRAY suppression_functions
  LOOP
    function_oid := pg_catalog.to_regprocedure(target_signature);
    EXECUTE pg_catalog.format(
      'GRANT EXECUTE ON FUNCTION %s TO home_agent_api, '
      'home_agent_binding_operator, home_agent_ingest, home_agent_worker, '
      'home_agent_erasure, home_agent_rollout, home_agent_backup',
      function_oid
    );
    EXECUTE pg_catalog.format(
      'GRANT EXECUTE ON FUNCTION %s '
      'TO home_agent_identity_erasure_kernel',
      function_oid
    );
  END LOOP;
  function_oid := pg_catalog.to_regprocedure(
    initiative_fact_visibility_function
  );
  FOREACH target_role IN ARRAY initiative_fact_visibility_roles
  LOOP
    EXECUTE pg_catalog.format(
      'GRANT EXECUTE ON FUNCTION %s TO %I',
      function_oid, target_role
    );
  END LOOP;
  function_oid := pg_catalog.to_regprocedure(replay_function);
  EXECUTE pg_catalog.format(
    'GRANT EXECUTE ON FUNCTION %s TO home_agent_erasure',
    function_oid
  );
END
$identity_erasure_e2_acl$;

-- E3 is dormant database-only authority. Grant replay must first remove every
-- table/column/function capability held by either finalizer role. This
-- quarantine is committed before the conditional admission below, so a
-- partial or tampered 0013 object set remains inaccessible when validation
-- fails. Revision 0006a has none of the E3 objects and continues through the
-- explicit zero-object path.
DO $identity_finalizer_e3_quarantine$
DECLARE
  column_list text;
  function_oid regprocedure;
  grantee_sql text;
  target_role text;
  target_signature text;
  target_table record;
  type_entry record;
BEGIN
  FOR target_table IN
    SELECT table_namespace.nspname,
           candidate_table.relname,
           pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           ) AS column_list
      FROM pg_catalog.pg_class AS candidate_table
      JOIN pg_catalog.pg_namespace AS table_namespace
        ON table_namespace.oid = candidate_table.relnamespace
      JOIN pg_catalog.pg_attribute AS attribute
        ON attribute.attrelid = candidate_table.oid
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped
     WHERE table_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
       AND candidate_table.relkind IN ('r','p','v','m','f')
     GROUP BY table_namespace.nspname, candidate_table.relname
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM '
      'home_agent_identity_finalizer, home_agent_identity_finalizer_kernel',
      target_table.nspname, target_table.relname
    );
    EXECUTE pg_catalog.format(
      'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
      'REFERENCES (%1$s) ON TABLE %2$I.%3$I FROM '
      'home_agent_identity_finalizer, home_agent_identity_finalizer_kernel',
      target_table.column_list, target_table.nspname, target_table.relname
    );
  END LOOP;

  REVOKE USAGE, CREATE ON SCHEMA public, ingest, identity, knowledge,
    engagement, privacy, operations, media
    FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
  REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, ingest, identity,
    knowledge, engagement, privacy, operations, media
    FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
  REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public, ingest, identity,
    knowledge, engagement, privacy, operations, media
    FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
  FOR type_entry IN
    SELECT type_namespace.nspname, candidate_type.typname
      FROM pg_catalog.pg_type AS candidate_type
      JOIN pg_catalog.pg_namespace AS type_namespace
        ON type_namespace.oid = candidate_type.typnamespace
     WHERE type_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
       AND candidate_type.typisdefined
       AND candidate_type.typrelid = 0
       AND candidate_type.typelem = 0
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE USAGE ON TYPE %I.%I FROM '
      'home_agent_identity_finalizer, home_agent_identity_finalizer_kernel',
      type_entry.nspname,
      type_entry.typname
    );
  END LOOP;

  -- Dedicated E3 control objects may never retain a grant to an unreviewed
  -- role. Quarantine each object independently before checking completeness.
  FOREACH target_signature IN ARRAY ARRAY[
    'operations.finalize_reviewed_identity_migration(bytea,uuid)',
    'privacy.lock_identity_semantic_write_fence()',
    'privacy.fence_identity_tombstone_write()'
  ]::text[]
  LOOP
    function_oid := pg_catalog.to_regprocedure(target_signature);
    IF function_oid IS NULL THEN
      CONTINUE;
    END IF;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s',
        function_oid, grantee_sql
      );
    END LOOP;
  END LOOP;

  FOREACH column_list IN ARRAY ARRAY[
    'operations.reviewed_identity_finalizer_admissions',
    'privacy.identity_semantic_write_fence'
  ]::text[]
  LOOP
    IF pg_catalog.to_regclass(column_list) IS NULL THEN
      CONTINUE;
    END IF;
    SELECT pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           )
      INTO STRICT grantee_sql
      FROM pg_catalog.pg_attribute AS attribute
     WHERE attribute.attrelid = column_list::regclass
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname <> 'home_agent_owner'
      UNION ALL SELECT 'PUBLIC'
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s',
        column_list,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s',
        grantee_sql,
        column_list,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
    END LOOP;
  END LOOP;
END
$identity_finalizer_e3_quarantine$;

-- Revision 0001 predates the per-function PUBLIC-EXECUTE convention used by
-- every later migration. Repair that one bootstrap routine explicitly after
-- the E3 quarantine commits. If this legacy object is missing or tampered,
-- replay fails closed without rolling the quarantine back.
REVOKE ALL ON FUNCTION ingest.reject_artifact_link_cycle() FROM PUBLIC;

-- Owner-created functions must not regain PostgreSQL's implicit PUBLIC
-- EXECUTE grant. Do not persist a pg_default_acl object owned by the dormant
-- E3 kernel: revisions 0009 and 0013 require that role to own nothing before
-- the reviewed function is created. Restore the built-in kernel default here
-- to remove any stale row left by an earlier grant script. The kernel is
-- NOLOGIN and has no CREATE privilege; revision 0013 creates its one function
-- and revokes PUBLIC in the same transactional migration.
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_identity_finalizer_kernel
  GRANT EXECUTE ON FUNCTIONS TO PUBLIC;

-- Restore E3 only when every object, role, ownership dependency, fence, RLS
-- policy, and narrow ACL is present. The login remains expired: this block
-- makes the reviewed database kernel internally complete, not live.
DO $identity_finalizer_e3_acl$
DECLARE
  admission_table regclass :=
    pg_catalog.to_regclass(
      'operations.reviewed_identity_finalizer_admissions'
    );
  fence_table regclass :=
    pg_catalog.to_regclass('privacy.identity_semantic_write_fence');
  finalizer_function regprocedure :=
    pg_catalog.to_regprocedure(
      'operations.finalize_reviewed_identity_migration(bytea,uuid)'
    );
  fence_function regprocedure :=
    pg_catalog.to_regprocedure(
      'privacy.lock_identity_semantic_write_fence()'
    );
  fence_trigger_function regprocedure :=
    pg_catalog.to_regprocedure(
      'privacy.fence_identity_tombstone_write()'
    );
  person_blocked_function regprocedure :=
    pg_catalog.to_regprocedure(
      'privacy.identity_person_is_blocked(uuid)'
    );
  finalizer_oid oid;
  kernel_oid oid;
  cutover_kernel_oid oid;
  owner_oid oid;
  erasure_kernel_oid oid;
  database_oid oid;
  current_revision text;
  primary_object_count integer;
  bootstrap_columns text[];
  bootstrap_constraints text[];
  bootstrap_indexes text[];
  bootstrap_trigger_count integer;
  bootstrap_insert_ri_trigger_count integer;
  bootstrap_update_ri_trigger_count integer;
  evidence_table regclass;
  evidence_tables constant text[] := ARRAY[
    'operations.reviewed_identity_migration_runs',
    'operations.reviewed_identity_migration_source_items',
    'operations.reviewed_identity_migration_decisions',
    'operations.reviewed_identity_migration_item_receipts',
    'operations.reviewed_identity_migration_finalizations',
    'operations.reviewed_identity_migration_projection_lineage',
    'operations.reviewed_identity_migration_projection_subjects',
    'operations.reviewed_identity_migration_erasure_impacts',
    'operations.legacy_identity_writer_evidence',
    'operations.privacy_cutover_check_receipts',
    'operations.semantic_authority_cutovers'
  ]::text[];
  e4_e3_policy_relations constant text[] := ARRAY[
    'operations.reviewed_identity_migration_runs',
    'operations.reviewed_identity_migration_finalizations',
    'operations.reviewed_identity_finalizer_admissions',
    'operations.semantic_authority_cutovers',
    'operations.legacy_identity_writer_evidence',
    'operations.privacy_cutover_check_receipts',
    'operations.reviewed_identity_migration_erasure_impacts',
    'operations.reviewed_identity_migration_projection_lineage',
    'operations.reviewed_identity_migration_projection_subjects'
  ]::text[];
  pre_e3_revisions constant text[] := ARRAY[
    '0001_greenfield_core',
    '0002_people_privacy_cutover',
    '0003_resource_budgets',
    '0004_rollout_authorizations',
    '0005_principal_binding_proposals',
    '0006_worker_maintenance_health',
    '0006a_worker_lease_arbitration',
    '0007_phase3_identity_authority',
    '0008_identity_migration_kernel',
    '0009_identity_finalizer_base',
    '0010_identity_erasure_source',
    '0011_identity_erasure_e1',
    '0012_identity_erasure_e2'
  ]::text[];
  -- E4 is the only reviewed descendant whose migration preserves the exact
  -- E3 relations, functions, policies, ownership, and ACL contract validated
  -- below. Every later revision must be admitted explicitly after review.
  reviewed_e3_catalog_revisions constant text[] := ARRAY[
    '0013_identity_finalizer_e3',
    '0014_identity_cutover_e4'
  ]::text[];
  expected_e3_catalog_sha256 constant text :=
    '123326a4620d3dd123773819d95255e40813a5a949f406570252ff1f7031f29a';
  expected_finalizer_body_sha256 constant text :=
    '5578c4f0cbbaf0eb6ca69c427d113f94a24a752f67f4ba164bafde209f2594fe';
  expected_fence_body_sha256 constant text :=
    'fd6919a406140ac5bc82d1d1dededa1397c5e27dc54916fb572ce7bc33ba22a5';
  expected_fence_trigger_body_sha256 constant text :=
    'f78a0f25ca2ea86f286c4bf288a1bcb8889213b0e272d8784852da67b110e69f';
  expected_person_blocked_body_sha256 constant text :=
    '36032e050f94dd376dfffd59a25b54f5d0afa197ac13922156be46df753889ea';
  actual_e3_catalog_sha256 text;
  expected_schema_acl text[];
  actual_schema_acl text[];
  expected_table_acl text[];
  actual_table_acl text[];
  expected_column_acl text[];
  actual_column_acl text[];
  expected_function_acl text[];
  actual_function_acl text[];
  actual_effective_acl text[];
  expected_effective_acl text[];
  target_table text;
  evidence_select_policy constant text := 'identity_finalizer_e3_select';
  evidence_insert_policy constant text := 'identity_finalizer_e3_insert';
  e4_select_policy constant text := 'identity_cutover_e4_select';
  migration_run_lock_policy constant text :=
    'identity_finalizer_e3_migration_run_lock';
  predicate constant text :=
    'session_user = ''home_agent_identity_finalizer'' AND '
    'current_user = ''home_agent_identity_finalizer_kernel'' AND NOT '
    'pg_catalog.pg_has_role(session_user, '
    '''home_agent_identity_finalizer_kernel'', ''SET'')';
BEGIN
  SELECT version_num
    INTO STRICT current_revision
    FROM public.alembic_version;
  SELECT oid INTO STRICT owner_oid
    FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_owner';

  primary_object_count := pg_catalog.num_nonnulls(
    admission_table,
    fence_table,
    finalizer_function,
    fence_function,
    fence_trigger_function
  );
  IF primary_object_count = 0 THEN
    IF current_revision = ANY (pre_e3_revisions) THEN
      RETURN;
    END IF;
    RAISE EXCEPTION 'identity finalizer E3 object set absent at unknown revision'
      USING ERRCODE = '55000';
  END IF;

  -- metadata.create_all() at a fresh pre-0013 revision contains the future
  -- admission relation because revision 0001 deliberately uses the current
  -- SQLAlchemy metadata. Accept only that exact empty, owner-only, inert
  -- bootstrap. Any other one-object state is a partial E3 deployment.
  IF primary_object_count = 1
     AND admission_table IS NOT NULL
     AND current_revision = ANY (pre_e3_revisions) THEN
    SELECT pg_catalog.array_agg(
             attribute.attname || '|' ||
             pg_catalog.format_type(
               attribute.atttypid, attribute.atttypmod
             ) || '|' ||
             attribute.attnotnull::text || '|' ||
             coalesce(
               pg_catalog.pg_get_expr(
                 default_row.adbin, default_row.adrelid, true
               ),
               ''
             )
             ORDER BY attribute.attnum
           )
      INTO STRICT bootstrap_columns
      FROM pg_catalog.pg_attribute AS attribute
      LEFT JOIN pg_catalog.pg_attrdef AS default_row
        ON default_row.adrelid = attribute.attrelid
       AND default_row.adnum = attribute.attnum
     WHERE attribute.attrelid = admission_table
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    SELECT pg_catalog.array_agg(
             constraint_row.conname || '|' || constraint_row.contype::text
             ORDER BY constraint_row.conname
           )
      INTO STRICT bootstrap_constraints
      FROM pg_catalog.pg_constraint AS constraint_row
     WHERE constraint_row.conrelid = admission_table;
    SELECT pg_catalog.array_agg(
             index_row.relname || '|' ||
             index_state.indisprimary::text || '|' ||
             index_state.indisunique::text
             ORDER BY index_row.relname
           )
      INTO STRICT bootstrap_indexes
      FROM pg_catalog.pg_index AS index_state
      JOIN pg_catalog.pg_class AS index_row
        ON index_row.oid = index_state.indexrelid
     WHERE index_state.indrelid = admission_table;
    SELECT pg_catalog.count(*),
           pg_catalog.count(*) FILTER (
             WHERE trigger_row.tgisinternal
               AND trigger_row.tgenabled::text = 'O'
               AND trigger_row.tgparentid = 0
               AND NOT trigger_row.tgdeferrable
               AND NOT trigger_row.tginitdeferred
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND constraint_row.conrelid = admission_table
               AND constraint_row.conname =
                     'fk_identity_finalizer_admission_run'
               AND constraint_row.contype::text = 'f'
               AND trigger_row.tgconstrrelid = constraint_row.confrelid
               AND trigger_row.tgconstrindid = constraint_row.conindid
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                     'pg_catalog."RI_FKey_check_ins"()'
                   )
               AND trigger_row.tgtype = 5
           ),
           pg_catalog.count(*) FILTER (
             WHERE trigger_row.tgisinternal
               AND trigger_row.tgenabled::text = 'O'
               AND trigger_row.tgparentid = 0
               AND NOT trigger_row.tgdeferrable
               AND NOT trigger_row.tginitdeferred
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgoldtable IS NULL
               AND trigger_row.tgnewtable IS NULL
               AND constraint_row.conrelid = admission_table
               AND constraint_row.conname =
                     'fk_identity_finalizer_admission_run'
               AND constraint_row.contype::text = 'f'
               AND trigger_row.tgconstrrelid = constraint_row.confrelid
               AND trigger_row.tgconstrindid = constraint_row.conindid
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                     'pg_catalog."RI_FKey_check_upd"()'
                   )
               AND trigger_row.tgtype = 17
           )
      INTO STRICT bootstrap_trigger_count,
                  bootstrap_insert_ri_trigger_count,
                  bootstrap_update_ri_trigger_count
      FROM pg_catalog.pg_trigger AS trigger_row
      LEFT JOIN pg_catalog.pg_constraint AS constraint_row
        ON constraint_row.oid = trigger_row.tgconstraint
     WHERE trigger_row.tgrelid = admission_table;

    IF bootstrap_columns IS DISTINCT FROM ARRAY[
         'admission_id|uuid|true|',
         'run_id|uuid|true|',
         'finalization_id|uuid|true|',
         'contract_version|character varying(64)|true|',
         'verification_status|character varying(32)|true|',
         'document_sha256|character varying(64)|true|',
         'document_octets|integer|true|',
         'finalization_commitment|character varying(64)|true|',
         'decision_manifest_commitment|character varying(64)|true|',
         'receipt_set_commitment|character varying(64)|true|',
         'lineage_set_commitment|character varying(64)|true|',
         'privacy_closure_set_commitment|character varying(64)|true|',
         'auto_expiry_effect_set_commitment|character varying(64)|true|',
         'review_receipt_commitment|character varying(64)|true|',
         'release_manifest_digest|character varying(64)|true|',
         'migration_tool_bundle_digest|character varying(64)|true|',
         'core_oci_manifest_digest|character varying(64)|true|',
         'core_schema_digest|character varying(64)|true|',
         'core_capability_digest|character varying(64)|true|',
         'policy_digest|character varying(64)|true|',
         'review_signing_key_fingerprint|character varying(64)|true|',
         'finalization_signing_key_fingerprint|character varying(64)|true|',
         'verifier_bundle_digest|character varying(64)|true|',
         'admitted_at|timestamp with time zone|true|transaction_timestamp()',
         'expires_at|timestamp with time zone|true|',
         'consumed_at|timestamp with time zone|false|'
       ]::text[]
       OR bootstrap_constraints IS DISTINCT FROM ARRAY[
         'ck_reviewed_identity_finalizer_admissions_digest_shape|c',
         'ck_reviewed_identity_finalizer_admissions_document_size|c',
         'ck_reviewed_identity_finalizer_admissions_fixed_contract|c',
         'ck_reviewed_identity_finalizer_admissions_one_time_window|c',
         'ck_reviewed_identity_finalizer_admissions_uuidv7_ids|c',
         'fk_identity_finalizer_admission_run|f',
         'pk_reviewed_identity_finalizer_admissions|p',
         'uq_reviewed_identity_finalizer_admissions_document_sha256|u',
         'uq_reviewed_identity_finalizer_admissions_finalization__1d9a|u',
         'uq_reviewed_identity_finalizer_admissions_finalization_id|u',
         'uq_reviewed_identity_finalizer_admissions_run_id|u'
       ]::text[]
       OR bootstrap_indexes IS DISTINCT FROM ARRAY[
         'pk_reviewed_identity_finalizer_admissions|true|true',
         'uq_reviewed_identity_finalizer_admissions_document_sha256|false|true',
         'uq_reviewed_identity_finalizer_admissions_finalization__1d9a|false|true',
         'uq_reviewed_identity_finalizer_admissions_finalization_id|false|true',
         'uq_reviewed_identity_finalizer_admissions_run_id|false|true'
       ]::text[]
       OR bootstrap_trigger_count <> 2
       OR bootstrap_insert_ri_trigger_count <> 1
       OR bootstrap_update_ri_trigger_count <> 1
       OR NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_class AS table_row
          WHERE table_row.oid = admission_table
            AND table_row.relowner = owner_oid
            AND table_row.relkind = 'r'
            AND table_row.relpersistence = 'p'
            AND NOT table_row.relrowsecurity
            AND NOT table_row.relforcerowsecurity
            AND table_row.relhastriggers
       )
       OR EXISTS (
         SELECT 1
           FROM operations.reviewed_identity_finalizer_admissions
       )
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.pg_policy
          WHERE polrelid = admission_table
       )
       OR pg_catalog.obj_description(
            admission_table, 'pg_class'
          ) IS NOT NULL
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.pg_attribute AS attribute
          WHERE attribute.attrelid = admission_table
            AND attribute.attnum > 0
            AND NOT attribute.attisdropped
            AND attribute.attacl IS NOT NULL
       )
        OR EXISTS (
          SELECT 1
            FROM pg_catalog.aclexplode(
             coalesce(
               (SELECT relacl FROM pg_catalog.pg_class
                 WHERE oid = admission_table),
               pg_catalog.acldefault('r', owner_oid)
             )
           ) AS table_acl
           WHERE table_acl.grantee <> owner_oid
           ) THEN
      RAISE EXCEPTION 'identity finalizer E3 inert bootstrap contract mismatch'
        USING ERRCODE = '55000',
              DETAIL = pg_catalog.format(
                'columns=%s constraints=%s indexes=%s '
                'ri_triggers=%s/%s/%s '
                'relation=%s rows_present=%s policies_present=%s '
                'comment_present=%s column_acl_present=%s '
                'external_table_acl_present=%s',
                bootstrap_columns,
                bootstrap_constraints,
                bootstrap_indexes,
                bootstrap_trigger_count,
                bootstrap_insert_ri_trigger_count,
                bootstrap_update_ri_trigger_count,
                (
                  SELECT pg_catalog.format(
                           'owner=%s kind=%s persistence=%s rls=%s '
                           'force_rls=%s triggers=%s',
                           table_row.relowner::regrole,
                           table_row.relkind,
                           table_row.relpersistence,
                           table_row.relrowsecurity,
                           table_row.relforcerowsecurity,
                           table_row.relhastriggers
                         )
                    FROM pg_catalog.pg_class AS table_row
                   WHERE table_row.oid = admission_table
                ),
                EXISTS (
                  SELECT 1
                    FROM operations.reviewed_identity_finalizer_admissions
                ),
                EXISTS (
                  SELECT 1
                    FROM pg_catalog.pg_policy
                   WHERE polrelid = admission_table
                ),
                pg_catalog.obj_description(
                  admission_table, 'pg_class'
                ) IS NOT NULL,
                EXISTS (
                  SELECT 1
                    FROM pg_catalog.pg_attribute AS attribute
                   WHERE attribute.attrelid = admission_table
                     AND attribute.attnum > 0
                     AND NOT attribute.attisdropped
                     AND attribute.attacl IS NOT NULL
                ),
                EXISTS (
                  SELECT 1
                    FROM pg_catalog.aclexplode(
                      coalesce(
                        (SELECT relacl FROM pg_catalog.pg_class
                          WHERE oid = admission_table),
                        pg_catalog.acldefault('r', owner_oid)
                      )
                    ) AS table_acl
                   WHERE table_acl.grantee <> owner_oid
                )
              );
    END IF;
    RETURN;
  END IF;
  IF primary_object_count <> 5
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_runs'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_source_items'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_decisions'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_item_receipts'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_finalizations'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_projection_lineage'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_projection_subjects'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_migration_erasure_impacts'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.legacy_identity_writer_evidence'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.privacy_cutover_check_receipts'
     ) IS NULL
     OR pg_catalog.to_regclass(
       'operations.semantic_authority_cutovers'
     ) IS NULL
     OR person_blocked_function IS NULL
     OR NOT (
       current_revision = ANY (reviewed_e3_catalog_revisions)
     ) THEN
    RAISE EXCEPTION 'partial identity finalizer E3 object set'
      USING ERRCODE = '55000';
  END IF;

  SELECT oid INTO STRICT finalizer_oid
    FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_identity_finalizer';
  SELECT oid INTO STRICT kernel_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_finalizer_kernel';
  SELECT oid INTO STRICT erasure_kernel_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_erasure_kernel';
  IF current_revision = '0014_identity_cutover_e4' THEN
    SELECT oid INTO STRICT cutover_kernel_oid
      FROM pg_catalog.pg_roles
     WHERE rolname = 'home_agent_identity_cutover_kernel';
  END IF;
  SELECT oid INTO STRICT database_oid
    FROM pg_catalog.pg_database
   WHERE datname = pg_catalog.current_database();

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS login_role
        WHERE login_role.oid = finalizer_oid
          AND login_role.rolcanlogin
          AND NOT login_role.rolinherit
          AND NOT login_role.rolsuper
          AND NOT login_role.rolcreatedb
          AND NOT login_role.rolcreaterole
          AND NOT login_role.rolreplication
           AND NOT login_role.rolbypassrls
           AND login_role.rolconnlimit = 1
           AND login_role.rolvaliduntil IS NOT NULL
           AND login_role.rolvaliduntil =
               timestamptz '1970-01-01 00:00:00+00'
           AND login_role.rolconfig = ARRAY[
             'default_transaction_isolation=serializable',
             'statement_timeout=120s',
             'lock_timeout=5s',
             'idle_in_transaction_session_timeout=15s',
             'transaction_timeout=180s',
             'log_parameter_max_length_on_error=0'
           ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS kernel_role
        WHERE kernel_role.oid = kernel_oid
          AND NOT kernel_role.rolcanlogin
          AND NOT kernel_role.rolinherit
          AND NOT kernel_role.rolsuper
          AND NOT kernel_role.rolcreatedb
          AND NOT kernel_role.rolcreaterole
          AND NOT kernel_role.rolreplication
           AND NOT kernel_role.rolbypassrls
           AND kernel_role.rolconnlimit = 0
           AND kernel_role.rolvaliduntil IS NULL
           AND kernel_role.rolconfig IS NULL
     )
     OR NOT pg_catalog.has_database_privilege(
       finalizer_oid, database_oid, 'CONNECT'
     )
     OR pg_catalog.has_database_privilege(
       finalizer_oid, database_oid, 'CREATE,TEMPORARY'
     )
     OR pg_catalog.has_database_privilege(
       kernel_oid, database_oid, 'CONNECT,CREATE,TEMPORARY'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = finalizer_oid
           OR membership.roleid = finalizer_oid
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = kernel_oid
          AND membership.member = owner_oid
          AND NOT membership.admin_option
          AND NOT membership.inherit_option
          AND membership.set_option
     ) <> 1
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = kernel_oid
     ) <> 1
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_db_role_setting AS database_setting
        WHERE database_setting.setrole IN (finalizer_oid, kernel_oid)
          AND database_setting.setdatabase <> 0
      ) THEN
    RAISE EXCEPTION 'identity finalizer E3 dormant role contract mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS login_ownership
        WHERE login_ownership.refobjid = finalizer_oid
          AND login_ownership.deptype = 'o'
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_shdepend AS kernel_ownership
        WHERE kernel_ownership.refobjid = kernel_oid
          AND kernel_ownership.deptype = 'o'
     ) <> 1
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS kernel_ownership
        WHERE kernel_ownership.refobjid = kernel_oid
          AND kernel_ownership.deptype = 'o'
          AND NOT (
            kernel_ownership.dbid = database_oid
            AND kernel_ownership.classid = 'pg_catalog.pg_proc'::regclass
            AND kernel_ownership.objid = finalizer_function::oid
            AND kernel_ownership.objsubid = 0
          )
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 ownership dependency mismatch'
      USING ERRCODE = '42501';
  END IF;

  FOREACH target_table IN ARRAY evidence_tables
  LOOP
    evidence_table := pg_catalog.to_regclass(target_table);
    IF evidence_table IS NULL
       OR NOT EXISTS (
         SELECT 1
           FROM pg_catalog.pg_class AS table_row
          WHERE table_row.oid = evidence_table
            AND table_row.relowner = owner_oid
            AND table_row.relkind = 'r'
            AND table_row.relpersistence = 'p'
            AND table_row.relrowsecurity
            AND table_row.relforcerowsecurity
       ) THEN
      RAISE EXCEPTION
        'identity finalizer E3 evidence relation contract mismatch: %',
        target_table
        USING ERRCODE = '42501';
    END IF;
  END LOOP;

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = finalizer_function
           AND function_row.proowner = kernel_oid
           AND function_row.prolang = (
             SELECT language_row.oid
               FROM pg_catalog.pg_language AS language_row
              WHERE language_row.lanname = 'plpgsql'
           )
           AND function_row.prosecdef
           AND function_row.prokind = 'f'
           AND function_row.provolatile = 'v'
           AND function_row.prorettype = 'uuid'::regtype
           AND NOT function_row.proretset
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.pronargs = 2
           AND function_row.pronargdefaults = 0
           AND function_row.proargtypes =
               ARRAY[
                 'bytea'::pg_catalog.regtype::oid,
                 'uuid'::pg_catalog.regtype::oid
               ]::pg_catalog.oidvector
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.proargnames =
               ARRAY['finalizer_document','admission_id']::text[]
           AND function_row.proconfig =
               ARRAY['search_path=pg_catalog, pg_temp']::text[]
           AND pg_catalog.encode(
                 pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                 ),
                 'hex'
               ) = expected_finalizer_body_sha256
      )
      OR NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = fence_function
           AND function_row.proowner = owner_oid
           AND function_row.prolang = (
             SELECT language_row.oid
               FROM pg_catalog.pg_language AS language_row
              WHERE language_row.lanname = 'plpgsql'
           )
           AND function_row.prosecdef
           AND function_row.prokind = 'f'
           AND function_row.provolatile = 'v'
           AND function_row.prorettype = 'void'::regtype
           AND NOT function_row.proretset
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.pronargs = 0
           AND function_row.pronargdefaults = 0
           AND function_row.proargtypes = ''::pg_catalog.oidvector
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.proargnames IS NULL
           AND function_row.proconfig =
               ARRAY['search_path=pg_catalog, pg_temp']::text[]
           AND pg_catalog.encode(
                 pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                 ),
                 'hex'
               ) = expected_fence_body_sha256
      )
      OR NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = fence_trigger_function
           AND function_row.proowner = owner_oid
           AND function_row.prolang = (
             SELECT language_row.oid
               FROM pg_catalog.pg_language AS language_row
              WHERE language_row.lanname = 'plpgsql'
           )
           AND function_row.prosecdef
           AND function_row.prokind = 'f'
           AND function_row.provolatile = 'v'
           AND function_row.prorettype = 'trigger'::regtype
           AND NOT function_row.proretset
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.pronargs = 0
           AND function_row.pronargdefaults = 0
           AND function_row.proargtypes = ''::pg_catalog.oidvector
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.proargnames IS NULL
           AND function_row.proconfig =
               ARRAY['search_path=pg_catalog, pg_temp']::text[]
           AND pg_catalog.encode(
                 pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                 ),
                 'hex'
               ) = expected_fence_trigger_body_sha256
      )
      OR NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = person_blocked_function
           AND function_row.proowner = erasure_kernel_oid
           AND function_row.prolang = (
             SELECT language_row.oid
               FROM pg_catalog.pg_language AS language_row
              WHERE language_row.lanname = 'sql'
           )
           AND function_row.prosecdef
           AND function_row.prokind = 'f'
           AND function_row.provolatile = 's'
           AND function_row.prorettype = 'boolean'::regtype
           AND NOT function_row.proretset
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.pronargs = 1
           AND function_row.pronargdefaults = 0
           AND function_row.proargtypes =
               ARRAY[
                 'uuid'::pg_catalog.regtype::oid
               ]::pg_catalog.oidvector
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.proargnames =
               ARRAY['target_person_id']::text[]
           AND function_row.proconfig =
               ARRAY['search_path=pg_catalog']::text[]
           AND pg_catalog.encode(
                 pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                 ),
                 'hex'
               ) = expected_person_blocked_body_sha256
      ) THEN
    RAISE EXCEPTION 'identity finalizer E3 function contract mismatch'
      USING ERRCODE = '42501',
            DETAIL = pg_catalog.format(
              'finalizer=%s fence=%s trigger=%s person_blocked=%s',
              pg_catalog.encode(
                pg_catalog.sha256(pg_catalog.convert_to(
                  (SELECT prosrc FROM pg_catalog.pg_proc
                    WHERE oid = finalizer_function), 'UTF8'
                )), 'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(pg_catalog.convert_to(
                  (SELECT prosrc FROM pg_catalog.pg_proc
                    WHERE oid = fence_function), 'UTF8'
                )), 'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(pg_catalog.convert_to(
                  (SELECT prosrc FROM pg_catalog.pg_proc
                    WHERE oid = fence_trigger_function), 'UTF8'
                )), 'hex'
              ),
              pg_catalog.encode(
                pg_catalog.sha256(pg_catalog.convert_to(
                  (SELECT prosrc FROM pg_catalog.pg_proc
                    WHERE oid = person_blocked_function), 'UTF8'
                )), 'hex'
              )
            );
  END IF;

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS table_row
        WHERE table_row.oid = admission_table
          AND table_row.relowner = owner_oid
          AND table_row.relkind = 'r'
          AND table_row.relpersistence = 'p'
          AND table_row.relrowsecurity
          AND table_row.relforcerowsecurity
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS table_row
        WHERE table_row.oid = fence_table
          AND table_row.relowner = owner_oid
          AND table_row.relkind = 'r'
          AND table_row.relpersistence = 'p'
          AND table_row.relrowsecurity
          AND table_row.relforcerowsecurity
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM privacy.identity_semantic_write_fence
        WHERE fence_key = 'global' AND fence_generation = 1
     ) <> 1
     OR (
       SELECT pg_catalog.count(*)
         FROM privacy.identity_semantic_write_fence
     ) <> 1
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_trigger AS trigger_row
        WHERE trigger_row.tgrelid =
              'privacy.subject_retrieval_blocks'::regclass
          AND trigger_row.tgname =
              'subject_retrieval_blocks_identity_write_fence'
          AND trigger_row.tgfoid = fence_trigger_function
          AND trigger_row.tgenabled = 'O'
          AND NOT trigger_row.tgisinternal
           AND trigger_row.tgtype = 23
           AND trigger_row.tgnargs = 0
           AND trigger_row.tgargs = ''::bytea
           AND trigger_row.tgqual IS NULL
           AND trigger_row.tgoldtable IS NULL
           AND trigger_row.tgnewtable IS NULL
           AND trigger_row.tgattr::text = (
            SELECT attribute.attnum::text
              FROM pg_catalog.pg_attribute AS attribute
             WHERE attribute.attrelid =
                   'privacy.subject_retrieval_blocks'::regclass
               AND attribute.attname = 'person_id'
               AND NOT attribute.attisdropped
          )
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 write-fence contract mismatch'
      USING ERRCODE = '42501';
  END IF;

  -- Restore the exact RLS policy texts. Admission/fence tables have no other
  -- policies; evidence tables retain their reviewed E1/E2 owner policies.
  DROP POLICY IF EXISTS reviewed_identity_finalizer_admissions_owner_select
    ON operations.reviewed_identity_finalizer_admissions;
  CREATE POLICY reviewed_identity_finalizer_admissions_owner_select
    ON operations.reviewed_identity_finalizer_admissions
    FOR SELECT TO home_agent_owner
    USING (session_user = 'home_agent_owner');
  DROP POLICY IF EXISTS reviewed_identity_finalizer_admissions_owner_insert
    ON operations.reviewed_identity_finalizer_admissions;
  CREATE POLICY reviewed_identity_finalizer_admissions_owner_insert
    ON operations.reviewed_identity_finalizer_admissions
    FOR INSERT TO home_agent_owner
    WITH CHECK (session_user = 'home_agent_owner');
  DROP POLICY IF EXISTS reviewed_identity_finalizer_admissions_e3_kernel_select
    ON operations.reviewed_identity_finalizer_admissions;
  CREATE POLICY reviewed_identity_finalizer_admissions_e3_kernel_select
    ON operations.reviewed_identity_finalizer_admissions
    FOR SELECT TO home_agent_identity_finalizer_kernel
    USING (
      session_user = 'home_agent_identity_finalizer'
      AND current_user = 'home_agent_identity_finalizer_kernel'
      AND NOT pg_catalog.pg_has_role(
        session_user, 'home_agent_identity_finalizer_kernel', 'SET'
      )
    );
  DROP POLICY IF EXISTS reviewed_identity_finalizer_admissions_e3_kernel_update
    ON operations.reviewed_identity_finalizer_admissions;
  CREATE POLICY reviewed_identity_finalizer_admissions_e3_kernel_update
    ON operations.reviewed_identity_finalizer_admissions
    FOR UPDATE TO home_agent_identity_finalizer_kernel
    USING (
      session_user = 'home_agent_identity_finalizer'
      AND current_user = 'home_agent_identity_finalizer_kernel'
      AND NOT pg_catalog.pg_has_role(
        session_user, 'home_agent_identity_finalizer_kernel', 'SET'
      )
    )
    WITH CHECK (
      session_user = 'home_agent_identity_finalizer'
      AND current_user = 'home_agent_identity_finalizer_kernel'
      AND NOT pg_catalog.pg_has_role(
        session_user, 'home_agent_identity_finalizer_kernel', 'SET'
      )
    );
  DROP POLICY IF EXISTS identity_semantic_write_fence_owner
    ON privacy.identity_semantic_write_fence;
  CREATE POLICY identity_semantic_write_fence_owner
    ON privacy.identity_semantic_write_fence
    FOR ALL TO home_agent_owner
    USING (current_user = 'home_agent_owner')
    WITH CHECK (current_user = 'home_agent_owner');

  -- Revision 0014 adds one reviewed E4 SELECT policy to nine E3 evidence
  -- relations. Validate that overlay against its E4-owned reference policy
  -- before projecting it out of the E3-only policy count and catalog digest.
  -- The E4 block below separately validates the complete E4 policy catalog.
  IF current_revision = '0014_identity_cutover_e4'
     AND (
       (
         SELECT pg_catalog.count(*)
           FROM pg_catalog.pg_policy AS policy_row
          WHERE policy_row.polname = e4_select_policy
            AND policy_row.polrelid IN (
              SELECT pg_catalog.to_regclass(target.target_name)
                FROM pg_catalog.unnest(
                       e4_e3_policy_relations
                     ) AS target(target_name)
            )
       ) <> pg_catalog.cardinality(e4_e3_policy_relations)
       OR EXISTS (
         SELECT 1
           FROM pg_catalog.unnest(
                  e4_e3_policy_relations
                ) AS target(target_name)
           LEFT JOIN pg_catalog.pg_policy AS policy_row
             ON policy_row.polrelid =
                  pg_catalog.to_regclass(target.target_name)
            AND policy_row.polname = e4_select_policy
           LEFT JOIN pg_catalog.pg_policy AS reference_policy
             ON reference_policy.polrelid =
                  pg_catalog.to_regclass(
                    'operations.enforced_legacy_identity_writer_freezes'
                  )
            AND reference_policy.polname = e4_select_policy
          WHERE policy_row.oid IS NULL
             OR reference_policy.oid IS NULL
             OR NOT policy_row.polpermissive
             OR policy_row.polcmd <> 'r'
             OR policy_row.polroles <>
                  ARRAY[cutover_kernel_oid]::oid[]
             OR policy_row.polqual IS NULL
             OR policy_row.polwithcheck IS NOT NULL
             OR NOT reference_policy.polpermissive
             OR reference_policy.polcmd <> 'r'
             OR reference_policy.polroles <>
                  ARRAY[cutover_kernel_oid]::oid[]
             OR reference_policy.polqual IS NULL
             OR reference_policy.polwithcheck IS NOT NULL
             OR policy_row.polqual::text <>
                  reference_policy.polqual::text
       )
     ) THEN
    RAISE EXCEPTION
      'identity finalizer E3 reviewed descendant policy mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_policy
        WHERE polrelid = admission_table
     ) <> (
       CASE
         WHEN current_revision = '0014_identity_cutover_e4' THEN 5
         ELSE 4
       END
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_policy
        WHERE polrelid = fence_table
     ) <> 1 THEN
    RAISE EXCEPTION 'identity finalizer E3 control policy set mismatch'
      USING ERRCODE = '42501';
  END IF;

  FOREACH target_table IN ARRAY ARRAY[
    'operations.reviewed_identity_migration_runs',
    'operations.reviewed_identity_migration_source_items',
    'operations.reviewed_identity_migration_decisions',
    'operations.reviewed_identity_migration_erasure_impacts',
    'operations.legacy_identity_writer_evidence',
    'operations.privacy_cutover_check_receipts',
    'operations.semantic_authority_cutovers'
  ]::text[]
  LOOP
    EXECUTE pg_catalog.format(
      'DROP POLICY IF EXISTS %I ON %s',
      evidence_select_policy,
      target_table
    );
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %s FOR SELECT '
      'TO home_agent_identity_finalizer_kernel USING (%s)',
      evidence_select_policy,
      target_table,
      predicate
    );
  END LOOP;
  DROP POLICY IF EXISTS identity_finalizer_e3_migration_run_lock
    ON operations.reviewed_identity_migration_runs;
  CREATE POLICY identity_finalizer_e3_migration_run_lock
    ON operations.reviewed_identity_migration_runs
    FOR UPDATE TO home_agent_identity_finalizer_kernel
    USING (
      session_user = 'home_agent_identity_finalizer'
      AND current_user = 'home_agent_identity_finalizer_kernel'
      AND NOT pg_catalog.pg_has_role(
        session_user, 'home_agent_identity_finalizer_kernel', 'SET'
      )
    )
    WITH CHECK (false);
  FOREACH target_table IN ARRAY ARRAY[
    'operations.reviewed_identity_migration_item_receipts',
    'operations.reviewed_identity_migration_finalizations',
    'operations.reviewed_identity_migration_projection_lineage',
    'operations.reviewed_identity_migration_projection_subjects'
  ]::text[]
  LOOP
    EXECUTE pg_catalog.format(
      'DROP POLICY IF EXISTS %I ON %s',
      evidence_select_policy,
      target_table
    );
    EXECUTE pg_catalog.format(
      'DROP POLICY IF EXISTS %I ON %s',
      evidence_insert_policy,
      target_table
    );
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %s FOR SELECT '
      'TO home_agent_identity_finalizer_kernel USING (%s)',
      evidence_select_policy,
      target_table,
      predicate
    );
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %s FOR INSERT '
      'TO home_agent_identity_finalizer_kernel WITH CHECK (%s)',
      evidence_insert_policy,
      target_table,
      predicate
    );
  END LOOP;

  IF (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_policy AS policy_row
        WHERE policy_row.polrelid = ANY (ARRAY[
          'operations.reviewed_identity_migration_runs'::regclass,
          'operations.reviewed_identity_migration_source_items'::regclass,
          'operations.reviewed_identity_migration_decisions'::regclass,
          'operations.reviewed_identity_migration_erasure_impacts'::regclass,
          'operations.legacy_identity_writer_evidence'::regclass,
          'operations.privacy_cutover_check_receipts'::regclass,
          'operations.semantic_authority_cutovers'::regclass,
          'operations.reviewed_identity_migration_item_receipts'::regclass,
          'operations.reviewed_identity_migration_finalizations'::regclass,
          'operations.reviewed_identity_migration_projection_lineage'::regclass,
          'operations.reviewed_identity_migration_projection_subjects'::regclass
        ]::oid[])
          AND (
            0 = ANY (policy_row.polroles)
            OR kernel_oid = ANY (policy_row.polroles)
          )
     ) <> 16
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_policy AS policy_row
        WHERE policy_row.polrelid = ANY (ARRAY[
          'operations.reviewed_identity_migration_runs'::regclass,
          'operations.reviewed_identity_migration_source_items'::regclass,
          'operations.reviewed_identity_migration_decisions'::regclass,
          'operations.reviewed_identity_migration_erasure_impacts'::regclass,
          'operations.legacy_identity_writer_evidence'::regclass,
          'operations.privacy_cutover_check_receipts'::regclass,
          'operations.semantic_authority_cutovers'::regclass,
          'operations.reviewed_identity_migration_item_receipts'::regclass,
          'operations.reviewed_identity_migration_finalizations'::regclass,
          'operations.reviewed_identity_migration_projection_lineage'::regclass,
          'operations.reviewed_identity_migration_projection_subjects'::regclass
        ]::oid[])
          AND (
            0 = ANY (policy_row.polroles)
            OR kernel_oid = ANY (policy_row.polroles)
          )
          AND policy_row.polname NOT IN (
            evidence_select_policy,
            evidence_insert_policy,
            migration_run_lock_policy
          )
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_policy AS lock_policy
         JOIN pg_catalog.pg_policy AS select_policy
           ON select_policy.polrelid = lock_policy.polrelid
          AND select_policy.polname = evidence_select_policy
        WHERE lock_policy.polrelid =
              'operations.reviewed_identity_migration_runs'::regclass
          AND lock_policy.polname = migration_run_lock_policy
          AND lock_policy.polpermissive
          AND lock_policy.polcmd = 'w'
          AND lock_policy.polroles = ARRAY[kernel_oid]::oid[]
          AND lock_policy.polqual::text = select_policy.polqual::text
          AND pg_catalog.pg_get_expr(
                lock_policy.polwithcheck,
                lock_policy.polrelid,
                true
              ) = 'false'
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 evidence policy set mismatch'
      USING ERRCODE = '42501';
  END IF;

  -- One reviewed digest pins the complete PostgreSQL catalog shape of the
  -- admission, fence, and eleven evidence relations: columns/defaults,
  -- constraints and their definitions, indexes, RLS policies, comments, and
  -- non-internal triggers. It is intentionally PostgreSQL-image-specific.
  -- The digest is emitted in DETAIL while the 0013 migration is still moving;
  -- replace the PENDING sentinel only after the migration body is frozen.
  WITH target_relations(target_name, relation_oid) AS (
    SELECT target_name, pg_catalog.to_regclass(target_name)
      FROM pg_catalog.unnest(
        evidence_tables || ARRAY[
          'operations.reviewed_identity_finalizer_admissions',
          'privacy.identity_semantic_write_fence'
        ]::text[]
      ) AS target(target_name)
  ),
  relation_manifests AS (
    SELECT target.target_name,
           pg_catalog.jsonb_build_object(
             'owner', owner_role.rolname,
             'kind', relation.relkind,
             'persistence', relation.relpersistence,
             'rls', relation.relrowsecurity,
             'force_rls', relation.relforcerowsecurity,
             'replica_identity', relation.relreplident,
             'comment', pg_catalog.obj_description(
               relation.oid, 'pg_class'
             ),
             'columns', (
               SELECT pg_catalog.jsonb_agg(
                        pg_catalog.jsonb_build_array(
                          attribute.attnum,
                          attribute.attname,
                          pg_catalog.format_type(
                            attribute.atttypid, attribute.atttypmod
                          ),
                          attribute.attnotnull,
                          attribute.attidentity,
                          attribute.attgenerated,
                          CASE
                            WHEN attribute.attcollation = 0 THEN NULL
                            ELSE attribute.attcollation::regcollation::text
                          END,
                          pg_catalog.pg_get_expr(
                            default_row.adbin, default_row.adrelid, true
                          )
                        )
                        ORDER BY attribute.attnum
                      )
                 FROM pg_catalog.pg_attribute AS attribute
                 LEFT JOIN pg_catalog.pg_attrdef AS default_row
                   ON default_row.adrelid = attribute.attrelid
                  AND default_row.adnum = attribute.attnum
                WHERE attribute.attrelid = relation.oid
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
             ),
             'constraints', (
               SELECT coalesce(
                        pg_catalog.jsonb_agg(
                          pg_catalog.jsonb_build_array(
                            constraint_row.conname,
                            constraint_row.contype,
                            constraint_row.condeferrable,
                            constraint_row.condeferred,
                            constraint_row.convalidated,
                            constraint_row.conkey::text,
                            CASE
                              WHEN constraint_row.confrelid = 0 THEN NULL
                              ELSE constraint_row.confrelid::regclass::text
                            END,
                            constraint_row.confkey::text,
                            constraint_row.confupdtype,
                            constraint_row.confdeltype,
                            constraint_row.confmatchtype,
                            pg_catalog.pg_get_constraintdef(
                              constraint_row.oid, true
                            )
                          )
                          ORDER BY constraint_row.conname
                        ),
                        '[]'::jsonb
                      )
                 FROM pg_catalog.pg_constraint AS constraint_row
                WHERE constraint_row.conrelid = relation.oid
             ),
             'indexes', (
               SELECT coalesce(
                        pg_catalog.jsonb_agg(
                          pg_catalog.jsonb_build_array(
                            index_relation.relname,
                            index_state.indisunique,
                            index_state.indisprimary,
                            index_state.indisexclusion,
                            index_state.indimmediate,
                            index_state.indisvalid,
                            index_state.indisready,
                            index_state.indkey::text,
                            pg_catalog.pg_get_indexdef(
                              index_relation.oid, 0, true
                            )
                          )
                          ORDER BY index_relation.relname
                        ),
                        '[]'::jsonb
                      )
                 FROM pg_catalog.pg_index AS index_state
                 JOIN pg_catalog.pg_class AS index_relation
                   ON index_relation.oid = index_state.indexrelid
                WHERE index_state.indrelid = relation.oid
             ),
             'policies', (
               SELECT coalesce(
                        pg_catalog.jsonb_agg(
                          pg_catalog.jsonb_build_array(
                            policy_row.polname,
                            policy_row.polpermissive,
                            policy_row.polcmd,
                            (
                              SELECT pg_catalog.jsonb_agg(
                                       CASE
                                         WHEN policy_role.role_oid = 0
                                           THEN 'PUBLIC'
                                         ELSE role_row.rolname
                                       END
                                       ORDER BY policy_role.role_oid
                                     )
                                FROM pg_catalog.unnest(
                                  policy_row.polroles
                                ) AS policy_role(role_oid)
                                LEFT JOIN pg_catalog.pg_roles AS role_row
                                  ON role_row.oid = policy_role.role_oid
                            ),
                            pg_catalog.pg_get_expr(
                              policy_row.polqual, policy_row.polrelid, true
                            ),
                            pg_catalog.pg_get_expr(
                              policy_row.polwithcheck,
                              policy_row.polrelid,
                              true
                            )
                          )
                          ORDER BY policy_row.polname
                        ),
                        '[]'::jsonb
                      )
                  FROM pg_catalog.pg_policy AS policy_row
                 WHERE policy_row.polrelid = relation.oid
                   AND NOT (
                     current_revision = '0014_identity_cutover_e4'
                     AND policy_row.polname = e4_select_policy
                     AND policy_row.polrelid IN (
                       SELECT pg_catalog.to_regclass(target.target_name)
                         FROM pg_catalog.unnest(
                                e4_e3_policy_relations
                              ) AS target(target_name)
                     )
                     AND policy_row.polpermissive
                     AND policy_row.polcmd = 'r'
                     AND policy_row.polroles =
                           ARRAY[cutover_kernel_oid]::oid[]
                     AND policy_row.polqual IS NOT NULL
                     AND policy_row.polwithcheck IS NULL
                     AND EXISTS (
                       SELECT 1
                         FROM pg_catalog.pg_policy AS reference_policy
                        WHERE reference_policy.polrelid =
                              pg_catalog.to_regclass(
                                'operations.enforced_legacy_identity_writer_freezes'
                              )
                          AND reference_policy.polname = e4_select_policy
                          AND reference_policy.polpermissive
                          AND reference_policy.polcmd = 'r'
                          AND reference_policy.polroles =
                                ARRAY[cutover_kernel_oid]::oid[]
                          AND reference_policy.polqual IS NOT NULL
                          AND reference_policy.polwithcheck IS NULL
                          AND reference_policy.polqual::text =
                                policy_row.polqual::text
                     )
                   )
              ),
             'triggers', (
               SELECT coalesce(
                        pg_catalog.jsonb_agg(
                          pg_catalog.jsonb_build_array(
                            trigger_row.tgname,
                            trigger_row.tgenabled,
                            trigger_row.tgtype,
                            trigger_row.tgattr::text,
                            pg_catalog.encode(trigger_row.tgargs, 'hex'),
                            pg_catalog.pg_get_expr(
                              trigger_row.tgqual, trigger_row.tgrelid, true
                            ),
                            trigger_row.tgfoid::regprocedure::text,
                            pg_catalog.pg_get_triggerdef(
                              trigger_row.oid, true
                            )
                          )
                          ORDER BY trigger_row.tgname
                        ),
                        '[]'::jsonb
                      )
                 FROM pg_catalog.pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid = relation.oid
                  AND NOT trigger_row.tgisinternal
             )
           ) AS manifest
      FROM target_relations AS target
      JOIN pg_catalog.pg_class AS relation
        ON relation.oid = target.relation_oid
      JOIN pg_catalog.pg_roles AS owner_role
        ON owner_role.oid = relation.relowner
  )
  SELECT pg_catalog.encode(
           pg_catalog.sha256(
             pg_catalog.convert_to(
               pg_catalog.jsonb_agg(
                 pg_catalog.jsonb_build_array(
                   manifest.target_name, manifest.manifest
                 )
                 ORDER BY manifest.target_name
               )::text,
               'UTF8'
             )
           ),
           'hex'
         )
    INTO STRICT actual_e3_catalog_sha256
    FROM relation_manifests AS manifest;
  IF expected_e3_catalog_sha256 !~ '^[0-9a-f]{64}$'
     OR actual_e3_catalog_sha256 <> expected_e3_catalog_sha256 THEN
    RAISE EXCEPTION 'identity finalizer E3 catalog manifest mismatch'
      USING ERRCODE = '42501',
            DETAIL = pg_catalog.format(
              'expected=%s actual=%s',
              expected_e3_catalog_sha256,
              actual_e3_catalog_sha256
            );
  END IF;

  GRANT USAGE ON SCHEMA operations
    TO home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
  GRANT USAGE ON SCHEMA identity, privacy
    TO home_agent_identity_finalizer_kernel;
  GRANT SELECT ON TABLE
    operations.reviewed_identity_migration_runs,
    operations.reviewed_identity_migration_source_items,
    operations.reviewed_identity_migration_decisions,
    operations.reviewed_identity_migration_erasure_impacts,
    operations.legacy_identity_writer_evidence,
    operations.privacy_cutover_check_receipts,
    operations.semantic_authority_cutovers
    TO home_agent_identity_finalizer_kernel;
  GRANT UPDATE (expires_at)
    ON TABLE operations.reviewed_identity_migration_runs
    TO home_agent_identity_finalizer_kernel;
  GRANT SELECT ON TABLE operations.reviewed_identity_finalizer_admissions
    TO home_agent_identity_finalizer_kernel;
  GRANT UPDATE (consumed_at)
    ON TABLE operations.reviewed_identity_finalizer_admissions
    TO home_agent_identity_finalizer_kernel;
  GRANT SELECT, INSERT ON TABLE
    operations.reviewed_identity_migration_item_receipts,
    operations.reviewed_identity_migration_finalizations,
    operations.reviewed_identity_migration_projection_lineage,
    operations.reviewed_identity_migration_projection_subjects
    TO home_agent_identity_finalizer_kernel;
  GRANT SELECT, INSERT ON TABLE identity.people
    TO home_agent_identity_finalizer_kernel;
  GRANT UPDATE (
    status, status_source_ref, status_source_version,
    status_source_sha256, updated_at
  ) ON TABLE identity.people TO home_agent_identity_finalizer_kernel;
  GRANT SELECT, INSERT ON TABLE
    identity.aliases,
    identity.external_recognition_bindings,
    identity.privacy_directives,
    identity.legacy_role_labels,
    identity.legacy_relationship_candidates,
    privacy.auto_expiry_schedules,
    operations.outbox
    TO home_agent_identity_finalizer_kernel;
  GRANT EXECUTE ON FUNCTION privacy.identity_person_is_blocked(uuid),
    privacy.lock_identity_semantic_write_fence()
    TO home_agent_identity_finalizer_kernel;
  GRANT EXECUTE ON FUNCTION
    operations.finalize_reviewed_identity_migration(bytea,uuid)
    TO home_agent_identity_finalizer;

  expected_schema_acl := ARRAY[
    'home_agent_identity_finalizer|operations|USAGE',
    'home_agent_identity_finalizer_kernel|identity|USAGE',
    'home_agent_identity_finalizer_kernel|operations|USAGE',
    'home_agent_identity_finalizer_kernel|privacy|USAGE'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' || namespace_row.nspname || '|' ||
           schema_acl.privilege_type
           ORDER BY role_row.rolname, namespace_row.nspname,
                    schema_acl.privilege_type
         )
    INTO actual_schema_acl
    FROM pg_catalog.pg_namespace AS namespace_row
    CROSS JOIN LATERAL
      pg_catalog.aclexplode(namespace_row.nspacl) AS schema_acl
    JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = schema_acl.grantee
   WHERE role_row.oid IN (finalizer_oid, kernel_oid);
  IF actual_schema_acl IS DISTINCT FROM expected_schema_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 schema ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  expected_table_acl := ARRAY[
    'home_agent_identity_finalizer_kernel|identity.aliases|INSERT',
    'home_agent_identity_finalizer_kernel|identity.aliases|SELECT',
    'home_agent_identity_finalizer_kernel|identity.external_recognition_bindings|INSERT',
    'home_agent_identity_finalizer_kernel|identity.external_recognition_bindings|SELECT',
    'home_agent_identity_finalizer_kernel|identity.legacy_relationship_candidates|INSERT',
    'home_agent_identity_finalizer_kernel|identity.legacy_relationship_candidates|SELECT',
    'home_agent_identity_finalizer_kernel|identity.legacy_role_labels|INSERT',
    'home_agent_identity_finalizer_kernel|identity.legacy_role_labels|SELECT',
    'home_agent_identity_finalizer_kernel|identity.people|INSERT',
    'home_agent_identity_finalizer_kernel|identity.people|SELECT',
    'home_agent_identity_finalizer_kernel|identity.privacy_directives|INSERT',
    'home_agent_identity_finalizer_kernel|identity.privacy_directives|SELECT',
    'home_agent_identity_finalizer_kernel|operations.legacy_identity_writer_evidence|SELECT',
    'home_agent_identity_finalizer_kernel|operations.outbox|INSERT',
    'home_agent_identity_finalizer_kernel|operations.outbox|SELECT',
    'home_agent_identity_finalizer_kernel|operations.privacy_cutover_check_receipts|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_finalizer_admissions|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_decisions|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_erasure_impacts|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_finalizations|INSERT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_finalizations|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_item_receipts|INSERT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_item_receipts|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_projection_lineage|INSERT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_projection_lineage|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_projection_subjects|INSERT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_projection_subjects|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_runs|SELECT',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_source_items|SELECT',
    'home_agent_identity_finalizer_kernel|operations.semantic_authority_cutovers|SELECT',
    'home_agent_identity_finalizer_kernel|privacy.auto_expiry_schedules|INSERT',
    'home_agent_identity_finalizer_kernel|privacy.auto_expiry_schedules|SELECT'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' || table_namespace.nspname || '.' ||
           table_row.relname || '|' || table_acl.privilege_type
           ORDER BY role_row.rolname, table_namespace.nspname,
                    table_row.relname, table_acl.privilege_type
         )
    INTO actual_table_acl
    FROM pg_catalog.pg_class AS table_row
    JOIN pg_catalog.pg_namespace AS table_namespace
      ON table_namespace.oid = table_row.relnamespace
    CROSS JOIN LATERAL
      pg_catalog.aclexplode(table_row.relacl) AS table_acl
    JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = table_acl.grantee
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
     AND table_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     );
  IF actual_table_acl IS DISTINCT FROM expected_table_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 table ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  expected_column_acl := ARRAY[
    'home_agent_identity_finalizer_kernel|identity.people|status|UPDATE',
    'home_agent_identity_finalizer_kernel|identity.people|status_source_ref|UPDATE',
    'home_agent_identity_finalizer_kernel|identity.people|status_source_sha256|UPDATE',
    'home_agent_identity_finalizer_kernel|identity.people|status_source_version|UPDATE',
    'home_agent_identity_finalizer_kernel|identity.people|updated_at|UPDATE',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_finalizer_admissions|consumed_at|UPDATE',
    'home_agent_identity_finalizer_kernel|operations.reviewed_identity_migration_runs|expires_at|UPDATE'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' || table_namespace.nspname || '.' ||
           table_row.relname || '|' || attribute.attname || '|' ||
           column_acl.privilege_type
           ORDER BY role_row.rolname, table_namespace.nspname,
                    table_row.relname, attribute.attname,
                    column_acl.privilege_type
         )
    INTO actual_column_acl
    FROM pg_catalog.pg_attribute AS attribute
    JOIN pg_catalog.pg_class AS table_row
      ON table_row.oid = attribute.attrelid
    JOIN pg_catalog.pg_namespace AS table_namespace
      ON table_namespace.oid = table_row.relnamespace
    CROSS JOIN LATERAL
      pg_catalog.aclexplode(attribute.attacl) AS column_acl
    JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = column_acl.grantee
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
     AND table_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     );
  IF actual_column_acl IS DISTINCT FROM expected_column_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 column ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  expected_function_acl := ARRAY[
    'home_agent_identity_finalizer|operations.finalize_reviewed_identity_migration(bytea,uuid)|EXECUTE',
    'home_agent_identity_finalizer_kernel|privacy.identity_person_is_blocked(uuid)|EXECUTE',
    'home_agent_identity_finalizer_kernel|privacy.lock_identity_semantic_write_fence()|EXECUTE'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' ||
           function_row.oid::regprocedure::text || '|' ||
           function_acl.privilege_type
           ORDER BY role_row.rolname, function_row.oid::regprocedure::text,
                    function_acl.privilege_type
         )
    INTO actual_function_acl
    FROM pg_catalog.pg_proc AS function_row
    JOIN pg_catalog.pg_namespace AS function_namespace
      ON function_namespace.oid = function_row.pronamespace
    CROSS JOIN LATERAL
      pg_catalog.aclexplode(function_row.proacl) AS function_acl
    JOIN pg_catalog.pg_roles AS role_row ON role_row.oid = function_acl.grantee
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
      AND function_namespace.nspname IN (
        'ingest','identity','knowledge','engagement','privacy','operations',
        'media'
      );
  IF actual_function_acl IS DISTINCT FROM expected_function_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 function ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_namespace AS namespace_row
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(namespace_row.nspacl) AS schema_acl
        WHERE schema_acl.grantee IN (finalizer_oid, kernel_oid)
          AND schema_acl.is_grantable
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS table_row
         JOIN pg_catalog.pg_namespace AS table_namespace
           ON table_namespace.oid = table_row.relnamespace
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(table_row.relacl) AS table_acl
        WHERE table_acl.grantee IN (finalizer_oid, kernel_oid)
          AND table_acl.is_grantable
          AND table_namespace.nspname IN (
            'public','ingest','identity','knowledge','engagement','privacy',
            'operations','media'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_attribute AS attribute
         JOIN pg_catalog.pg_class AS table_row
           ON table_row.oid = attribute.attrelid
         JOIN pg_catalog.pg_namespace AS table_namespace
           ON table_namespace.oid = table_row.relnamespace
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(attribute.attacl) AS column_acl
        WHERE column_acl.grantee IN (finalizer_oid, kernel_oid)
          AND column_acl.is_grantable
          AND table_namespace.nspname IN (
            'public','ingest','identity','knowledge','engagement','privacy',
            'operations','media'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS function_row
         JOIN pg_catalog.pg_namespace AS function_namespace
           ON function_namespace.oid = function_row.pronamespace
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(function_row.proacl) AS function_acl
        WHERE function_acl.grantee IN (finalizer_oid, kernel_oid)
          AND function_acl.is_grantable
          AND function_namespace.nspname IN (
            'public','ingest','identity','knowledge','engagement','privacy',
            'operations','media'
          )
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 grant option detected'
      USING ERRCODE = '42501';
  END IF;

  expected_effective_acl := ARRAY[
    'home_agent_identity_finalizer|operations|USAGE',
    'home_agent_identity_finalizer|public|USAGE',
    'home_agent_identity_finalizer_kernel|identity|USAGE',
    'home_agent_identity_finalizer_kernel|operations|USAGE',
    'home_agent_identity_finalizer_kernel|privacy|USAGE',
    'home_agent_identity_finalizer_kernel|public|USAGE'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' || namespace_row.nspname || '|' ||
           privilege_name
           ORDER BY role_row.rolname, namespace_row.nspname, privilege_name
         )
    INTO actual_effective_acl
    FROM pg_catalog.pg_roles AS role_row
    CROSS JOIN pg_catalog.pg_namespace AS namespace_row
    CROSS JOIN pg_catalog.unnest(
      ARRAY['CREATE','USAGE']::text[]
    ) AS privilege(privilege_name)
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
     AND namespace_row.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
     AND pg_catalog.has_schema_privilege(
       role_row.oid, namespace_row.oid, privilege_name
     );
  IF actual_effective_acl IS DISTINCT FROM expected_effective_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 effective schema ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  -- Effective table access catches direct, inherited, and PUBLIC privileges.
  -- Column-only UPDATE does not satisfy has_table_privilege(), and is pinned
  -- separately by expected_column_acl above.
  expected_effective_acl := expected_table_acl;
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' || table_namespace.nspname || '.' ||
           table_row.relname || '|' || privilege_name
           ORDER BY role_row.rolname, table_namespace.nspname,
                    table_row.relname, privilege_name
         )
    INTO actual_effective_acl
    FROM pg_catalog.pg_roles AS role_row
    CROSS JOIN pg_catalog.pg_class AS table_row
    JOIN pg_catalog.pg_namespace AS table_namespace
      ON table_namespace.oid = table_row.relnamespace
    CROSS JOIN pg_catalog.unnest(
      ARRAY[
        'DELETE','INSERT','REFERENCES','SELECT','TRIGGER','TRUNCATE','UPDATE'
      ]::text[]
    ) AS privilege(privilege_name)
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
     AND table_row.relkind IN ('r','p','v','m','f')
     AND table_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
     AND pg_catalog.has_table_privilege(
       role_row.oid, table_row.oid, privilege_name
     );
  IF actual_effective_acl IS DISTINCT FROM expected_effective_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 effective table ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  expected_effective_acl := ARRAY[
    'home_agent_identity_finalizer|operations.finalize_reviewed_identity_migration(bytea,uuid)',
    'home_agent_identity_finalizer_kernel|privacy.identity_person_is_blocked(uuid)',
    'home_agent_identity_finalizer_kernel|privacy.lock_identity_semantic_write_fence()'
  ]::text[];
  SELECT pg_catalog.array_agg(
           role_row.rolname || '|' ||
           function_row.oid::regprocedure::text
           ORDER BY role_row.rolname,
                    function_row.oid::regprocedure::text
         )
    INTO actual_effective_acl
    FROM pg_catalog.pg_roles AS role_row
    CROSS JOIN pg_catalog.pg_proc AS function_row
    JOIN pg_catalog.pg_namespace AS function_namespace
      ON function_namespace.oid = function_row.pronamespace
   WHERE role_row.oid IN (finalizer_oid, kernel_oid)
     AND function_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
     AND pg_catalog.has_function_privilege(
       role_row.oid, function_row.oid, 'EXECUTE'
     );
  IF actual_effective_acl IS DISTINCT FROM expected_effective_acl THEN
    RAISE EXCEPTION 'identity finalizer E3 effective function ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
         CROSS JOIN pg_catalog.pg_class AS sequence_row
         JOIN pg_catalog.pg_namespace AS sequence_namespace
           ON sequence_namespace.oid = sequence_row.relnamespace
        WHERE role_row.oid IN (finalizer_oid, kernel_oid)
          AND sequence_row.relkind = 'S'
          AND sequence_namespace.nspname IN (
            'public','ingest','identity','knowledge','engagement','privacy',
            'operations','media'
          )
          AND (
            pg_catalog.has_sequence_privilege(
              role_row.oid, sequence_row.oid, 'USAGE'
            )
            OR pg_catalog.has_sequence_privilege(
              role_row.oid, sequence_row.oid, 'SELECT'
            )
            OR pg_catalog.has_sequence_privilege(
              role_row.oid, sequence_row.oid, 'UPDATE'
            )
          )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
         CROSS JOIN pg_catalog.pg_type AS type_row
         JOIN pg_catalog.pg_namespace AS type_namespace
           ON type_namespace.oid = type_row.typnamespace
        WHERE role_row.oid IN (finalizer_oid, kernel_oid)
          AND type_namespace.nspname IN (
            'public','ingest','identity','knowledge','engagement','privacy',
            'operations','media'
          )
          AND type_row.typisdefined
          AND type_row.typrelid = 0
          AND type_row.typelem = 0
          AND pg_catalog.has_type_privilege(
            role_row.oid, type_row.oid, 'USAGE'
          )
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 sequence/type ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_default_acl AS default_acl
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(default_acl.defaclacl) AS privilege
        WHERE privilege.grantee IN (finalizer_oid, kernel_oid)
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_default_acl AS default_acl
        WHERE default_acl.defaclrole IN (finalizer_oid, kernel_oid)
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_default_acl AS default_acl
        WHERE default_acl.defaclrole = owner_oid
          AND default_acl.defaclnamespace = 0
          AND default_acl.defaclobjtype = 'f'
          AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.aclexplode(
                default_acl.defaclacl
              ) AS default_privilege
             WHERE default_privilege.grantee = 0
               AND default_privilege.privilege_type = 'EXECUTE'
          )
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 default ACL mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS control_table
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(control_table.relacl) AS table_acl
        WHERE control_table.oid IN (admission_table, fence_table)
          AND table_acl.grantee = 0
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS control_function
         CROSS JOIN LATERAL
           pg_catalog.aclexplode(control_function.proacl) AS function_acl
        WHERE control_function.oid IN (
          finalizer_function,
          fence_function,
          fence_trigger_function,
          person_blocked_function
        )
          AND function_acl.grantee = 0
     ) THEN
    RAISE EXCEPTION 'identity finalizer E3 PUBLIC ACL mismatch'
      USING ERRCODE = '42501';
  END IF;
END
$identity_finalizer_e3_acl$;

-- E4 remains an unactivated semantic-authority cutover boundary. Always
-- quarantine both dedicated roles and the exact E4 control objects before
-- inspecting revision state. This statement commits independently, so a
-- partial or future object set cannot retain stale authority when the
-- following catalog admission fails.
DO $identity_cutover_e4_quarantine$
DECLARE
  control_columns text;
  control_name text;
  control_table regclass;
  function_oid regprocedure;
  grantee_sql text;
  target_role text;
  target_table record;
  type_entry record;
BEGIN
  -- Quarantine the exact E4 controls from every extant role even if the
  -- additive role ceremony was omitted or only partially completed.
  function_oid := pg_catalog.to_regprocedure(
    'operations.commit_reviewed_identity_cutover(bytea,uuid)'
  );
  IF function_oid IS NOT NULL THEN
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
      UNION ALL SELECT 'PUBLIC'
    LOOP
      grantee_sql := CASE WHEN target_role = 'PUBLIC'
        THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END;
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s CASCADE',
        function_oid, grantee_sql
      );
    END LOOP;
  END IF;

  FOREACH control_name IN ARRAY ARRAY[
    'operations.enforced_legacy_identity_writer_freezes',
    'operations.reviewed_identity_cutover_admissions',
    'operations.semantic_authority_promotions'
  ]::text[]
  LOOP
    control_table := pg_catalog.to_regclass(control_name);
    IF control_table IS NULL THEN
      CONTINUE;
    END IF;
    SELECT pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           )
      INTO STRICT control_columns
      FROM pg_catalog.pg_attribute AS attribute
     WHERE attribute.attrelid = control_table
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped;
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname <> 'home_agent_owner'
      UNION ALL SELECT 'PUBLIC'
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %s FROM %s CASCADE',
        control_table,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$s FROM %3$s CASCADE',
        control_columns,
        control_table,
        CASE WHEN target_role = 'PUBLIC'
          THEN 'PUBLIC' ELSE pg_catalog.quote_ident(target_role) END
      );
    END LOOP;
  END LOOP;

  -- Remove every generic application capability from whichever members of
  -- the additive pair exist. The following admission statement rejects a
  -- missing or partial pair only after this quarantine commits.
  FOR target_table IN
    SELECT table_namespace.nspname,
           candidate_table.relname,
           pg_catalog.string_agg(
             pg_catalog.quote_ident(attribute.attname), ', '
             ORDER BY attribute.attnum
           ) AS column_list
      FROM pg_catalog.pg_class AS candidate_table
      JOIN pg_catalog.pg_namespace AS table_namespace
        ON table_namespace.oid = candidate_table.relnamespace
      JOIN pg_catalog.pg_attribute AS attribute
        ON attribute.attrelid = candidate_table.oid
       AND attribute.attnum > 0
       AND NOT attribute.attisdropped
     WHERE table_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
       AND candidate_table.relkind IN ('r','p','v','m','f')
     GROUP BY table_namespace.nspname, candidate_table.relname
  LOOP
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname IN (
         'home_agent_identity_cutover',
         'home_agent_identity_cutover_kernel'
       )
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I CASCADE',
        target_table.nspname, target_table.relname, target_role
      );
      EXECUTE pg_catalog.format(
        'REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s), '
        'REFERENCES (%1$s) ON TABLE %2$I.%3$I FROM %4$I CASCADE',
        target_table.column_list, target_table.nspname,
        target_table.relname, target_role
      );
    END LOOP;
  END LOOP;

  FOR target_role IN
    SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname IN (
       'home_agent_identity_cutover',
       'home_agent_identity_cutover_kernel'
     )
  LOOP
    EXECUTE pg_catalog.format(
      'REVOKE USAGE, CREATE ON SCHEMA public, ingest, identity, knowledge, '
      'engagement, privacy, operations, media FROM %I CASCADE',
      target_role
    );
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, ingest, '
      'identity, knowledge, engagement, privacy, operations, media FROM %I '
      'CASCADE',
      target_role
    );
    EXECUTE pg_catalog.format(
      'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public, ingest, '
      'identity, knowledge, engagement, privacy, operations, media FROM %I '
      'CASCADE',
      target_role
    );
  END LOOP;
  FOR type_entry IN
    SELECT type_namespace.nspname, candidate_type.typname
      FROM pg_catalog.pg_type AS candidate_type
      JOIN pg_catalog.pg_namespace AS type_namespace
        ON type_namespace.oid = candidate_type.typnamespace
     WHERE type_namespace.nspname IN (
       'public','ingest','identity','knowledge','engagement','privacy',
       'operations','media'
     )
       AND candidate_type.typisdefined
       AND candidate_type.typrelid = 0
       AND candidate_type.typelem = 0
  LOOP
    FOR target_role IN
      SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
       WHERE role_row.rolname IN (
         'home_agent_identity_cutover',
         'home_agent_identity_cutover_kernel'
       )
    LOOP
      EXECUTE pg_catalog.format(
        'REVOKE USAGE ON TYPE %I.%I FROM %I CASCADE',
        type_entry.nspname, type_entry.typname, target_role
      );
    END LOOP;
  END LOOP;
  FOR target_role IN
    SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname IN (
       'home_agent_identity_cutover',
       'home_agent_identity_cutover_kernel'
     )
  LOOP
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, '
      'ingest, identity, knowledge, engagement, privacy, operations, media '
      'REVOKE ALL PRIVILEGES ON TABLES FROM %I CASCADE',
      target_role
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, '
      'ingest, identity, knowledge, engagement, privacy, operations, media '
      'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I CASCADE',
      target_role
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, '
      'ingest, identity, knowledge, engagement, privacy, operations, media '
      'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %I CASCADE',
      target_role
    );
    EXECUTE pg_catalog.format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, '
      'ingest, identity, knowledge, engagement, privacy, operations, media '
      'REVOKE ALL PRIVILEGES ON TYPES FROM %I CASCADE',
      target_role
    );
  END LOOP;
END
$identity_cutover_e4_quarantine$;

-- The migration-owned E4 catalog manifest is intentionally not guessed. At
-- revisions through 0013 this validates a zero-authority dormant role pair.
-- If 0014 objects appear, replay reports the observed catalog digest and
-- remains quarantined until a reviewed change replaces the explicit pending
-- value and adds only the exact positive ACLs required by the E4 kernel.
DO $identity_cutover_e4_acl$
DECLARE
  admission_table regclass := pg_catalog.to_regclass(
    'operations.reviewed_identity_cutover_admissions'
  );
  actual_e4_catalog_sha256 text;
  current_revision text;
  cutover_function regprocedure := pg_catalog.to_regprocedure(
    'operations.commit_reviewed_identity_cutover(bytea,uuid)'
  );
  cutover_oid oid;
  database_oid oid;
  expected_e4_catalog_sha256 constant text :=
    'PENDING_E4_CATALOG_SHA256';
  freeze_table regclass := pg_catalog.to_regclass(
    'operations.enforced_legacy_identity_writer_freezes'
  );
  kernel_oid oid;
  object_count integer;
  owner_oid oid;
  promotion_table regclass := pg_catalog.to_regclass(
    'operations.semantic_authority_promotions'
  );
  pre_e4_revisions constant text[] := ARRAY[
    '0001_greenfield_core',
    '0002_people_privacy_cutover',
    '0003_resource_budgets',
    '0004_rollout_authorizations',
    '0005_principal_binding_proposals',
    '0006_worker_maintenance_health',
    '0006a_worker_lease_arbitration',
    '0007_phase3_identity_authority',
    '0008_identity_migration_kernel',
    '0009_identity_finalizer_base',
    '0010_identity_erasure_source',
    '0011_identity_erasure_e1',
    '0012_identity_erasure_e2',
    '0013_identity_finalizer_e3'
  ]::text[];
  role_count integer;
BEGIN
  SELECT version_num INTO STRICT current_revision
    FROM public.alembic_version;
  object_count :=
    (admission_table IS NOT NULL)::integer +
    (cutover_function IS NOT NULL)::integer +
    (freeze_table IS NOT NULL)::integer +
    (promotion_table IS NOT NULL)::integer;
  SELECT pg_catalog.count(*) INTO STRICT role_count
    FROM pg_catalog.pg_roles
   WHERE rolname IN (
     'home_agent_identity_cutover',
     'home_agent_identity_cutover_kernel'
   );
  IF role_count = 0 THEN
    IF object_count = 0 AND current_revision = ANY (pre_e4_revisions) THEN
      RETURN;
    END IF;
    RAISE EXCEPTION 'identity cutover E4 role ceremony was omitted'
      USING ERRCODE = '55000';
  ELSIF role_count <> 2 THEN
    RAISE EXCEPTION 'partial identity cutover E4 role pair'
      USING ERRCODE = '55000';
  END IF;
  SELECT oid INTO STRICT owner_oid
    FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_owner';
  SELECT oid INTO STRICT cutover_oid
    FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_identity_cutover';
  SELECT oid INTO STRICT kernel_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_cutover_kernel';
  SELECT oid INTO STRICT database_oid
    FROM pg_catalog.pg_database
   WHERE datname = pg_catalog.current_database();

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS login_role
        WHERE login_role.oid = cutover_oid
          AND login_role.rolcanlogin
          AND NOT login_role.rolinherit
          AND NOT login_role.rolsuper
          AND NOT login_role.rolcreatedb
          AND NOT login_role.rolcreaterole
          AND NOT login_role.rolreplication
          AND NOT login_role.rolbypassrls
          AND login_role.rolconnlimit = 1
          AND login_role.rolvaliduntil =
              timestamptz '1970-01-01 00:00:00+00'
          AND login_role.rolconfig = ARRAY[
            'default_transaction_isolation=serializable',
            'statement_timeout=120s',
            'lock_timeout=5s',
            'idle_in_transaction_session_timeout=15s',
            'transaction_timeout=180s',
            'log_parameter_max_length_on_error=0'
          ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS kernel_role
        WHERE kernel_role.oid = kernel_oid
          AND NOT kernel_role.rolcanlogin
          AND NOT kernel_role.rolinherit
          AND NOT kernel_role.rolsuper
          AND NOT kernel_role.rolcreatedb
          AND NOT kernel_role.rolcreaterole
          AND NOT kernel_role.rolreplication
          AND NOT kernel_role.rolbypassrls
          AND kernel_role.rolconnlimit = 0
          AND kernel_role.rolvaliduntil IS NULL
          AND kernel_role.rolconfig IS NULL
     )
     OR NOT pg_catalog.has_database_privilege(
       cutover_oid, database_oid, 'CONNECT'
     )
     OR pg_catalog.has_database_privilege(
       cutover_oid, database_oid, 'CREATE,TEMPORARY'
     )
     OR pg_catalog.has_database_privilege(
       kernel_oid, database_oid, 'CONNECT,CREATE,TEMPORARY'
     )
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = cutover_oid
           OR membership.roleid = cutover_oid
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = kernel_oid
          AND membership.member = owner_oid
          AND NOT membership.admin_option
          AND NOT membership.inherit_option
          AND membership.set_option
     ) <> 1
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = kernel_oid
     ) <> 1
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_db_role_setting AS database_setting
        WHERE database_setting.setrole IN (cutover_oid, kernel_oid)
          AND database_setting.setdatabase <> 0
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS login_ownership
        WHERE login_ownership.refobjid = cutover_oid
          AND login_ownership.deptype = 'o'
     ) THEN
    RAISE EXCEPTION 'identity cutover E4 dormant role contract mismatch'
      USING ERRCODE = '42501';
  END IF;

  IF object_count = 0 AND current_revision = ANY (pre_e4_revisions) THEN
    IF EXISTS (
         SELECT 1
           FROM pg_catalog.pg_shdepend AS kernel_ownership
          WHERE kernel_ownership.refobjid = kernel_oid
            AND kernel_ownership.deptype = 'o'
       ) THEN
      RAISE EXCEPTION 'identity cutover E4 pre-migration ownership mismatch'
        USING ERRCODE = '42501';
    END IF;
    RETURN;
  END IF;

  IF object_count <> 4
     OR current_revision <> '0014_identity_cutover_e4' THEN
    RAISE EXCEPTION 'partial or revision-mismatched identity cutover E4 object set'
      USING ERRCODE = '55000',
            DETAIL = pg_catalog.format(
              'revision=%s objects=%s', current_revision, object_count
            );
  END IF;

  -- Hash the post-quarantine catalog, not the migration's temporarily
  -- callable state. A later activation needs a distinct reviewed manifest.
  -- The manifest includes ownership/RLS, normalized policies and ACLs, plus
  -- every direct application-catalog grant to either dormant E4 role.
  SELECT pg_catalog.encode(
           pg_catalog.sha256(
             pg_catalog.convert_to(
               pg_catalog.jsonb_build_object(
                 'relations',
                 (
                   SELECT pg_catalog.jsonb_agg(
                            pg_catalog.jsonb_build_array(
                              target.target_name,
                              pg_catalog.jsonb_build_object(
                                'owner',
                                relation_row.relowner::regrole::text,
                                'kind',
                                relation_row.relkind::text,
                                'persistence',
                                relation_row.relpersistence::text,
                                'row_security',
                                relation_row.relrowsecurity,
                                'force_row_security',
                                relation_row.relforcerowsecurity,
                                'columns',
                                (
                                  SELECT pg_catalog.jsonb_agg(
                                           pg_catalog.jsonb_build_array(
                                             attribute.attname,
                                             pg_catalog.format_type(
                                               attribute.atttypid,
                                               attribute.atttypmod
                                             ),
                                             attribute.attnotnull,
                                             coalesce(
                                               pg_catalog.pg_get_expr(
                                                 default_row.adbin,
                                                 default_row.adrelid,
                                                 true
                                               ),
                                               ''
                                             )
                                           )
                                           ORDER BY attribute.attnum
                                         )
                                    FROM pg_catalog.pg_attribute AS attribute
                                    LEFT JOIN pg_catalog.pg_attrdef AS default_row
                                      ON default_row.adrelid =
                                           attribute.attrelid
                                     AND default_row.adnum = attribute.attnum
                                   WHERE attribute.attrelid =
                                         target.target_relation
                                     AND attribute.attnum > 0
                                     AND NOT attribute.attisdropped
                                ),
                                 'constraints',
                                 (
                                  SELECT coalesce(
                                           pg_catalog.jsonb_agg(
                                             pg_catalog.jsonb_build_array(
                                               constraint_row.conname,
                                               constraint_row.contype::text,
                                               pg_catalog.pg_get_constraintdef(
                                                 constraint_row.oid, true
                                               )
                                             )
                                             ORDER BY constraint_row.conname
                                           ),
                                           '[]'::jsonb
                                         )
                                    FROM pg_catalog.pg_constraint
                                         AS constraint_row
                                    WHERE constraint_row.conrelid =
                                          target.target_relation
                                 ),
                                 'user_triggers',
                                 (
                                   SELECT coalesce(
                                            pg_catalog.jsonb_agg(
                                              pg_catalog.jsonb_build_array(
                                                trigger_row.tgname,
                                                trigger_row.tgenabled::text,
                                                trigger_row.tgtype,
                                                trigger_row.tgfoid
                                                  ::regprocedure::text,
                                                pg_catalog.pg_get_triggerdef(
                                                  trigger_row.oid, true
                                                )
                                              )
                                              ORDER BY trigger_row.tgname
                                            ),
                                            '[]'::jsonb
                                          )
                                     FROM pg_catalog.pg_trigger AS trigger_row
                                    WHERE trigger_row.tgrelid =
                                          target.target_relation
                                      AND NOT trigger_row.tgisinternal
                                 ),
                                 'rules',
                                 (
                                   SELECT coalesce(
                                            pg_catalog.jsonb_agg(
                                              pg_catalog.jsonb_build_array(
                                                rule_row.rulename,
                                                rule_row.ev_type::text,
                                                rule_row.ev_enabled::text,
                                                rule_row.is_instead,
                                                pg_catalog.pg_get_ruledef(
                                                  rule_row.oid, true
                                                )
                                              )
                                              ORDER BY rule_row.rulename
                                            ),
                                            '[]'::jsonb
                                          )
                                     FROM pg_catalog.pg_rewrite AS rule_row
                                    WHERE rule_row.ev_class =
                                          target.target_relation
                                 ),
                                 'table_acl',
                                (
                                  SELECT coalesce(
                                           pg_catalog.jsonb_agg(
                                             pg_catalog.jsonb_build_array(
                                               CASE
                                                 WHEN relation_acl.grantee = 0
                                                 THEN 'PUBLIC'
                                                 ELSE
                                                   relation_acl.grantee
                                                     ::regrole::text
                                               END,
                                               relation_acl.grantor
                                                 ::regrole::text,
                                               relation_acl.privilege_type,
                                               relation_acl.is_grantable
                                             )
                                             ORDER BY
                                               relation_acl.grantee,
                                               relation_acl.grantor,
                                               relation_acl.privilege_type,
                                               relation_acl.is_grantable
                                           ),
                                           '[]'::jsonb
                                         )
                                    FROM pg_catalog.aclexplode(
                                           coalesce(
                                             relation_row.relacl,
                                             pg_catalog.acldefault(
                                               'r',
                                               relation_row.relowner
                                             )
                                           )
                                         ) AS relation_acl
                                ),
                                'column_acl',
                                (
                                  SELECT coalesce(
                                           pg_catalog.jsonb_agg(
                                             pg_catalog.jsonb_build_array(
                                               attribute.attname,
                                               CASE
                                                 WHEN column_acl.grantee = 0
                                                 THEN 'PUBLIC'
                                                 ELSE
                                                   column_acl.grantee
                                                     ::regrole::text
                                               END,
                                               column_acl.grantor
                                                 ::regrole::text,
                                               column_acl.privilege_type,
                                               column_acl.is_grantable
                                             )
                                             ORDER BY
                                               attribute.attnum,
                                               column_acl.grantee,
                                               column_acl.grantor,
                                               column_acl.privilege_type,
                                               column_acl.is_grantable
                                           ),
                                           '[]'::jsonb
                                         )
                                    FROM pg_catalog.pg_attribute AS attribute
                                    CROSS JOIN LATERAL
                                      pg_catalog.aclexplode(attribute.attacl)
                                        AS column_acl
                                   WHERE attribute.attrelid =
                                         target.target_relation
                                     AND attribute.attnum > 0
                                     AND NOT attribute.attisdropped
                                )
                              )
                            )
                            ORDER BY target.target_name
                          )
                     FROM (
                       VALUES
                         (
                           'operations.enforced_legacy_identity_writer_freezes',
                           freeze_table
                         ),
                         (
                           'operations.reviewed_identity_cutover_admissions',
                           admission_table
                         ),
                         (
                           'operations.semantic_authority_promotions',
                           promotion_table
                         )
                     ) AS target(target_name, target_relation)
                     JOIN pg_catalog.pg_class AS relation_row
                       ON relation_row.oid = target.target_relation
                 ),
                 'policies',
                 (
                   SELECT coalesce(
                            pg_catalog.jsonb_agg(
                              pg_catalog.jsonb_build_array(
                                pg_catalog.format(
                                  '%I.%I',
                                  policy_namespace.nspname,
                                  policy_relation.relname
                                ),
                                policy_row.polname,
                                policy_row.polcmd::text,
                                policy_row.polpermissive,
                                (
                                  SELECT pg_catalog.jsonb_agg(
                                           CASE
                                             WHEN policy_role.role_oid = 0
                                             THEN 'PUBLIC'
                                             ELSE
                                               policy_role.role_oid
                                                 ::regrole::text
                                           END
                                           ORDER BY policy_role.role_oid
                                         )
                                    FROM pg_catalog.unnest(
                                           policy_row.polroles
                                         ) AS policy_role(role_oid)
                                ),
                                coalesce(
                                  pg_catalog.pg_get_expr(
                                    policy_row.polqual,
                                    policy_row.polrelid,
                                    true
                                  ),
                                  ''
                                ),
                                coalesce(
                                  pg_catalog.pg_get_expr(
                                    policy_row.polwithcheck,
                                    policy_row.polrelid,
                                    true
                                  ),
                                  ''
                                )
                              )
                              ORDER BY
                                policy_namespace.nspname,
                                policy_relation.relname,
                                policy_row.polname,
                                policy_row.polcmd
                            ),
                            '[]'::jsonb
                          )
                     FROM pg_catalog.pg_policy AS policy_row
                     JOIN pg_catalog.pg_class AS policy_relation
                       ON policy_relation.oid = policy_row.polrelid
                     JOIN pg_catalog.pg_namespace AS policy_namespace
                       ON policy_namespace.oid =
                            policy_relation.relnamespace
                    WHERE policy_row.polrelid IN (
                            freeze_table,
                            admission_table,
                            promotion_table
                          )
                       OR (
                            policy_row.polname IN (
                              'identity_cutover_e4_select',
                              'identity_cutover_e4_insert',
                              'identity_cutover_e4_update'
                            )
                            AND policy_namespace.nspname IN (
                              'identity',
                              'knowledge',
                              'operations',
                              'privacy'
                            )
                          )
                 ),
                 'function',
                 (
                   SELECT pg_catalog.jsonb_build_object(
                            'identity',
                            function_row.oid::regprocedure::text,
                            'owner',
                            function_row.proowner::regrole::text,
                            'language',
                            function_language.lanname,
                            'returns',
                            pg_catalog.format_type(
                              function_row.prorettype, NULL
                            ),
                            'security_definer',
                            function_row.prosecdef,
                            'volatility',
                            function_row.provolatile::text,
                            'configuration',
                            function_row.proconfig,
                            'body_sha256',
                            pg_catalog.encode(
                              pg_catalog.sha256(
                                pg_catalog.convert_to(
                                  function_row.prosrc, 'UTF8'
                                )
                              ),
                              'hex'
                            ),
                            'acl',
                            (
                              SELECT coalesce(
                                       pg_catalog.jsonb_agg(
                                         pg_catalog.jsonb_build_array(
                                           CASE
                                             WHEN function_acl.grantee = 0
                                             THEN 'PUBLIC'
                                             ELSE
                                               function_acl.grantee
                                                 ::regrole::text
                                           END,
                                           function_acl.grantor
                                             ::regrole::text,
                                           function_acl.privilege_type,
                                           function_acl.is_grantable
                                         )
                                         ORDER BY
                                           function_acl.grantee,
                                           function_acl.grantor,
                                           function_acl.privilege_type,
                                           function_acl.is_grantable
                                       ),
                                       '[]'::jsonb
                                     )
                                FROM pg_catalog.aclexplode(
                                       coalesce(
                                         function_row.proacl,
                                         pg_catalog.acldefault(
                                           'f', function_row.proowner
                                         )
                                       )
                                     ) AS function_acl
                            )
                          )
                     FROM pg_catalog.pg_proc AS function_row
                     JOIN pg_catalog.pg_language AS function_language
                       ON function_language.oid = function_row.prolang
                    WHERE function_row.oid = cutover_function
                 ),
                 'role_acl_inventory',
                 (
                   SELECT coalesce(
                            pg_catalog.jsonb_agg(
                              pg_catalog.jsonb_build_array(
                                direct_acl.object_kind,
                                direct_acl.object_identity,
                                direct_acl.grantee_name,
                                direct_acl.grantor_name,
                                direct_acl.privilege_type,
                                direct_acl.is_grantable
                              )
                              ORDER BY
                                direct_acl.object_kind,
                                direct_acl.object_identity,
                                direct_acl.grantee_name,
                                direct_acl.grantor_name,
                                direct_acl.privilege_type,
                                direct_acl.is_grantable
                            ),
                            '[]'::jsonb
                          )
                     FROM (
                       SELECT
                         'database'::text AS object_kind,
                         database_row.datname::text AS object_identity,
                         granted_role.rolname::text AS grantee_name,
                         database_acl.grantor::regrole::text
                           AS grantor_name,
                         database_acl.privilege_type::text,
                         database_acl.is_grantable
                       FROM pg_catalog.pg_database AS database_row
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         database_row.datacl
                       ) AS database_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = database_acl.grantee
                      WHERE database_row.datname =
                            pg_catalog.current_database()
                        AND granted_role.rolname IN (
                          'home_agent_identity_cutover',
                          'home_agent_identity_cutover_kernel'
                        )
                       UNION ALL
                       SELECT
                         'schema',
                         namespace_row.nspname,
                         granted_role.rolname,
                         schema_acl.grantor::regrole::text,
                         schema_acl.privilege_type,
                         schema_acl.is_grantable
                       FROM pg_catalog.pg_namespace AS namespace_row
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         namespace_row.nspacl
                       ) AS schema_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = schema_acl.grantee
                      WHERE namespace_row.nspname IN (
                              'public','ingest','identity','knowledge',
                              'engagement','privacy','operations','media'
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'relation',
                         pg_catalog.format(
                           '%I.%I',
                           relation_namespace.nspname,
                           relation_row.relname
                         ),
                         granted_role.rolname,
                         relation_acl.grantor::regrole::text,
                         relation_acl.privilege_type,
                         relation_acl.is_grantable
                       FROM pg_catalog.pg_class AS relation_row
                       JOIN pg_catalog.pg_namespace AS relation_namespace
                         ON relation_namespace.oid =
                              relation_row.relnamespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         relation_row.relacl
                       ) AS relation_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = relation_acl.grantee
                      WHERE relation_namespace.nspname IN (
                              'public','ingest','identity','knowledge',
                              'engagement','privacy','operations','media'
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'column',
                         pg_catalog.format(
                           '%I.%I.%I',
                           column_namespace.nspname,
                           column_relation.relname,
                           column_row.attname
                         ),
                         granted_role.rolname,
                         column_acl.grantor::regrole::text,
                         column_acl.privilege_type,
                         column_acl.is_grantable
                       FROM pg_catalog.pg_attribute AS column_row
                       JOIN pg_catalog.pg_class AS column_relation
                         ON column_relation.oid = column_row.attrelid
                       JOIN pg_catalog.pg_namespace AS column_namespace
                         ON column_namespace.oid =
                              column_relation.relnamespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         column_row.attacl
                       ) AS column_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = column_acl.grantee
                      WHERE column_row.attnum > 0
                        AND NOT column_row.attisdropped
                        AND column_namespace.nspname IN (
                              'public','ingest','identity','knowledge',
                              'engagement','privacy','operations','media'
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'function',
                         function_row.oid::regprocedure::text,
                         granted_role.rolname,
                         function_acl.grantor::regrole::text,
                         function_acl.privilege_type,
                         function_acl.is_grantable
                       FROM pg_catalog.pg_proc AS function_row
                       JOIN pg_catalog.pg_namespace AS function_namespace
                         ON function_namespace.oid =
                              function_row.pronamespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         function_row.proacl
                       ) AS function_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = function_acl.grantee
                      WHERE function_namespace.nspname IN (
                              'public','ingest','identity','knowledge',
                              'engagement','privacy','operations','media'
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'type',
                         pg_catalog.format(
                           '%I.%I',
                           type_namespace.nspname,
                           type_row.typname
                         ),
                         granted_role.rolname,
                         type_acl.grantor::regrole::text,
                         type_acl.privilege_type,
                         type_acl.is_grantable
                       FROM pg_catalog.pg_type AS type_row
                       JOIN pg_catalog.pg_namespace AS type_namespace
                         ON type_namespace.oid = type_row.typnamespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         type_row.typacl
                       ) AS type_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid = type_acl.grantee
                      WHERE type_namespace.nspname IN (
                              'public','ingest','identity','knowledge',
                              'engagement','privacy','operations','media'
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'default_acl',
                         pg_catalog.format(
                           '%s:%s:%s',
                           default_acl.defaclrole::regrole::text,
                           coalesce(
                             default_namespace.nspname,
                             '<all-schemas>'
                           ),
                           default_acl.defaclobjtype::text
                         ),
                         granted_role.rolname,
                         exploded_default_acl.grantor::regrole::text,
                         exploded_default_acl.privilege_type,
                         exploded_default_acl.is_grantable
                       FROM pg_catalog.pg_default_acl AS default_acl
                       LEFT JOIN pg_catalog.pg_namespace AS default_namespace
                         ON default_namespace.oid =
                              default_acl.defaclnamespace
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         default_acl.defaclacl
                       ) AS exploded_default_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid =
                              exploded_default_acl.grantee
                      WHERE (
                              default_acl.defaclnamespace = 0
                              OR default_namespace.nspname IN (
                                'public','ingest','identity','knowledge',
                                'engagement','privacy','operations','media'
                              )
                            )
                        AND granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                       UNION ALL
                       SELECT
                         'parameter',
                         parameter_acl.parname,
                         granted_role.rolname,
                         exploded_parameter_acl.grantor::regrole::text,
                         exploded_parameter_acl.privilege_type,
                         exploded_parameter_acl.is_grantable
                       FROM pg_catalog.pg_parameter_acl AS parameter_acl
                       CROSS JOIN LATERAL pg_catalog.aclexplode(
                         parameter_acl.paracl
                       ) AS exploded_parameter_acl
                       JOIN pg_catalog.pg_roles AS granted_role
                         ON granted_role.oid =
                              exploded_parameter_acl.grantee
                      WHERE granted_role.rolname IN (
                              'home_agent_identity_cutover',
                              'home_agent_identity_cutover_kernel'
                            )
                     ) AS direct_acl
                 ),
                 'dependency_effective_access',
                 (
                   SELECT pg_catalog.jsonb_agg(
                            pg_catalog.jsonb_build_array(
                              effective_access.object_kind,
                              effective_access.object_identity,
                              effective_access.role_name,
                              effective_access.privilege_type,
                              effective_access.is_allowed
                            )
                            ORDER BY
                              effective_access.object_kind,
                              effective_access.object_identity,
                              effective_access.role_name,
                              effective_access.privilege_type
                          )
                     FROM (
                       SELECT
                         'schema'::text AS object_kind,
                         target_schema.schema_name::text
                           AS object_identity,
                         target_role.role_name::text AS role_name,
                         target_privilege.privilege_type::text
                           AS privilege_type,
                         pg_catalog.has_schema_privilege(
                           target_role.role_name,
                           target_schema.schema_name,
                           target_privilege.privilege_type
                         ) AS is_allowed
                       FROM (
                         VALUES
                           ('home_agent_identity_cutover'),
                           ('home_agent_identity_cutover_kernel')
                       ) AS target_role(role_name)
                       CROSS JOIN (
                         VALUES ('operations'), ('privacy')
                       ) AS target_schema(schema_name)
                       CROSS JOIN (
                         VALUES ('USAGE'), ('CREATE')
                       ) AS target_privilege(privilege_type)
                       UNION ALL
                       SELECT
                         'relation',
                         target_relation.relation_name,
                         target_role.role_name,
                         target_privilege.privilege_type,
                         pg_catalog.has_table_privilege(
                           target_role.role_name,
                           pg_catalog.to_regclass(
                             target_relation.relation_name
                           ),
                           target_privilege.privilege_type
                         )
                       FROM (
                         VALUES
                           ('home_agent_identity_cutover'),
                           ('home_agent_identity_cutover_kernel')
                       ) AS target_role(role_name)
                       CROSS JOIN (
                         VALUES
                           (
                             'operations.enforced_legacy_identity_writer_freezes'
                           ),
                           (
                             'operations.reviewed_identity_cutover_admissions'
                           ),
                           (
                             'operations.semantic_authority_promotions'
                           ),
                           (
                             'operations.reviewed_identity_migration_runs'
                           ),
                           (
                             'operations.reviewed_identity_migration_finalizations'
                           ),
                           (
                             'operations.reviewed_identity_finalizer_admissions'
                           ),
                           ('operations.semantic_authority_cutovers'),
                           ('operations.legacy_identity_writer_evidence'),
                           ('operations.privacy_cutover_check_receipts'),
                           (
                             'operations.reviewed_identity_migration_erasure_impacts'
                           ),
                           (
                             'operations.reviewed_identity_migration_projection_lineage'
                           ),
                           (
                             'operations.reviewed_identity_migration_projection_subjects'
                           ),
                           (
                             'operations.erasure_ledger_state'
                           )
                       ) AS target_relation(relation_name)
                       CROSS JOIN (
                         VALUES
                           ('SELECT'),
                           ('INSERT'),
                           ('UPDATE'),
                           ('DELETE'),
                           ('TRUNCATE'),
                           ('REFERENCES'),
                           ('TRIGGER')
                       ) AS target_privilege(privilege_type)
                       UNION ALL
                       SELECT
                         'function',
                         target_function.function_name,
                         target_role.role_name,
                         'EXECUTE',
                         pg_catalog.has_function_privilege(
                           target_role.role_name,
                           pg_catalog.to_regprocedure(
                             target_function.function_name
                           ),
                           'EXECUTE'
                         )
                       FROM (
                         VALUES
                           ('home_agent_identity_cutover'),
                           ('home_agent_identity_cutover_kernel')
                       ) AS target_role(role_name)
                       CROSS JOIN (
                         VALUES
                           (
                             'operations.commit_reviewed_identity_cutover(bytea,uuid)'
                           ),
                           (
                             'privacy.lock_identity_semantic_write_fence()'
                           ),
                           ('privacy.identity_person_is_blocked(uuid)')
                       ) AS target_function(function_name)
                     ) AS effective_access
                 )
               )::text,
               'UTF8'
             )
           ),
           'hex'
         )
    INTO STRICT actual_e4_catalog_sha256;

  IF expected_e4_catalog_sha256 !~ '^[0-9a-f]{64}$'
     OR actual_e4_catalog_sha256 <> expected_e4_catalog_sha256 THEN
    RAISE EXCEPTION
      'identity cutover E4 catalog admission is pending reviewed digest'
      USING ERRCODE = '42501',
            DETAIL = pg_catalog.format(
              'expected=%s actual=%s',
              expected_e4_catalog_sha256,
              actual_e4_catalog_sha256
            );
  END IF;

  -- No positive E4 grants exist until the catalog digest and external legacy
  -- writer-freeze ceremony are reviewed together.
  RAISE EXCEPTION 'identity cutover E4 activation contract is not installed'
    USING ERRCODE = '55000';
END
$identity_cutover_e4_acl$;
SQL

# The broad role setup above supports old pinned revisions and creates the
# default ACL baseline.  Always finish by narrowing online identity writes to
# the exact column-level workflows in the separately testable SQL contract.
psql -v ON_ERROR_STOP=1 -f "$script_dir/identity-api-acl.sql"
