"""Atomic challenge/proof association; internal and unprovisioned.

Caller authentication, parent issuance, cancellation, final owner confirmation
and erasure-ledger integration remain separate required transaction boundaries.
The issuer argument is for a trusted kernel, never a browser-supplied identity.
"""
from alembic import op

revision = "0036_shared_link_proof_v1"
down_revision = "0035_shared_link_owner_v1"
branch_labels = None
depends_on = None

FUNCTION = "identity.associate_shared_auth_proof_v1"
SIGNATURE = "text, uuid, text, text, text, timestamptz, bigint"
BODY = f"""
CREATE FUNCTION {FUNCTION}(
    p_issuer text, p_proof_id uuid, p_subject text, p_session text,
    p_challenge text, p_authenticated_at timestamptz, p_registration_revision bigint
) RETURNS TABLE (
    proof_id uuid, issuer_id varchar, subject varchar, session_commitment varchar,
    challenge_commitment varchar, authenticated_at timestamptz, issued_at timestamptz,
    expires_at timestamptz, registration_revision bigint
)
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog
SET row_security = on
AS $association$
DECLARE
    context record;
    existing record;
    echo_subject text;
    checked_at timestamptz;
    expiry timestamptz;
BEGIN
    IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE = '25001';
    END IF;
    IF p_issuer IS NULL OR p_issuer NOT IN ('home-assistant:echo','home-assistant:victoria') OR
       p_proof_id IS NULL OR p_subject IS NULL OR pg_catalog.char_length(p_subject) NOT BETWEEN 1 AND 64 OR
       p_subject <> pg_catalog.btrim(p_subject) OR p_subject ~ '[[:cntrl:]]' OR
       p_subject ~ '^[[:space:]]|[[:space:]]$' OR
       p_session IS NULL OR p_session !~ '^[0-9a-f]{{64}}$' OR
       p_challenge IS NULL OR p_challenge !~ '^[0-9a-f]{{64}}$' OR
       p_authenticated_at IS NULL OR NOT pg_catalog.isfinite(p_authenticated_at) OR
       p_registration_revision IS NULL OR p_registration_revision <= 0 THEN
        RAISE EXCEPTION 'shared_link_proof_input_invalid' USING ERRCODE = '22023';
    END IF;
    PERFORM privacy.lock_identity_semantic_write_fence();
    SELECT child.*, parent.principal_id, parent.person_id, parent.legacy_binding_id,
           parent.created_at AS parent_created_at, parent.expires_at AS parent_expires_at
      INTO context
      FROM identity.shared_link_challenges child
      JOIN identity.shared_link_ceremonies parent ON parent.ceremony_id = child.ceremony_id
      JOIN privacy.shared_owner_generations generation ON generation.owner_commitment = parent.owner_commitment
     WHERE child.challenge_commitment = p_challenge AND child.issuer_id = p_issuer
       AND child.session_commitment = p_session AND child.registration_revision = p_registration_revision
       AND parent.state = 'pending' AND parent.purpose = 'link_echo_victoria'
       AND child.authorization_generation = parent.authorization_generation
       AND generation.authorization_generation = parent.authorization_generation AND generation.state = 'active'
     FOR UPDATE OF generation, parent, child;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM identity.shared_issuers issuer
     WHERE issuer.issuer_id = p_issuer AND issuer.registration_revision = p_registration_revision
       AND issuer.state = 'active' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE = '42501';
    END IF;
    SELECT binding.ha_user_id INTO echo_subject FROM identity.ha_user_bindings binding
     WHERE binding.binding_id = context.legacy_binding_id;
    PERFORM identity.require_shared_link_owner_v1(echo_subject, context.principal_id,
                                                 context.person_id, context.legacy_binding_id);
    IF p_issuer = 'home-assistant:echo' AND p_subject IS DISTINCT FROM echo_subject THEN
        RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE = '42501';
    END IF;
    -- Re-read wall time only after every potentially blocking authority lock.
    checked_at := pg_catalog.clock_timestamp();
    expiry := LEAST(p_authenticated_at + interval '5 minutes', context.expires_at, context.parent_expires_at);
    IF p_authenticated_at < context.created_at OR p_authenticated_at < context.parent_created_at OR
       p_authenticated_at > checked_at OR expiry <= checked_at THEN
        RAISE EXCEPTION 'shared_link_challenge_expired' USING ERRCODE = '22023';
    END IF;
    SELECT proof.* INTO existing FROM identity.shared_auth_proofs proof
     WHERE proof.proof_id = p_proof_id OR proof.challenge_commitment = p_challenge;
    IF FOUND THEN
        IF existing.proof_id IS DISTINCT FROM p_proof_id OR existing.issuer_id IS DISTINCT FROM p_issuer OR
           existing.subject IS DISTINCT FROM p_subject OR existing.session_commitment IS DISTINCT FROM p_session OR
           existing.challenge_commitment IS DISTINCT FROM p_challenge OR
           existing.authenticated_at IS DISTINCT FROM p_authenticated_at OR
           existing.registration_revision IS DISTINCT FROM p_registration_revision OR
           existing.consumed_at IS NOT NULL OR existing.expires_at <= checked_at OR
           existing.expires_at <> expiry OR context.consumed_proof_id IS DISTINCT FROM p_proof_id OR
           context.consumed_subject IS DISTINCT FROM p_subject OR context.consumed_at IS NULL THEN
            RAISE EXCEPTION 'shared_link_proof_conflict' USING ERRCODE = '23505';
        END IF;
    ELSE
        IF context.consumed_proof_id IS NOT NULL THEN
            RAISE EXCEPTION 'shared_link_proof_conflict' USING ERRCODE = '23505';
        END IF;
        INSERT INTO identity.shared_auth_proofs
            (proof_id,issuer_id,subject,session_commitment,challenge_commitment,
             authenticated_at,issued_at,expires_at,registration_revision)
        VALUES (p_proof_id,p_issuer,p_subject,p_session,p_challenge,
                p_authenticated_at,checked_at,expiry,p_registration_revision);
        UPDATE identity.shared_link_challenges child
           SET consumed_proof_id=p_proof_id, consumed_subject=p_subject, consumed_at=checked_at
         WHERE child.challenge_id=context.challenge_id;
    END IF;
    RETURN QUERY SELECT proof.proof_id,proof.issuer_id,proof.subject,proof.session_commitment,
        proof.challenge_commitment,proof.authenticated_at,proof.issued_at,proof.expires_at,proof.registration_revision
      FROM identity.shared_auth_proofs proof WHERE proof.proof_id=p_proof_id;
END
$association$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user <> 'home_agent_owner' OR session_user <> 'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version) <> 1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '{expected}') THEN
        RAISE EXCEPTION 'shared_proof_transaction_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(BODY)
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
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
