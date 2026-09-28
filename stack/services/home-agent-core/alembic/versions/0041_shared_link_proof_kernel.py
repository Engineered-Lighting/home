"""Dormant issuer-bound challenge proof admission; no callers enabled.

The old 0033 proof function and 0040 issuance entry points stay revision-closed.
Reviewed combined runtime admission remains a separate gate.
"""
from alembic import op

revision = "0041_shared_link_proof_kernel_v1"
down_revision = "0040_shared_link_issue_kernel_v1"
branch_labels = None
depends_on = None
FUNCTION = "identity.issue_shared_link_auth_proof_v1"
SIGNATURE = "uuid,text,text,text,timestamptz,bigint"
KERNEL_ROLE = "home_agent_shared_link_proof_kernel"
ECHO_ROLE = "home_agent_shared_echo_proof_ingress"
VICTORIA_ROLE = "home_agent_shared_victoria_proof_ingress"
ISSUER = f"CASE session_user WHEN '{ECHO_ROLE}' THEN 'home-assistant:echo' WHEN '{VICTORIA_ROLE}' THEN 'home-assistant:victoria' ELSE NULL END"
PAIR = f"session_user IN ('{ECHO_ROLE}','{VICTORIA_ROLE}') AND current_user='{KERNEL_ROLE}'"

READS = {'public.alembic_version': 'version_num',
 'identity.ha_user_bindings': 'binding_id,ha_user_id,principal_id,person_id,revoked_at,confirmed_by_principal_id,proposal_id,source_artifact_id,confirmed_at',
 'identity.principals': 'principal_id,person_id,kind,status',
 'identity.people': 'person_id,status',
 'identity.principal_binding_proposals': 'proposal_id,person_id,result_principal_id,confirmation_artifact_id,consumed_at,ha_user_id,state,request_id,proposal_digest',
 'identity.principal_binding_requests': 'request_id,ha_user_id,state,closed_at',
 'identity.confirmation_artifacts': 'artifact_id,principal_id,purpose,proposal_digest,consumed_at',
 'identity.edge_privacy_user_blocks': 'ha_user_id,person_id',
 'identity.privacy_directives': 'person_id,directive,enabled',
 'privacy.shared_link_session_revocations': 'issuer_id,session_commitment',
 'identity.shared_issuers': 'issuer_id,state,registration_revision',
 'privacy.shared_owner_generations': None,
 'identity.shared_link_ceremonies': None,
 'identity.shared_link_challenges': None,
 'identity.shared_auth_proofs': None}
LOCKS = {'identity.ha_user_bindings': 'binding_id',
 'identity.principals': 'principal_id',
 'identity.people': 'person_id',
 'identity.principal_binding_proposals': 'proposal_id',
 'identity.principal_binding_requests': 'request_id',
 'identity.confirmation_artifacts': 'artifact_id',
 'identity.shared_issuers': 'registration_revision',
 'privacy.shared_owner_generations': 'revision',
 'identity.shared_link_ceremonies': 'revision'}
SUPPRESSION = {'identity.people': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.principals': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.ha_user_bindings': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.principal_binding_proposals': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                         'privacy.identity_principal_is_blocked(result_principal_id)',
 'identity.confirmation_artifacts': 'NOT privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_link_ceremonies': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                    'privacy.identity_principal_is_blocked(principal_id)'}

