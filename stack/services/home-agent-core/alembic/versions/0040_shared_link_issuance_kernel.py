"""Dormant issuance wrapper; no login or caller EXECUTE is enabled.

The coordinator must authenticate both session boundaries and derive commitments
before calling. This migration supplies SQL containment, not that authentication.
"""
from alembic import op

revision = "0040_shared_link_issue_kernel_v1"
down_revision = "0039_shared_link_issue_v1"
branch_labels = None
depends_on = None
FUNCTION = "identity.issue_shared_link_ceremony_v1"
SIGNATURE = "uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text"
LOOKUP_FUNCTION = "identity.resolve_shared_link_owner_v1"
LOOKUP_SIGNATURE = "text"
RECONCILE_FUNCTION = "identity.inspect_shared_link_issuance_v1"
RECONCILE_SIGNATURE = SIGNATURE
KERNEL_ROLE = "home_agent_shared_link_issue_kernel"
COORDINATOR_ROLE = "home_agent_shared_link_coordinator"
PAIR = f"session_user='{COORDINATOR_ROLE}' AND current_user='{KERNEL_ROLE}'"

# None means the helper selects row.*; otherwise grant the exact read columns.
READS = {
    "public.alembic_version": "version_num",
    "identity.ha_user_bindings": "binding_id,ha_user_id,principal_id,person_id,revoked_at,confirmed_by_principal_id,proposal_id,source_artifact_id,confirmed_at",
    "identity.principals": "principal_id,person_id,kind,status",
    "identity.people": "person_id,status",
    "identity.principal_binding_proposals": "proposal_id,person_id,result_principal_id,confirmation_artifact_id,consumed_at,ha_user_id,state,request_id,proposal_digest",
    "identity.principal_binding_requests": "request_id,ha_user_id,state,closed_at",
    "identity.confirmation_artifacts": "artifact_id,principal_id,purpose,proposal_digest,consumed_at",
    "identity.edge_privacy_user_blocks": "ha_user_id,person_id",
    "identity.privacy_directives": "person_id,directive,enabled",
    "privacy.shared_link_session_revocations": "issuer_id,session_commitment",
    "privacy.shared_link_key_admission": "scope,state,key_id,key_fingerprint",
    "identity.shared_issuers": "issuer_id,state,registration_revision",
    "privacy.shared_owner_generations": None,
    "identity.shared_link_ceremonies": None,
    "identity.shared_link_challenges": "ceremony_id,issuer_id,challenge_id,session_commitment,challenge_commitment,registration_revision,authorization_generation,created_at,expires_at",
    "identity.shared_owner_links": "person_id,revoked_at",
}
LOCKS = {
    "identity.ha_user_bindings": "binding_id", "identity.principals": "principal_id",
    "identity.people": "person_id", "identity.principal_binding_proposals": "proposal_id",
    "identity.principal_binding_requests": "request_id", "identity.confirmation_artifacts": "artifact_id",
    "privacy.shared_link_key_admission": "revision", "identity.shared_issuers": "registration_revision",
    "privacy.shared_owner_generations": "revision", "identity.shared_link_ceremonies": "revision",
    "identity.shared_link_challenges": "challenge_id",
}
INSERTS = {
    "privacy.shared_owner_generations": "owner_commitment,authorization_generation,revision,state,updated_at",
    "identity.shared_link_ceremonies": "ceremony_id,owner_commitment,principal_id,person_id,legacy_binding_id,initiating_session_commitment,request_commitment,purpose,authorization_generation,revision,state,created_at,expires_at",
    "identity.shared_link_challenges": "challenge_id,ceremony_id,authorization_generation,issuer_id,registration_revision,session_commitment,challenge_commitment,created_at,expires_at",
}
SUPPRESSION = {
    "identity.people": "NOT privacy.identity_person_is_blocked(person_id)",
    "identity.principals": "NOT privacy.identity_person_is_blocked(person_id)",
    "identity.ha_user_bindings": "NOT privacy.identity_person_is_blocked(person_id)",
    "identity.principal_binding_proposals": "NOT privacy.identity_person_is_blocked(person_id) AND NOT privacy.identity_principal_is_blocked(result_principal_id)",
    "identity.confirmation_artifacts": "NOT privacy.identity_principal_is_blocked(principal_id)",
    "identity.shared_link_ceremonies": "NOT privacy.identity_person_is_blocked(person_id) AND NOT privacy.identity_principal_is_blocked(principal_id)",
    "identity.shared_owner_links": "NOT privacy.identity_person_is_blocked(person_id)",
}
HELPERS = (
    f"identity.begin_shared_link_v1({SIGNATURE})",
    "identity.require_shared_link_owner_v1(text,uuid,uuid,uuid)",
    "identity.require_shared_link_session_active_v1(text,text)",
    "privacy.lock_identity_semantic_write_fence()",
    "privacy.identity_person_is_blocked(uuid)",
    "privacy.identity_principal_is_blocked(uuid)",
)
BODY = f"""
CREATE FUNCTION {FUNCTION}(
 p_ceremony uuid,p_principal uuid,p_person uuid,p_binding uuid,p_echo_subject text,
 p_owner text,p_request text,p_echo_session text,p_victoria_session text,
 p_echo_child uuid,p_victoria_child uuid,p_echo_challenge text,p_victoria_challenge text,
 p_key_id text,p_key_fingerprint text
) RETURNS TABLE(ceremony_id uuid,authorization_generation bigint,revision bigint,
 created_at timestamptz,expires_at timestamptz,echo_registration_revision bigint,victoria_registration_revision bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $kernel$
BEGIN
 IF session_user<>'{COORDINATOR_ROLE}' OR current_user<>'{KERNEL_ROLE}' OR
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
   RAISE EXCEPTION 'shared_link_issuance_role_invalid' USING ERRCODE='42501';
 END IF;
 IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR
    pg_catalog.current_setting('transaction_read_only')<>'off' OR pg_catalog.pg_is_in_recovery() OR
    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
   RAISE EXCEPTION 'shared_link_issuance_transaction_invalid' USING ERRCODE='25001';
 END IF;
 IF (SELECT count(*) FROM public.alembic_version)<>1 OR
    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE a.version_num='{revision}') THEN
   RAISE EXCEPTION 'shared_link_issuance_revision_invalid' USING ERRCODE='55000';
 END IF;
 RETURN QUERY SELECT issued.* FROM identity.begin_shared_link_v1(
   p_ceremony,p_principal,p_person,p_binding,p_echo_subject,p_owner,p_request,p_echo_session,p_victoria_session,
   p_echo_child,p_victoria_child,p_echo_challenge,p_victoria_challenge,p_key_id,p_key_fingerprint) issued;
END
$kernel$;
"""

