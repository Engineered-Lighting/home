#!/bin/sh
set -eu

export PGPASSWORD="$(tr -d '\r\n' < "$POSTGRES_OWNER_PASSWORD_FILE")"
[ -n "$PGPASSWORD" ] || { echo "empty owner password" >&2; exit 78; }

psql -v ON_ERROR_STOP=1 <<'SQL'
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
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity, knowledge,
  engagement, privacy, operations TO home_agent_api;
-- Principal binding is a two-party workflow. These exact ACLs are repeated
-- after the schema-wide API grant so new default privileges cannot widen the
-- online roles. Subject access is scoped by the HA-user transaction GUC;
-- operator access is scoped by the unforgeable PostgreSQL session_user.
REVOKE ALL ON TABLE identity.principal_binding_requests,
  identity.principal_binding_proposals
  FROM PUBLIC, home_agent_ingest, home_agent_worker, home_agent_erasure,
  home_agent_rollout, home_agent_binding_operator, home_agent_api;
GRANT SELECT, INSERT ON TABLE identity.principal_binding_requests
  TO home_agent_api;
GRANT UPDATE (state, closed_at)
  ON TABLE identity.principal_binding_requests TO home_agent_api;
GRANT SELECT ON TABLE identity.principal_binding_proposals TO home_agent_api;
GRANT UPDATE (
  state, consumed_at, result_principal_id, confirmation_artifact_id
) ON TABLE identity.principal_binding_proposals TO home_agent_api;
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
-- Subject confirmation may create a principal, one confirmation artifact,
-- and one binding. It cannot erase either authority record; governed erasure
-- retains the only DELETE grant. Confirmation artifacts are immutable.
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE identity.confirmation_artifacts
  FROM home_agent_api;
REVOKE DELETE, TRUNCATE ON TABLE identity.ha_user_bindings
  FROM home_agent_api;
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
GRANT EXECUTE ON FUNCTION
  privacy.cancel_principal_binding_work_for_person(uuid,timestamptz)
  TO home_agent_api;
GRANT SELECT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ingest, identity, knowledge,
  engagement, privacy TO home_agent_erasure;
REVOKE INSERT ON TABLE identity.principal_binding_requests,
  identity.principal_binding_proposals FROM home_agent_erasure;
GRANT SELECT, UPDATE, DELETE ON TABLE identity.principal_binding_requests,
  identity.principal_binding_proposals TO home_agent_erasure;
GRANT INSERT ON TABLE privacy.retrieval_blocks TO home_agent_erasure;
GRANT SELECT, INSERT, UPDATE ON TABLE privacy.erasure_requests
  TO home_agent_erasure;
GRANT INSERT ON TABLE privacy.auto_expiry_receipts TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.apply_person_auto_expiry(uuid)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.expire_principal_binding_work(timestamptz)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION
  privacy.cancel_principal_binding_work_for_person(uuid,timestamptz)
  TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.replay_person_auto_expiry(uuid,uuid,uuid)
  TO home_agent_erasure;
GRANT SELECT ON TABLE identity.principals TO home_agent_erasure;
GRANT SELECT, INSERT ON TABLE operations.erasure_replay_receipts
  TO home_agent_erasure;
GRANT SELECT, INSERT, UPDATE ON TABLE operations.erasure_ledger_state
  TO home_agent_erasure;
GRANT SELECT, UPDATE ON TABLE operations.outbox TO home_agent_erasure;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA ingest
  GRANT SELECT, INSERT, UPDATE ON TABLES TO home_agent_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity, knowledge,
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
SQL