PROOF_COLUMNS = "proof_id,issuer_id,subject,session_commitment,challenge_commitment,authenticated_at,issued_at,expires_at,registration_revision"
CHILD_UPDATE = "consumed_proof_id,consumed_subject,consumed_at"
HELPERS = (
    "identity.associate_shared_auth_proof_v1(text,uuid,text,text,text,timestamptz,bigint)",
    "identity.require_shared_link_owner_v1(text,uuid,uuid,uuid)",
    "identity.require_shared_link_session_active_v1(text,text)",
    "privacy.lock_identity_semantic_write_fence()",
    "privacy.identity_person_is_blocked(uuid)",
    "privacy.identity_principal_is_blocked(uuid)",
)
BODY = f"""
CREATE FUNCTION {FUNCTION}(p_proof_id uuid,p_subject text,p_session text,
 p_challenge text,p_authenticated_at timestamptz,p_registration_revision bigint)
RETURNS TABLE(proof_id uuid,issuer_id varchar,subject varchar,session_commitment varchar,
 challenge_commitment varchar,authenticated_at timestamptz,issued_at timestamptz,
 expires_at timestamptz,registration_revision bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $kernel$
DECLARE v_issuer text; parent_id uuid; parent_session text; sibling record; receipt record;
BEGIN
 v_issuer := {ISSUER};
 IF v_issuer IS NULL OR current_user<>'{KERNEL_ROLE}' OR
    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT r.rolinherit
      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR
    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit
      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r
      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r
      ON r.oid=m.member WHERE r.rolname=current_user) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid
      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member
      WHERE target.rolname=current_user AND
        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR NOT m.set_option)) THEN
   RAISE EXCEPTION 'shared_link_proof_role_invalid' USING ERRCODE='42501';
 END IF;
 IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR
    pg_catalog.current_setting('transaction_read_only')<>'off' OR pg_catalog.pg_is_in_recovery() OR
    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
   RAISE EXCEPTION 'shared_link_proof_transaction_invalid' USING ERRCODE='25001';
 END IF;
 IF (SELECT count(*) FROM public.alembic_version)<>1 OR
    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE a.version_num='{revision}') THEN
   RAISE EXCEPTION 'shared_link_proof_revision_invalid' USING ERRCODE='55000';
 END IF;
 -- Includes the semantic-write fence before any authority reads. Triggers alone
 -- do not cover an exact proof replay, which performs no insert or update.
 PERFORM identity.require_shared_link_session_active_v1(v_issuer,p_session);
 SELECT parent.ceremony_id,parent.initiating_session_commitment INTO parent_id,parent_session
 FROM identity.shared_link_challenges child
 JOIN identity.shared_link_ceremonies parent ON parent.ceremony_id=child.ceremony_id
 WHERE child.issuer_id=v_issuer AND child.challenge_commitment=p_challenge
   AND child.session_commitment=p_session AND parent.state='pending';
 IF NOT FOUND THEN
   RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',parent_session);
 FOR sibling IN SELECT child.issuer_id,child.session_commitment
   FROM identity.shared_link_challenges child WHERE child.ceremony_id=parent_id
 LOOP
   PERFORM identity.require_shared_link_session_active_v1(sibling.issuer_id,sibling.session_commitment);
 END LOOP;
 SELECT admitted.* INTO STRICT receipt FROM identity.associate_shared_auth_proof_v1(
   v_issuer,p_proof_id,p_subject,p_session,p_challenge,p_authenticated_at,p_registration_revision) admitted;
 -- A unique-index wait during insertion must not turn an expired proof into
 -- a successful call. Raise before returning any receipt, rolling back writes.
 IF receipt.expires_at<=pg_catalog.clock_timestamp() THEN
   RAISE EXCEPTION 'shared_link_challenge_expired' USING ERRCODE='22023';
 END IF;
 RETURN QUERY SELECT receipt.proof_id,receipt.issuer_id,receipt.subject,receipt.session_commitment,
   receipt.challenge_commitment,receipt.authenticated_at,receipt.issued_at,receipt.expires_at,receipt.registration_revision;
END
$kernel$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_link_proof_kernel_migration_authority_invalid';
      END IF;
    END $guard$;""")


def _boundary(table):
    # Sibling sessions must remain visible; only proof and issuer rows are
    # issuer-filtered for SELECT. Child mutation is separately issuer-bound.
    if table in ('identity.shared_auth_proofs', 'identity.shared_issuers'):
        return f"({PAIR}) AND issuer_id=({ISSUER})"
    return PAIR


