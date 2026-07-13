-- Final, replay-safe identity ACL for the online semantic API.
--
-- apply-grants.sh deliberately runs this file after its schema-wide and
-- default grants.  Identity migration projections are never an online API
-- capability: the API may read only the exact current tables needed by its
-- reviewed routes, while its only direct writes create and close a
-- principal-binding request or cancel/expire its reviewed proposal. Authority
-- graph creation remains disabled until an atomic database kernel exists.
-- This table-level SELECT list remains a service boundary, not a claim of
-- per-principal database isolation; completing RLS review for every readable
-- table is a blocker before broader semantic retrieval is enabled.

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA identity FROM home_agent_api;

-- Future identity tables inherit no API access.  A new online identity read or
-- write therefore requires an explicit review and grant in this file.
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
  REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_api;

GRANT SELECT ON TABLE
  identity.people,
  identity.principals,
  identity.confirmation_artifacts,
  identity.ha_user_bindings,
  identity.principal_binding_requests,
  identity.principal_binding_proposals,
  identity.edge_privacy_user_blocks,
  identity.privacy_directives
TO home_agent_api;

-- An authenticated HA subject may allocate and close only its own request;
-- FORCE RLS and app.ha_user_id provide the row boundary.
GRANT INSERT (
  request_id, ha_user_id, review_code, state, requested_at, expires_at
) ON TABLE identity.principal_binding_requests TO home_agent_api;
GRANT UPDATE (state, closed_at)
  ON TABLE identity.principal_binding_requests TO home_agent_api;

-- Proposal creation remains exclusive to home_agent_binding_operator.  Until
-- the atomic confirmation kernel exists, the subject can only cancel or
-- expire the immutable reviewed proposal; it cannot consume it.
GRANT UPDATE (state)
  ON TABLE identity.principal_binding_proposals TO home_agent_api;

-- Governed preference opt-outs, place/descriptor confirmation, correction,
-- and retraction mint this immutable, FORCE-RLS-scoped receipt. It cannot
-- create a principal, HA binding, or source binding by itself, and the API
-- receives no UPDATE or DELETE authority on the table.
GRANT INSERT (
  artifact_id, principal_id, purpose, proposal_digest, client_nonce_sha256,
  issued_at, expires_at, consumed_at
) ON TABLE identity.confirmation_artifacts TO home_agent_api;

-- This denial-only kernel accepts an arbitrary person UUID and is reserved for
-- the erasure credential.  The subject's own request cancellation uses the
-- row-scoped request/proposal column grants above.
REVOKE EXECUTE ON FUNCTION
  privacy.cancel_principal_binding_work_for_person(uuid,timestamptz)
FROM home_agent_api;
