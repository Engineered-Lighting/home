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

-- Future identity tables inherit no API access.  A new online identity read or
-- write therefore requires an explicit review and grant in this file.
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
  REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_api;
ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner IN SCHEMA identity
  REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;

GRANT SELECT ON TABLE
  identity.people,
  identity.principals,
  identity.confirmation_artifacts,
  identity.principal_binding_requests,
  identity.principal_binding_proposals,
  identity.edge_privacy_user_blocks,
  identity.privacy_directives
TO home_agent_api;

-- E1 adds an owner-only generated binding-validity support column. Keep the
-- existing authenticated binding read surface without exposing that kernel
-- linkage column through a table-level SELECT grant.
GRANT SELECT (
  binding_id, proposal_id, ha_user_id, principal_id, person_id,
  confirmed_by_principal_id, confirmed_at, revoked_at, source_artifact_id
) ON TABLE identity.ha_user_bindings TO home_agent_api;

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
