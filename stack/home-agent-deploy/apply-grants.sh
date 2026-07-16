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
REVOKE ALL PRIVILEGES ON TABLE
  operations.reviewed_identity_migration_runs,
  operations.reviewed_identity_migration_source_items,
  operations.reviewed_identity_migration_decisions,
  operations.reviewed_identity_migration_item_receipts,
  operations.reviewed_identity_migration_finalizations,
  operations.legacy_identity_writer_evidence,
  operations.privacy_cutover_check_receipts,
  operations.semantic_authority_cutovers,
  operations.reviewed_identity_migration_erasure_impacts
FROM PUBLIC, home_agent_api, home_agent_binding_operator, home_agent_ingest,
  home_agent_worker, home_agent_erasure, home_agent_rollout, home_agent_backup,
  home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;

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
  knowledge, engagement, privacy, operations
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA ingest, identity, knowledge,
  engagement, privacy, operations
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
       'public','ingest','identity','knowledge','engagement','privacy','operations'
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
  privacy, operations
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON TABLES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON SEQUENCES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest, identity,
  knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON FUNCTIONS
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA public, ingest,
  identity, knowledge, engagement, privacy, operations
  REVOKE ALL PRIVILEGES ON TYPES
  FROM home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
REVOKE UPDATE (expires_at) ON TABLE
  operations.reviewed_identity_migration_runs
  FROM home_agent_identity_migration, home_agent_identity_kernel,
  home_agent_identity_finalizer, home_agent_identity_finalizer_kernel;
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
-- role. Runtime roles receive only deterministic suppression predicates, and
-- the restore login receives only the v2 replay entry point.
DO $identity_erasure_e2_acl$
DECLARE
  suppression_functions text[] := ARRAY[
    'privacy.identity_person_is_blocked(uuid)',
    'privacy.identity_principal_is_blocked(uuid)',
    'privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)'
  ]::text[];
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
  all_functions := suppression_functions || trigger_functions
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
      FROM pg_catalog.unnest(suppression_functions)
           AS signature(signature_text)
      JOIN pg_catalog.pg_proc AS function_row
        ON function_row.oid = pg_catalog.to_regprocedure(
          signature.signature_text
        )
     WHERE function_row.proowner <> kernel_oid
        OR NOT function_row.prosecdef
        OR function_row.prokind <> 'f'
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

  GRANT USAGE ON SCHEMA identity, privacy
    TO home_agent_identity_erasure_kernel;
  GRANT SELECT (principal_id, person_id) ON TABLE identity.principals
    TO home_agent_identity_erasure_kernel;
  GRANT SELECT (person_id) ON TABLE privacy.subject_retrieval_blocks
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
  END LOOP;
  function_oid := pg_catalog.to_regprocedure(replay_function);
  EXECUTE pg_catalog.format(
    'GRANT EXECUTE ON FUNCTION %s TO home_agent_erasure',
    function_oid
  );
END
$identity_erasure_e2_acl$;
SQL

# The broad role setup above supports old pinned revisions and creates the
# default ACL baseline.  Always finish by narrowing online identity writes to
# the exact column-level workflows in the separately testable SQL contract.
psql -v ON_ERROR_STOP=1 -f "$script_dir/identity-api-acl.sql"
