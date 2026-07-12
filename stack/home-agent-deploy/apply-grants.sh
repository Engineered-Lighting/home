#!/bin/sh
set -eu

export PGPASSWORD="$(tr -d '\r\n' < "$POSTGRES_OWNER_PASSWORD_FILE")"
[ -n "$PGPASSWORD" ] || { echo "empty owner password" >&2; exit 78; }

psql -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE ON SCHEMA ingest TO home_agent_ingest, home_agent_api,
  home_agent_worker, home_agent_erasure;
GRANT USAGE ON SCHEMA identity, knowledge, engagement, privacy, operations
  TO home_agent_api, home_agent_worker, home_agent_erasure;
GRANT USAGE ON SCHEMA identity, knowledge, engagement TO home_agent_ingest;
GRANT USAGE ON SCHEMA operations TO home_agent_ingest, home_agent_rollout;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, ingest, identity,
  knowledge, engagement, privacy, operations FROM home_agent_rollout;
GRANT SELECT ON TABLE public.alembic_version
  TO home_agent_api, home_agent_ingest, home_agent_worker, home_agent_erasure,
  home_agent_rollout;

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
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA operations TO home_agent_worker;
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
GRANT SELECT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ingest, identity, knowledge,
  engagement, privacy TO home_agent_erasure;
GRANT INSERT ON TABLE privacy.retrieval_blocks TO home_agent_erasure;
GRANT SELECT, INSERT, UPDATE ON TABLE privacy.erasure_requests
  TO home_agent_erasure;
GRANT INSERT ON TABLE privacy.auto_expiry_receipts TO home_agent_erasure;
GRANT EXECUTE ON FUNCTION privacy.apply_person_auto_expiry(uuid)
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
SQL
