"""Atomic final linking transaction, internal and unprovisioned.

The trusted caller must bind the Echo subject/session and owner confirmation
gesture, derive commitments and allocate stable IDs before dispatch. No browser
endpoint, runtime grants or source capability grants are introduced here.
"""
from alembic import op

revision = "0038_shared_link_confirm_v1"
down_revision = "0037_shared_link_session_v1"
branch_labels = None
depends_on = None

FUNCTION = "identity.confirm_shared_link_v1"
SIGNATURE = "uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid"
BODY = f"""
CREATE FUNCTION {FUNCTION}(
 p_ceremony uuid,p_echo_subject text,p_session text,p_expected_revision bigint,
 p_confirmation text,p_digest text,p_proposal uuid,p_receipt uuid,p_link uuid,p_echo_binding uuid,p_victoria_binding uuid
) RETURNS TABLE (link_id uuid,authorization_generation bigint,revision bigint,confirmed_at timestamptz)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET row_security=on
AS $confirm$
DECLARE context record; echo_proof record; victoria_proof record; prior record;
        stamp timestamptz; expiry timestamptz; next_generation bigint;
BEGIN
 IF pg_catalog.current_setting('transaction_isolation') <> 'serializable' THEN
   RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE='25001';
 END IF;
 IF p_ceremony IS NULL OR p_expected_revision IS NULL OR p_expected_revision <= 0 OR
    p_expected_revision >= 9223372036854775807 OR p_confirmation IS NULL OR
    p_confirmation !~ '^[0-9a-f]{{64}}$' OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{{64}}$' OR
    p_proposal IS NULL OR p_receipt IS NULL OR p_link IS NULL OR
    p_echo_binding IS NULL OR p_victoria_binding IS NULL OR p_echo_binding=p_victoria_binding THEN
   RAISE EXCEPTION 'shared_link_confirmation_invalid' USING ERRCODE='22023';
 END IF;
 PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',p_session);
 SELECT parent.*,generation.authorization_generation AS current_generation,generation.state AS generation_state
   INTO context FROM identity.shared_link_ceremonies parent
   JOIN privacy.shared_owner_generations generation ON generation.owner_commitment=parent.owner_commitment
  WHERE parent.ceremony_id=p_ceremony AND parent.initiating_session_commitment=p_session
  FOR UPDATE OF generation,parent;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM identity.require_shared_link_owner_v1(p_echo_subject,context.principal_id,context.person_id,context.legacy_binding_id);
 SELECT proof.*,child.expires_at AS child_expiry,child.created_at AS child_created_at INTO echo_proof
   FROM identity.shared_link_challenges child JOIN identity.shared_auth_proofs proof ON proof.proof_id=child.consumed_proof_id
  WHERE child.ceremony_id=p_ceremony AND child.issuer_id='home-assistant:echo'
  FOR UPDATE OF child,proof;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';
 END IF;
 SELECT proof.*,child.expires_at AS child_expiry,child.created_at AS child_created_at INTO victoria_proof
   FROM identity.shared_link_challenges child JOIN identity.shared_auth_proofs proof ON proof.proof_id=child.consumed_proof_id
  WHERE child.ceremony_id=p_ceremony AND child.issuer_id='home-assistant:victoria'
  FOR UPDATE OF child,proof;
 IF NOT FOUND OR echo_proof.subject IS DISTINCT FROM p_echo_subject THEN
   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM identity.require_shared_link_session_active_v1(echo_proof.issuer_id,echo_proof.session_commitment);
 PERFORM identity.require_shared_link_session_active_v1(victoria_proof.issuer_id,victoria_proof.session_commitment);
 PERFORM 1 FROM identity.shared_issuers issuer WHERE issuer.issuer_id=echo_proof.issuer_id
   AND issuer.state='active' AND issuer.registration_revision=echo_proof.registration_revision FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM identity.shared_issuers issuer WHERE issuer.issuer_id=victoria_proof.issuer_id
   AND issuer.state='active' AND issuer.registration_revision=victoria_proof.registration_revision FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501'; END IF;
 IF context.generation_state <> 'active' OR context.authorization_generation >= 9223372036854775807 THEN
   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';
 END IF;
 next_generation := context.authorization_generation+1;
 IF context.state='consumed' THEN
   SELECT receipt.confirmed_at,link.authorization_generation,link.revision INTO prior
     FROM identity.shared_link_receipts receipt
     JOIN identity.shared_link_proposals proposal ON proposal.proposal_id=receipt.proposal_id
     JOIN identity.shared_owner_links link ON link.receipt_id=receipt.receipt_id
     JOIN identity.shared_subject_bindings eb ON eb.link_id=link.link_id AND eb.issuer_id='home-assistant:echo'
     JOIN identity.shared_subject_bindings vb ON vb.link_id=link.link_id AND vb.issuer_id='home-assistant:victoria'
    WHERE receipt.receipt_id=p_receipt AND context.confirmation_receipt_id=p_receipt
      AND receipt.confirmation_commitment=p_confirmation AND receipt.proposal_digest=p_digest
      AND proposal.proposal_id=p_proposal AND proposal.echo_proof_id=echo_proof.proof_id
      AND proposal.victoria_proof_id=victoria_proof.proof_id
      AND proposal.expected_generation=context.authorization_generation
      AND receipt.principal_id=context.principal_id AND receipt.person_id=context.person_id
      AND link.link_id=p_link AND link.revoked_at IS NULL AND link.authorization_generation=next_generation
      AND eb.binding_id=p_echo_binding AND eb.subject=echo_proof.subject AND eb.revoked_at IS NULL
      AND vb.binding_id=p_victoria_binding AND vb.subject=victoria_proof.subject AND vb.revoked_at IS NULL
      AND echo_proof.consumed_at=receipt.confirmed_at AND victoria_proof.consumed_at=receipt.confirmed_at
      AND context.ended_at=receipt.confirmed_at
    FOR SHARE OF receipt,proposal,link,eb,vb;
   IF NOT FOUND OR context.revision<>p_expected_revision+1 OR context.current_generation<>next_generation THEN
     RAISE EXCEPTION 'shared_link_confirmation_conflict' USING ERRCODE='23505';
   END IF;
   RETURN QUERY SELECT p_link,prior.authorization_generation,prior.revision,prior.confirmed_at;
   RETURN;
 END IF;
 IF context.state<>'pending' OR context.revision<>p_expected_revision OR
    context.current_generation<>context.authorization_generation OR
    echo_proof.consumed_at IS NOT NULL OR victoria_proof.consumed_at IS NOT NULL THEN
   RAISE EXCEPTION 'shared_link_confirmation_conflict' USING ERRCODE='23505';
 END IF;
 stamp := pg_catalog.clock_timestamp();
 expiry := LEAST(context.expires_at,echo_proof.expires_at,victoria_proof.expires_at,
                 echo_proof.child_expiry,victoria_proof.child_expiry);
 IF expiry<=stamp OR echo_proof.issued_at>stamp OR victoria_proof.issued_at>stamp OR
    echo_proof.authenticated_at<GREATEST(context.created_at,echo_proof.child_created_at) OR
    victoria_proof.authenticated_at<GREATEST(context.created_at,victoria_proof.child_created_at) THEN
   RAISE EXCEPTION 'shared_link_confirmation_expired' USING ERRCODE='22023';
 END IF;
 INSERT INTO identity.shared_link_proposals
   (proposal_id,legacy_binding_id,principal_id,person_id,confirmed_by_principal_id,
    echo_proof_id,echo_issuer,echo_subject,victoria_proof_id,victoria_issuer,victoria_subject,
    proposal_digest,expected_generation,created_at,expires_at)
 VALUES (p_proposal,context.legacy_binding_id,context.principal_id,context.person_id,context.principal_id,
    echo_proof.proof_id,echo_proof.issuer_id,echo_proof.subject,victoria_proof.proof_id,victoria_proof.issuer_id,victoria_proof.subject,
    p_digest,context.authorization_generation,stamp,expiry);
 INSERT INTO identity.shared_link_receipts
   (receipt_id,proposal_id,principal_id,person_id,proposal_digest,confirmation_commitment,confirmed_at)
 VALUES (p_receipt,p_proposal,context.principal_id,context.person_id,p_digest,p_confirmation,stamp);
 INSERT INTO identity.shared_owner_links
   (link_id,receipt_id,principal_id,person_id,revision,authorization_generation,created_at)
 VALUES (p_link,p_receipt,context.principal_id,context.person_id,1,next_generation,stamp);
 INSERT INTO identity.shared_subject_bindings (binding_id,link_id,issuer_id,subject,created_at)
 VALUES (p_echo_binding,p_link,echo_proof.issuer_id,echo_proof.subject,stamp),
        (p_victoria_binding,p_link,victoria_proof.issuer_id,victoria_proof.subject,stamp);
 UPDATE identity.shared_auth_proofs proof SET consumed_at=stamp
  WHERE proof.proof_id IN (echo_proof.proof_id,victoria_proof.proof_id);
 UPDATE privacy.shared_owner_generations generation SET authorization_generation=next_generation,
        revision=generation.revision+1,updated_at=stamp WHERE generation.owner_commitment=context.owner_commitment;
 UPDATE identity.shared_link_ceremonies parent SET state='consumed',ended_at=stamp,
        confirmation_receipt_id=p_receipt,revision=parent.revision+1 WHERE parent.ceremony_id=p_ceremony;
 IF pg_catalog.clock_timestamp()>=expiry THEN
   RAISE EXCEPTION 'shared_link_confirmation_expired' USING ERRCODE='22023';
 END IF;
 RETURN QUERY SELECT p_link,next_generation,1::bigint,stamp;
END
$confirm$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_confirmation_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute("""ALTER TABLE identity.shared_link_ceremonies
      ADD COLUMN confirmation_receipt_id uuid UNIQUE REFERENCES identity.shared_link_receipts(receipt_id),
      ADD CONSTRAINT shared_ceremony_confirmation_shape CHECK
        ((state='consumed') = (confirmation_receipt_id IS NOT NULL));""")
    op.execute(BODY)
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT role.rolname FROM pg_catalog.pg_proc proc
        CROSS JOIN LATERAL pg_catalog.aclexplode(proc.proacl) acl
        JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
        WHERE proc.oid='{FUNCTION}({SIGNATURE})'::regprocedure AND acl.grantee<>proc.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")


def downgrade():
    _guard(revision)
    op.execute("SET LOCAL row_security=off;")
    op.execute("LOCK TABLE identity.shared_link_ceremonies IN ACCESS EXCLUSIVE MODE;")
    op.execute("""DO $empty$ BEGIN
      IF EXISTS (SELECT 1 FROM identity.shared_link_ceremonies WHERE confirmation_receipt_id IS NOT NULL) THEN
        RAISE EXCEPTION 'shared_confirmation_downgrade_requires_empty_history' USING ERRCODE='55000';
      END IF;
    END $empty$;""")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute("ALTER TABLE identity.shared_link_ceremonies DROP CONSTRAINT shared_ceremony_confirmation_shape, DROP COLUMN confirmation_receipt_id;")
