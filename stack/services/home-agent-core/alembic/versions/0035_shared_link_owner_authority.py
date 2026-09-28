"""Internal shared-link owner check; no caller grants or linking endpoint.

This invoker function is a transaction building block, not authentication. A
future governed kernel must supply the Echo subject from trusted ingress and
check ceremony/session/generation/issuer authority in the same transaction.
"""
from alembic import op

revision = "0035_shared_link_owner_v1"
down_revision = "0034_shared_link_challenges_v1"
branch_labels = None
depends_on = None

FUNCTION = "identity.require_shared_link_owner_v1"
SIGNATURE = "text, uuid, uuid, uuid"

BODY = f"""
CREATE FUNCTION {FUNCTION}(
    p_echo_subject text, p_principal_id uuid, p_person_id uuid, p_binding_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog
SET row_security = on
AS $authority$
BEGIN
    IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE = '25001';
    END IF;
    IF p_echo_subject IS NULL OR p_principal_id IS NULL OR p_person_id IS NULL OR
       p_binding_id IS NULL OR pg_catalog.char_length(p_echo_subject) NOT BETWEEN 1 AND 64 OR
       p_echo_subject <> pg_catalog.btrim(p_echo_subject) OR
       p_echo_subject ~ '[[:cntrl:]]' OR p_echo_subject ~ '^[[:space:]]|[[:space:]]$' THEN
        RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE = '42501';
    END IF;
    -- Participate in the existing erasure/semantic-write fence before reads.
    PERFORM privacy.lock_identity_semantic_write_fence();
    PERFORM 1
      FROM identity.ha_user_bindings b
      JOIN identity.principals p ON p.principal_id = b.principal_id AND p.person_id = b.person_id
      JOIN identity.people person ON person.person_id = b.person_id
      JOIN identity.principal_binding_proposals proposal ON proposal.proposal_id = b.proposal_id
      JOIN identity.principal_binding_requests request ON request.request_id = proposal.request_id
      JOIN identity.confirmation_artifacts artifact ON artifact.artifact_id = b.source_artifact_id
     WHERE b.binding_id = p_binding_id AND b.ha_user_id = p_echo_subject
       AND b.principal_id = p_principal_id AND b.person_id = p_person_id
       AND b.revoked_at IS NULL AND b.confirmed_by_principal_id = p_principal_id
       AND p.kind = 'ha_user' AND p.status = 'active' AND person.status = 'active'
       AND proposal.state = 'consumed' AND proposal.ha_user_id = p_echo_subject
       AND proposal.person_id = p_person_id AND proposal.result_principal_id = p_principal_id
       AND proposal.confirmation_artifact_id = b.source_artifact_id
       AND proposal.consumed_at = b.confirmed_at
       AND request.ha_user_id = p_echo_subject AND request.state = 'consumed'
       AND request.closed_at = proposal.consumed_at
       AND artifact.principal_id = p_principal_id
       AND artifact.purpose = 'ha_user_person_binding.confirm'
       AND artifact.proposal_digest = proposal.proposal_digest
       AND artifact.consumed_at = proposal.consumed_at
     FOR SHARE OF b, p, person, proposal, request, artifact;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE = '42501';
    END IF;
    IF privacy.identity_person_is_blocked(p_person_id) OR
       EXISTS (SELECT 1 FROM identity.edge_privacy_user_blocks block
                WHERE block.ha_user_id = p_echo_subject OR block.person_id = p_person_id) OR
       EXISTS (SELECT 1 FROM identity.privacy_directives directive
                WHERE directive.person_id = p_person_id AND directive.enabled
                  AND directive.directive IN ('auto_expire','do_not_track','ignored','silent')) THEN
        -- Scheduled auto-expiry blocks linking immediately, matching CoreStore.
        RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE = '42501';
    END IF;
END
$authority$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version) <> 1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '{expected}') THEN
        RAISE EXCEPTION 'shared_owner_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(BODY)
    # Default function privileges can include named recipients as well as PUBLIC.
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN
        SELECT DISTINCT role.rolname FROM pg_catalog.pg_proc proc
        CROSS JOIN LATERAL pg_catalog.aclexplode(proc.proacl) acl
        JOIN pg_catalog.pg_roles role ON role.oid = acl.grantee
        WHERE proc.oid = '{FUNCTION}({SIGNATURE})'::regprocedure AND acl.grantee <> proc.proowner
      LOOP
        EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM %I', recipient.rolname);
      END LOOP;
    END $acl$;""")


def downgrade():
    _guard(revision)
    # No data mutation and no CASCADE: later dependent kernels must be removed
    # through their own reviewed migrations before this function can be dropped.
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
