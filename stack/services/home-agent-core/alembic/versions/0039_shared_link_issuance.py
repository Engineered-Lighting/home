"""Internal ceremony issuance with persistent commitment-key admission.

No admission row, secret, caller role or public endpoint is provisioned. The
trusted coordinator derives owner/request commitments; this function checks the
admitted key identity, live owner and sessions before creating bounded work.
"""
from alembic import op

revision = "0039_shared_link_issue_v1"
down_revision = "0038_shared_link_confirm_v1"
branch_labels = None
depends_on = None

TABLE = "privacy.shared_link_key_admission"
FUNCTION = "identity.begin_shared_link_v1"
SIGNATURE = "uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text"
CREATE_TABLE = f"""CREATE TABLE {TABLE} (
    scope varchar(32) PRIMARY KEY CHECK (scope='shared-link-v1'),
    key_id varchar(64) NOT NULL CHECK (key_id ~ '^[a-z0-9][a-z0-9._-]{{0,63}}$'),
    key_fingerprint varchar(64) NOT NULL CHECK (key_fingerprint ~ '^[0-9a-f]{{64}}$'),
    revision bigint NOT NULL CHECK (revision>0),
    state varchar(16) NOT NULL CHECK (state IN ('active','blocked'))
);"""

BODY = f"""
CREATE FUNCTION {FUNCTION}(
 p_ceremony uuid,p_principal uuid,p_person uuid,p_binding uuid,p_echo_subject text,
 p_owner text,p_request text,p_echo_session text,p_victoria_session text,
 p_echo_child uuid,p_victoria_child uuid,p_echo_challenge text,p_victoria_challenge text,
 p_key_id text,p_key_fingerprint text
) RETURNS TABLE (ceremony_id uuid,authorization_generation bigint,revision bigint,
                 created_at timestamptz,expires_at timestamptz,
                 echo_registration_revision bigint,victoria_registration_revision bigint)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET row_security=on
AS $issue$
DECLARE generation record; prior record; stamp timestamptz;
        echo_revision bigint; victoria_revision bigint;
BEGIN
 IF pg_catalog.current_setting('transaction_isolation')<>'serializable' THEN
   RAISE EXCEPTION 'shared_link_requires_serializable' USING ERRCODE='25001';
 END IF;
 IF p_ceremony IS NULL OR p_principal IS NULL OR p_person IS NULL OR p_binding IS NULL OR
    p_echo_child IS NULL OR p_victoria_child IS NULL OR p_echo_child=p_victoria_child OR
    p_owner IS NULL OR p_owner !~ '^[0-9a-f]{{64}}$' OR
    p_request IS NULL OR p_request !~ '^[0-9a-f]{{64}}$' OR
    p_echo_challenge IS NULL OR p_echo_challenge !~ '^[0-9a-f]{{64}}$' OR
    p_victoria_challenge IS NULL OR p_victoria_challenge !~ '^[0-9a-f]{{64}}$' OR
    p_echo_challenge=p_victoria_challenge OR p_key_id IS NULL OR
    p_key_fingerprint IS NULL OR p_key_fingerprint !~ '^[0-9a-f]{{64}}$' THEN
   RAISE EXCEPTION 'shared_link_issuance_invalid' USING ERRCODE='22023';
 END IF;
 PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',p_echo_session);
 PERFORM identity.require_shared_link_session_active_v1('home-assistant:victoria',p_victoria_session);
 PERFORM identity.require_shared_link_owner_v1(p_echo_subject,p_principal,p_person,p_binding);
 PERFORM 1 FROM {TABLE} admitted WHERE admitted.scope='shared-link-v1' AND admitted.state='active'
   AND admitted.key_id=p_key_id AND admitted.key_fingerprint=p_key_fingerprint FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_key_not_admitted' USING ERRCODE='42501'; END IF;
 SELECT issuer.registration_revision INTO echo_revision FROM identity.shared_issuers issuer
  WHERE issuer.issuer_id='home-assistant:echo' AND issuer.state='active' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING ERRCODE='42501'; END IF;
 SELECT issuer.registration_revision INTO victoria_revision FROM identity.shared_issuers issuer
  WHERE issuer.issuer_id='home-assistant:victoria' AND issuer.state='active' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING ERRCODE='42501'; END IF;
 stamp := pg_catalog.clock_timestamp();
 INSERT INTO privacy.shared_owner_generations (owner_commitment,authorization_generation,revision,state,updated_at)
 VALUES (p_owner,1,1,'active',stamp) ON CONFLICT (owner_commitment) DO NOTHING;
 SELECT counter.* INTO generation FROM privacy.shared_owner_generations counter WHERE counter.owner_commitment=p_owner FOR UPDATE;
 IF NOT FOUND OR generation.state<>'active' THEN RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501'; END IF;
 SELECT parent.* INTO prior FROM identity.shared_link_ceremonies parent
  WHERE parent.ceremony_id=p_ceremony OR (parent.owner_commitment=p_owner AND parent.request_commitment=p_request)
  FOR UPDATE;
 IF FOUND THEN
   IF prior.ceremony_id IS DISTINCT FROM p_ceremony OR prior.owner_commitment IS DISTINCT FROM p_owner OR
      prior.request_commitment IS DISTINCT FROM p_request OR prior.principal_id IS DISTINCT FROM p_principal OR
      prior.person_id IS DISTINCT FROM p_person OR prior.legacy_binding_id IS DISTINCT FROM p_binding OR
      prior.initiating_session_commitment IS DISTINCT FROM p_echo_session OR prior.state<>'pending' OR
      prior.authorization_generation<>generation.authorization_generation THEN
     RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505';
   END IF;
   PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony
     AND child.issuer_id='home-assistant:echo' AND child.challenge_id=p_echo_child
     AND child.session_commitment=p_echo_session AND child.challenge_commitment=p_echo_challenge
     AND child.registration_revision=echo_revision AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at
     FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; END IF;
   PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony
     AND child.issuer_id='home-assistant:victoria' AND child.challenge_id=p_victoria_child
     AND child.session_commitment=p_victoria_session AND child.challenge_commitment=p_victoria_challenge
     AND child.registration_revision=victoria_revision AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at
     FOR SHARE;
   IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; END IF;
   IF prior.expires_at<=pg_catalog.clock_timestamp() THEN
     RAISE EXCEPTION 'shared_link_issuance_expired' USING ERRCODE='22023';
   END IF;
   RETURN QUERY SELECT prior.ceremony_id,prior.authorization_generation,prior.revision,
       prior.created_at,prior.expires_at,echo_revision,victoria_revision;
   RETURN;
 END IF;
 IF EXISTS (SELECT 1 FROM identity.shared_owner_links link WHERE link.person_id=p_person AND link.revoked_at IS NULL) THEN
   RAISE EXCEPTION 'shared_link_owner_already_linked' USING ERRCODE='23505';
 END IF;
 -- Bound live work like the existing BFF ceremony coordinator; retain history.
 IF (SELECT count(*) FROM identity.shared_link_ceremonies parent WHERE parent.owner_commitment=p_owner
       AND parent.state='pending' AND parent.expires_at>pg_catalog.clock_timestamp())>=32 THEN
   RAISE EXCEPTION 'shared_link_issuance_busy' USING ERRCODE='54000';
 END IF;
 stamp := pg_catalog.clock_timestamp();
 INSERT INTO identity.shared_link_ceremonies
   (ceremony_id,owner_commitment,principal_id,person_id,legacy_binding_id,initiating_session_commitment,
    request_commitment,purpose,authorization_generation,revision,state,created_at,expires_at)
 VALUES (p_ceremony,p_owner,p_principal,p_person,p_binding,p_echo_session,p_request,'link_echo_victoria',
    generation.authorization_generation,1,'pending',stamp,stamp+interval '5 minutes');
 INSERT INTO identity.shared_link_challenges
   (challenge_id,ceremony_id,authorization_generation,issuer_id,registration_revision,session_commitment,
    challenge_commitment,created_at,expires_at)
 VALUES (p_echo_child,p_ceremony,generation.authorization_generation,'home-assistant:echo',echo_revision,
    p_echo_session,p_echo_challenge,stamp,stamp+interval '5 minutes'),
   (p_victoria_child,p_ceremony,generation.authorization_generation,'home-assistant:victoria',victoria_revision,
    p_victoria_session,p_victoria_challenge,stamp,stamp+interval '5 minutes');
 IF pg_catalog.clock_timestamp()>=stamp+interval '5 minutes' THEN
   RAISE EXCEPTION 'shared_link_issuance_expired' USING ERRCODE='22023';
 END IF;
 RETURN QUERY SELECT p_ceremony,generation.authorization_generation,1::bigint,stamp,stamp+interval '5 minutes',
                     echo_revision,victoria_revision;
END
$issue$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_issuance_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(CREATE_TABLE)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT role.rolname FROM pg_catalog.pg_class relation
        CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
        JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
        WHERE relation.oid='{TABLE}'::regclass AND acl.grantee<>relation.relowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON TABLE {TABLE} FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
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
    op.execute(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE;")
    op.execute(f"""DO $empty$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE}) THEN
      RAISE EXCEPTION 'shared_issuance_downgrade_requires_empty_key_admission' USING ERRCODE='55000';
    END IF; END $empty$;""")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute(f"DROP TABLE {TABLE};")