def upgrade():
    _guard(down_revision)
    op.execute(f"""DO $roles$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='{KERNEL_ROLE}') OR
         (SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname IN ('{ECHO_ROLE}','{VICTORIA_ROLE}'))<>2 THEN
        RAISE EXCEPTION 'shared_link_proof_kernel_role_collision';
      END IF;
    END $roles$;""")
    op.execute(f"CREATE ROLE {KERNEL_ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
               "NOCREATEROLE NOREPLICATION CONNECTION LIMIT 0 VALID UNTIL '1970-01-01';")
    op.execute(f"GRANT {KERNEL_ROLE} TO home_agent_owner WITH ADMIN FALSE,INHERIT FALSE,SET TRUE;")
    op.execute(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {KERNEL_ROLE};")
    for table, columns in READS.items():
        selected = f"SELECT ({columns})" if columns else "SELECT"
        op.execute(f"GRANT {selected} ON {table} TO {KERNEL_ROLE};")
        if table != 'public.alembic_version':
            boundary = _boundary(table)
            op.execute(f"CREATE POLICY shared_link_proof_boundary ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({boundary}) WITH CHECK ({boundary});")
            op.execute(f"CREATE POLICY shared_link_proof_select ON {table} FOR SELECT TO {KERNEL_ROLE} USING ({boundary});")
    for table, column in LOCKS.items():
        boundary = _boundary(table)
        op.execute(f"GRANT UPDATE ({column}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_link_proof_lock ON {table} FOR UPDATE TO {KERNEL_ROLE} USING ({boundary}) WITH CHECK (false);")
        op.execute(f"CREATE POLICY shared_link_proof_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO {KERNEL_ROLE} USING ({boundary}) WITH CHECK (false);")
    child_boundary = f"({PAIR}) AND issuer_id=({ISSUER})"
    op.execute(f"GRANT UPDATE ({CHILD_UPDATE}) ON identity.shared_link_challenges TO {KERNEL_ROLE};")
    for restrictive in ('', 'AS RESTRICTIVE '):
        suffix = 'restrict' if restrictive else 'consume'
        op.execute(f"CREATE POLICY shared_link_proof_{suffix} ON identity.shared_link_challenges {restrictive}FOR UPDATE TO {KERNEL_ROLE} USING ({child_boundary}) WITH CHECK ({child_boundary});")
    op.execute(f"GRANT INSERT ({PROOF_COLUMNS}) ON identity.shared_auth_proofs TO {KERNEL_ROLE};")
    proof_boundary = f"({_boundary('identity.shared_auth_proofs')}) AND registration_revision IS NOT NULL AND consumed_at IS NULL"
    for restrictive in ('', 'AS RESTRICTIVE '):
        suffix = 'insert_restrict' if restrictive else 'insert'
        op.execute(f"CREATE POLICY shared_link_proof_{suffix} ON identity.shared_auth_proofs {restrictive}FOR INSERT TO {KERNEL_ROLE} WITH CHECK ({proof_boundary});")
    for table, expression in SUPPRESSION.items():
        op.execute(f"CREATE POLICY shared_link_proof_erasure ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});")
    for function in HELPERS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {KERNEL_ROLE};")
    op.execute(BODY)
    op.execute(f"GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE};")
    op.execute(f"ALTER FUNCTION {FUNCTION}({SIGNATURE}) OWNER TO {KERNEL_ROLE};")
    op.execute(f"REVOKE CREATE ON SCHEMA identity FROM {KERNEL_ROLE};")
    op.execute(f"SET LOCAL ROLE {KERNEL_ROLE};")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
        CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
        JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid='{FUNCTION}({SIGNATURE})'::regprocedure AND a.grantee<>p.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    op.execute('RESET ROLE;')


def downgrade():
    _guard(revision)
    op.execute(f"SET LOCAL ROLE {KERNEL_ROLE};")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute('RESET ROLE;')
    for function in HELPERS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM {KERNEL_ROLE};")
    for table in SUPPRESSION:
        op.execute(f"DROP POLICY shared_link_proof_erasure ON {table};")
    for suffix in ('insert', 'insert_restrict'):
        op.execute(f"DROP POLICY shared_link_proof_{suffix} ON identity.shared_auth_proofs;")
    op.execute(f"REVOKE INSERT ({PROOF_COLUMNS}) ON identity.shared_auth_proofs FROM {KERNEL_ROLE};")
    for suffix in ('consume', 'restrict'):
        op.execute(f"DROP POLICY shared_link_proof_{suffix} ON identity.shared_link_challenges;")
    op.execute(f"REVOKE UPDATE ({CHILD_UPDATE}) ON identity.shared_link_challenges FROM {KERNEL_ROLE};")
    for table, column in LOCKS.items():
        op.execute(f"DROP POLICY shared_link_proof_no_update ON {table};")
        op.execute(f"DROP POLICY shared_link_proof_lock ON {table};")
        op.execute(f"REVOKE UPDATE ({column}) ON {table} FROM {KERNEL_ROLE};")
    for table, columns in READS.items():
        if table != 'public.alembic_version':
            op.execute(f"DROP POLICY shared_link_proof_boundary ON {table};")
            op.execute(f"DROP POLICY shared_link_proof_select ON {table};")
        selected = f"SELECT ({columns})" if columns else "SELECT"
        op.execute(f"REVOKE {selected} ON {table} FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA identity,privacy,public FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE {KERNEL_ROLE} FROM home_agent_owner;")
    op.execute(f"DROP ROLE {KERNEL_ROLE};")
