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
  home_agent_worker, home_agent_erasure, home_agent_rollout, home_agent_backup;
SQL
