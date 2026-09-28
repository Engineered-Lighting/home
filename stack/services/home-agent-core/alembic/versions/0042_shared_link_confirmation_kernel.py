"""Dormant final owner-link confirmation wrapper; no caller is enabled.

Existing issuance and proof wrappers remain revision-closed. This migration
contains authority, not owner-session authentication or runtime admission.
"""
from alembic import op

revision = "0042_shared_link_confirm_krnl_v1"
down_revision = "0041_shared_link_proof_kernel_v1"
branch_labels = None
depends_on = None
FUNCTION = "identity.confirm_shared_link_ceremony_v1"
SIGNATURE = "uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid"
KERNEL_ROLE = "home_agent_shared_link_confirm_kernel"
COORDINATOR_ROLE = "home_agent_shared_link_coordinator"
PAIR = f"session_user='{COORDINATOR_ROLE}' AND current_user='{KERNEL_ROLE}'"

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
 'identity.shared_link_challenges': 'ceremony_id,issuer_id,session_commitment,consumed_proof_id,expires_at,created_at',
 'identity.shared_owner_links': 'link_id,receipt_id,authorization_generation,revision,revoked_at,principal_id,person_id',
 'identity.shared_auth_proofs': None,
 'identity.shared_link_proposals': 'proposal_id,echo_proof_id,victoria_proof_id,expected_generation,principal_id,person_id',
 'identity.shared_link_receipts': 'receipt_id,proposal_id,confirmed_at,confirmation_commitment,proposal_digest,principal_id,person_id',
 'identity.shared_subject_bindings': 'binding_id,link_id,issuer_id,subject,revoked_at'}

LOCKS = {'identity.ha_user_bindings': 'binding_id',
 'identity.principals': 'principal_id',
 'identity.people': 'person_id',
 'identity.principal_binding_proposals': 'proposal_id',
 'identity.principal_binding_requests': 'request_id',
 'identity.confirmation_artifacts': 'artifact_id',
 'identity.shared_issuers': 'registration_revision',
 'identity.shared_link_challenges': 'challenge_id',
 'identity.shared_link_proposals': 'proposal_id',
 'identity.shared_link_receipts': 'receipt_id',
 'identity.shared_owner_links': 'link_id',
 'identity.shared_subject_bindings': 'binding_id'}

SUPPRESSION = {'identity.people': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.principals': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.ha_user_bindings': 'NOT privacy.identity_person_is_blocked(person_id)',
 'identity.principal_binding_proposals': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                         'privacy.identity_principal_is_blocked(result_principal_id)',
 'identity.confirmation_artifacts': 'NOT privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_link_ceremonies': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                    'privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_owner_links': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                'privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_link_proposals': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                   'privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_link_receipts': 'NOT privacy.identity_person_is_blocked(person_id) AND NOT '
                                  'privacy.identity_principal_is_blocked(principal_id)',
 'identity.shared_subject_bindings': 'EXISTS (SELECT 1 FROM identity.shared_owner_links linked WHERE '
                                     'linked.link_id=shared_subject_bindings.link_id)'}