# Keep the resolver and issuance entry point on precisely the same boundary.
# This is frozen migration-local SQL, never imported from mutable application code.
BOUNDARY_GUARDS = BODY.split("BEGIN\n", 1)[1].split(" RETURN QUERY", 1)[0]
LOOKUP_BODY = f"""
CREATE FUNCTION {LOOKUP_FUNCTION}(p_echo_subject text)
RETURNS TABLE(principal_id uuid,person_id uuid,legacy_binding_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $lookup$
DECLARE anchor record;
BEGIN
{BOUNDARY_GUARDS}
 IF p_echo_subject IS NULL OR pg_catalog.char_length(p_echo_subject) NOT BETWEEN 1 AND 64 OR
    p_echo_subject<>pg_catalog.btrim(p_echo_subject) OR p_echo_subject ~ '[[:cntrl:]]' OR
    p_echo_subject ~ '^[[:space:]]|[[:space:]]$' THEN
   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM privacy.lock_identity_semantic_write_fence();
 BEGIN
   SELECT b.principal_id,b.person_id,b.binding_id INTO STRICT anchor
     FROM identity.ha_user_bindings b
     JOIN identity.principals p ON p.principal_id=b.principal_id AND p.person_id=b.person_id
     JOIN identity.people person ON person.person_id=b.person_id
    WHERE b.ha_user_id=p_echo_subject AND b.revoked_at IS NULL
      AND p.kind='ha_user' AND p.status='active' AND person.status='active';
 EXCEPTION WHEN NO_DATA_FOUND OR TOO_MANY_ROWS THEN
   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';
 END;
 PERFORM identity.require_shared_link_owner_v1(
   p_echo_subject,anchor.principal_id,anchor.person_id,anchor.binding_id);
 RETURN QUERY SELECT anchor.principal_id,anchor.person_id,anchor.binding_id;
END
$lookup$;
"""