UPDATES = {
 "identity.shared_auth_proofs": "consumed_at",
 "privacy.shared_owner_generations": "authorization_generation,revision,updated_at",
 "identity.shared_link_ceremonies": "state,ended_at,confirmation_receipt_id,revision",
}
INSERTS = {
 "identity.shared_link_proposals": "proposal_id,legacy_binding_id,principal_id,person_id,confirmed_by_principal_id,echo_proof_id,echo_issuer,echo_subject,victoria_proof_id,victoria_issuer,victoria_subject,proposal_digest,expected_generation,created_at,expires_at",
 "identity.shared_link_receipts": "receipt_id,proposal_id,principal_id,person_id,proposal_digest,confirmation_commitment,confirmed_at",
 "identity.shared_owner_links": "link_id,receipt_id,principal_id,person_id,revision,authorization_generation,created_at",
 "identity.shared_subject_bindings": "binding_id,link_id,issuer_id,subject,created_at",
}
HELPERS = (
 f"identity.confirm_shared_link_v1({SIGNATURE})",
 "identity.require_shared_link_owner_v1(text,uuid,uuid,uuid)",
 "identity.require_shared_link_session_active_v1(text,text)",
 "privacy.lock_identity_semantic_write_fence()",
 "privacy.identity_person_is_blocked(uuid)",
 "privacy.identity_principal_is_blocked(uuid)",
)
BODY = f"""
CREATE FUNCTION {FUNCTION}(
 p_ceremony uuid,p_echo_subject text,p_session text,p_expected_revision bigint,
 p_confirmation text,p_digest text,p_proposal uuid,p_receipt uuid,p_link uuid,p_echo_binding uuid,p_victoria_binding uuid
) RETURNS TABLE(link_id uuid,authorization_generation bigint,revision bigint,confirmed_at timestamptz)
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
   RAISE EXCEPTION 'shared_link_confirmation_role_invalid' USING ERRCODE='42501';
 END IF;
 IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR
    pg_catalog.current_setting('transaction_read_only')<>'off' OR pg_catalog.pg_is_in_recovery() OR
    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
   RAISE EXCEPTION 'shared_link_confirmation_transaction_invalid' USING ERRCODE='25001';
 END IF;
 IF (SELECT count(*) FROM public.alembic_version)<>1 OR
    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE a.version_num='{revision}') THEN
   RAISE EXCEPTION 'shared_link_confirmation_revision_invalid' USING ERRCODE='55000';
 END IF;

 RETURN QUERY SELECT confirmed.* FROM identity.confirm_shared_link_v1(
   p_ceremony,p_echo_subject,p_session,p_expected_revision,p_confirmation,p_digest,
   p_proposal,p_receipt,p_link,p_echo_binding,p_victoria_binding) confirmed;
END
$kernel$;
"""


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_link_confirmation_kernel_migration_authority_invalid';
      END IF;
    END $guard$;""")


def upgrade():
    _guard(down_revision)
    op.execute(f"""DO $roles$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='{KERNEL_ROLE}') OR
         NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='{COORDINATOR_ROLE}') THEN
        RAISE EXCEPTION 'shared_link_confirmation_kernel_role_collision';
      END IF;
    END $roles$;""")
    op.execute(f"CREATE ROLE {KERNEL_ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB "
               "NOCREATEROLE NOREPLICATION CONNECTION LIMIT 0 VALID UNTIL '1970-01-01';")
    op.execute(f"GRANT {KERNEL_ROLE} TO home_agent_owner WITH ADMIN FALSE,INHERIT FALSE,SET TRUE;")
    op.execute(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {KERNEL_ROLE};")
    for table, columns in READS.items():
        selected = f"SELECT ({columns})" if columns else 'SELECT'
        op.execute(f"GRANT {selected} ON {table} TO {KERNEL_ROLE};")
        if table != 'public.alembic_version':
            op.execute(f"CREATE POLICY shared_confirm_boundary ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK ({PAIR});")
            op.execute(f"CREATE POLICY shared_confirm_select ON {table} FOR SELECT TO {KERNEL_ROLE} USING ({PAIR});")
    for table, column in LOCKS.items():
        op.execute(f"GRANT UPDATE ({column}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_confirm_lock ON {table} FOR UPDATE TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK (false);")
        op.execute(f"CREATE POLICY shared_confirm_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK (false);")
    for table, columns in UPDATES.items():
        op.execute(f"GRANT UPDATE ({columns}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_confirm_update ON {table} FOR UPDATE TO {KERNEL_ROLE} USING ({PAIR}) WITH CHECK ({PAIR});")
    for table, columns in INSERTS.items():
        op.execute(f"GRANT INSERT ({columns}) ON {table} TO {KERNEL_ROLE};")
        op.execute(f"CREATE POLICY shared_confirm_insert ON {table} FOR INSERT TO {KERNEL_ROLE} WITH CHECK ({PAIR});")
    for table, expression in SUPPRESSION.items():
        op.execute(f"CREATE POLICY shared_confirm_erasure ON {table} AS RESTRICTIVE FOR ALL TO {KERNEL_ROLE} USING ({expression}) WITH CHECK ({expression});")
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
        op.execute(f"DROP POLICY shared_confirm_erasure ON {table};")
    for table, columns in INSERTS.items():
        op.execute(f"DROP POLICY shared_confirm_insert ON {table};")
        op.execute(f"REVOKE INSERT ({columns}) ON {table} FROM {KERNEL_ROLE};")
    for table, columns in UPDATES.items():
        op.execute(f"DROP POLICY shared_confirm_update ON {table};")
        op.execute(f"REVOKE UPDATE ({columns}) ON {table} FROM {KERNEL_ROLE};")
    for table, column in LOCKS.items():
        op.execute(f"DROP POLICY shared_confirm_no_update ON {table};")
        op.execute(f"DROP POLICY shared_confirm_lock ON {table};")
        op.execute(f"REVOKE UPDATE ({column}) ON {table} FROM {KERNEL_ROLE};")
    for table, columns in READS.items():
        if table != 'public.alembic_version':
            op.execute(f"DROP POLICY shared_confirm_boundary ON {table};")
            op.execute(f"DROP POLICY shared_confirm_select ON {table};")
        selected = f"SELECT ({columns})" if columns else 'SELECT'
        op.execute(f"REVOKE {selected} ON {table} FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA identity,privacy,public FROM {KERNEL_ROLE};")
    op.execute(f"REVOKE {KERNEL_ROLE} FROM home_agent_owner;")
    op.execute(f"DROP ROLE {KERNEL_ROLE};")