RECONCILE_BODY = f"""
CREATE FUNCTION {RECONCILE_FUNCTION}(
 p_ceremony uuid,p_principal uuid,p_person uuid,p_binding uuid,p_echo_subject text,
 p_owner text,p_request text,p_echo_session text,p_victoria_session text,
 p_echo_child uuid,p_victoria_child uuid,p_echo_challenge text,p_victoria_challenge text,
 p_key_id text,p_key_fingerprint text
) RETURNS TABLE(ceremony_id uuid,authorization_generation bigint,revision bigint,
 created_at timestamptz,expires_at timestamptz,echo_registration_revision bigint,victoria_registration_revision bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $inspect$
DECLARE prior record; generation record; echo_revision bigint; victoria_revision bigint;
BEGIN
{BOUNDARY_GUARDS}
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
 PERFORM 1 FROM privacy.shared_link_key_admission admitted
  WHERE admitted.scope='shared-link-v1' AND admitted.state='active'
    AND admitted.key_id=p_key_id AND admitted.key_fingerprint=p_key_fingerprint FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_key_not_admitted' USING ERRCODE='42501'; END IF;
 SELECT issuer.registration_revision INTO echo_revision FROM identity.shared_issuers issuer
  WHERE issuer.issuer_id='home-assistant:echo' AND issuer.state='active' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING ERRCODE='42501'; END IF;
 SELECT issuer.registration_revision INTO victoria_revision FROM identity.shared_issuers issuer
  WHERE issuer.issuer_id='home-assistant:victoria' AND issuer.state='active' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING ERRCODE='42501'; END IF;
 BEGIN
   SELECT parent.* INTO STRICT prior FROM identity.shared_link_ceremonies parent
    WHERE parent.ceremony_id=p_ceremony OR
      (parent.owner_commitment=p_owner AND parent.request_commitment=p_request) FOR SHARE;
 EXCEPTION WHEN NO_DATA_FOUND THEN
   -- Absence is unknown, never permission to dispatch again.
   RETURN;
 WHEN TOO_MANY_ROWS THEN
   RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505';
 END;
 SELECT counter.* INTO generation FROM privacy.shared_owner_generations counter
  WHERE counter.owner_commitment=p_owner FOR SHARE;
 IF NOT FOUND OR generation.state<>'active' THEN
   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';
 END IF;
 IF prior.ceremony_id IS DISTINCT FROM p_ceremony OR prior.owner_commitment IS DISTINCT FROM p_owner OR
    prior.request_commitment IS DISTINCT FROM p_request OR prior.principal_id IS DISTINCT FROM p_principal OR
    prior.person_id IS DISTINCT FROM p_person OR prior.legacy_binding_id IS DISTINCT FROM p_binding OR
    prior.initiating_session_commitment IS DISTINCT FROM p_echo_session OR prior.state<>'pending' OR
    prior.purpose<>'link_echo_victoria' OR prior.revision<>1 OR prior.ended_at IS NOT NULL OR
    prior.authorization_generation<>generation.authorization_generation OR
    prior.expires_at<>prior.created_at+interval '5 minutes' THEN
   RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505';
 END IF;
 PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony
   AND child.issuer_id='home-assistant:echo' AND child.challenge_id=p_echo_child
   AND child.session_commitment=p_echo_session AND child.challenge_commitment=p_echo_challenge
   AND child.registration_revision=echo_revision AND child.authorization_generation=prior.authorization_generation
   AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; END IF;
 PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony
   AND child.issuer_id='home-assistant:victoria' AND child.challenge_id=p_victoria_child
   AND child.session_commitment=p_victoria_session AND child.challenge_commitment=p_victoria_challenge
   AND child.registration_revision=victoria_revision AND child.authorization_generation=prior.authorization_generation
   AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; END IF;
 -- Original timestamps are historical evidence even when the lease has expired.
 RETURN QUERY SELECT prior.ceremony_id,prior.authorization_generation,prior.revision,
   prior.created_at,prior.expires_at,echo_revision,victoria_revision;
END
$inspect$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_issuance_kernel_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(f"""DO $roles$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname IN ('{KERNEL_ROLE}','{COORDINATOR_ROLE}')) THEN
        RAISE EXCEPTION 'shared_issuance_kernel_role_collision';
      END IF;
    END $roles$;""")
    for role in (KERNEL_ROLE, COORDINATOR_ROLE):
        op.execute(f"CREATE ROLE {role} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
                   "NOCREATEROLE NOREPLICATION CONNECTION LIMIT 0 VALID UNTIL '1970-01-01';")
    op.execute(f"GRANT {KERNEL_ROLE} TO home_agent_owner WITH ADMIN FALSE,INHERIT FALSE,SET TRUE;")
    op.execute(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {KERNEL_ROLE};")
    for table, columns in READS.items():
        selected = f"SELECT ({columns})" if columns else "SELECT"
        op.execute(f"GRANT {selected} ON {table} TO {KERNEL_ROLE};")
        if table != "public.alembic_version":
            # Legacy PUBLIC permissive policies must not widen this role pair.
            op.execute(f"CREATE POLICY shared_issue_kernel_boundary ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK ({PAIR});")
            # Negative evidence is intentionally not filtered by owner/subject.
            # Only the dedicated definer invocation can read through this policy.
            op.execute(f"CREATE POLICY shared_issue_kernel_select ON {table} FOR SELECT TO {KERNEL_ROLE} USING ({PAIR});")
    for table, column in LOCKS.items():
        op.execute(f"GRANT UPDATE ({column}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_issue_kernel_lock ON {table} FOR UPDATE TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK (false);")
        op.execute(f"CREATE POLICY shared_issue_kernel_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK (false);")
    for table, columns in INSERTS.items():
        op.execute(f"GRANT INSERT ({columns}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_issue_kernel_insert ON {table} FOR INSERT TO {KERNEL_ROLE} WITH CHECK ({PAIR});")
    for table, expression in SUPPRESSION.items():
        op.execute(f"CREATE POLICY shared_issue_kernel_erasure ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});")
    for function in HELPERS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {KERNEL_ROLE};")
    op.execute(BODY)
    op.execute(LOOKUP_BODY)
    op.execute(RECONCILE_BODY)
    op.execute(f"GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE};")
    op.execute(f"ALTER FUNCTION {FUNCTION}({SIGNATURE}) OWNER TO {KERNEL_ROLE};")
    op.execute(f"ALTER FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE}) OWNER TO {KERNEL_ROLE};")
    op.execute(f"ALTER FUNCTION {RECONCILE_FUNCTION}({RECONCILE_SIGNATURE}) OWNER TO {KERNEL_ROLE};")
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
    op.execute(f"REVOKE ALL ON FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
        CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
        JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid='{LOOKUP_FUNCTION}({LOOKUP_SIGNATURE})'::regprocedure AND a.grantee<>p.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    op.execute(f"REVOKE ALL ON FUNCTION {RECONCILE_FUNCTION}({RECONCILE_SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
        CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
        JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid='{RECONCILE_FUNCTION}({RECONCILE_SIGNATURE})'::regprocedure AND a.grantee<>p.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {RECONCILE_FUNCTION}({RECONCILE_SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    op.execute("RESET ROLE;")


def downgrade():
    _guard(revision)
    op.execute(f"SET LOCAL ROLE {KERNEL_ROLE};")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute(f"DROP FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE});")
    op.execute(f"DROP FUNCTION {RECONCILE_FUNCTION}({RECONCILE_SIGNATURE});")
    op.execute("RESET ROLE;")
    for function in HELPERS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {function} FROM {KERNEL_ROLE};")
    for table in SUPPRESSION:
        op.execute(f"DROP POLICY shared_issue_kernel_erasure ON {table};")
    for table, columns in INSERTS.items():
        op.execute(f"DROP POLICY shared_issue_kernel_insert ON {table};")
        op.execute(f"REVOKE INSERT ({columns}) ON {table} FROM {KERNEL_ROLE};")
    for table, column in LOCKS.items():
        op.execute(f"DROP POLICY shared_issue_kernel_no_update ON {table};")
        op.execute(f"DROP POLICY shared_issue_kernel_lock ON {table};")
        op.execute(f"REVOKE UPDATE ({column}) ON {table} FROM {KERNEL_ROLE};")
    for table, columns in READS.items():
        if table != "public.alembic_version":
            op.execute(f"DROP POLICY shared_issue_kernel_boundary ON {table};")
            op.execute(f"DROP POLICY shared_issue_kernel_select ON {table};")
        selected = f"SELECT ({columns})" if columns else "SELECT"
        op.execute(f"REVOKE {selected} ON {table} FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA identity,privacy,public FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE {KERNEL_ROLE} FROM home_agent_owner;")
    op.execute(f"DROP ROLE {COORDINATOR_ROLE};")
    op.execute(f"DROP ROLE {KERNEL_ROLE};")
