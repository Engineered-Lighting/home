"""Read-only shared preference authority kernel; no runtime activation.

Online execution is provisioned separately. The kernel owns only read/row-lock
privileges; it cannot grant capabilities, link identities or change preferences.
"""
import importlib.util
from pathlib import Path

from alembic import op

revision = "0047_personal_pref_authority_v1"
down_revision = "0046_shared_link_session_krnl_v1"
branch_labels = None
depends_on = None

ROLE = "home_agent_personal_memory_reader"
FUNCTION = "identity.resolve_personal_preference_authority_v1"
SIGNATURE = "text,text,text,boolean"
KERNEL = {"function": FUNCTION, "signature": SIGNATURE, "owner": ROLE}
PAIR = f"current_user='{ROLE}' AND session_user IN ('home_agent_api','home_agent_owner')"
READS = {
    "public.alembic_version": "version_num",
    "identity.shared_owner_links": "link_id,principal_id,person_id,authorization_generation,revoked_at",
    "identity.shared_subject_bindings": "binding_id,link_id,issuer_id,subject,revoked_at",
    "identity.principals": "principal_id,person_id,status,kind",
    "identity.people": "person_id,status",
    "identity.shared_source_grants": "grant_id,link_id,site_id,source_id,capability,revision,expires_at,authorization_generation,revoked_at,source_revision",
    "identity.shared_sources": "site_id,source_id,capability,issuer_id,state,registration_revision",
    "identity.shared_issuers": "issuer_id,site_id,state",
    "privacy.shared_link_session_revocations": "issuer_id,session_commitment",
}
# PostgreSQL requires an UPDATE privilege for SELECT FOR SHARE. These column
# privileges belong exclusively to the non-login function owner, not the API.
LOCK_COLUMNS = {
    "identity.shared_owner_links":"link_id", "identity.shared_subject_bindings":"binding_id",
    "identity.principals":"principal_id", "identity.people":"person_id",
    "identity.shared_source_grants":"grant_id", "identity.shared_sources":"source_id",
    "identity.shared_issuers":"issuer_id",
}
HELPERS = ("privacy.identity_person_is_blocked(uuid)","privacy.lock_identity_semantic_write_fence()")
LINEAGE_GUARD = """
CREATE FUNCTION ingest.guard_personal_preference_lineage_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog SET row_security=on
AS $lineage$
DECLARE owner_id uuid; allowed boolean:=false;
BEGIN
 IF current_user<>'home_agent_api' THEN RETURN NEW; END IF;
 owner_id:=nullif(current_setting('app.principal_id',true),'')::uuid;
 IF owner_id IS NULL OR NOT EXISTS (
   SELECT 1 FROM privacy.artifact_registry p,privacy.artifact_registry c
   WHERE p.artifact_id=NEW.parent_artifact_id AND c.artifact_id=NEW.child_artifact_id
     AND p.owner_principal_id=owner_id AND c.owner_principal_id=owner_id
 ) THEN
   RAISE EXCEPTION 'preference_lineage_owner_required' USING ERRCODE='42501';
 END IF;
 IF NEW.relation='supports' THEN
   SELECT EXISTS (
     SELECT 1 FROM identity.confirmation_artifacts a
     JOIN knowledge.memory_transactions t ON t.transaction_id=NEW.child_artifact_id
     WHERE a.artifact_id=NEW.parent_artifact_id AND a.principal_id=owner_id
       AND t.principal_id=owner_id AND t.kind='personal_preference.v1'
       AND t.state='needs_confirmation' AND a.consumed_at IS NOT NULL
       AND a.expires_at>clock_timestamp()
       AND a.purpose=('personal_preference.v1.'||(t.candidate->'request'->>'operation')||'.confirm')
       AND a.proposal_digest=(t.preview->>'reviewed_digest')
   ) INTO allowed;
 ELSIF NEW.relation='derived_from' THEN
   SELECT EXISTS (
     SELECT 1 FROM knowledge.memory_transactions p
     WHERE p.transaction_id=NEW.parent_artifact_id AND p.principal_id=owner_id
       AND p.kind='personal_preference.v1' AND (
         EXISTS (SELECT 1 FROM knowledge.fact_versions f
           WHERE f.fact_id=NEW.child_artifact_id AND f.memory_transaction_id=p.transaction_id
             AND f.perspective_principal_id=owner_id AND f.predicate='personal_preference.evening_lighting')
         OR EXISTS (SELECT 1 FROM knowledge.memory_transactions c
           WHERE c.transaction_id=NEW.child_artifact_id AND c.principal_id=owner_id
             AND c.kind='personal_preference.v1' AND c.state='needs_confirmation'
             AND p.state='committed'
             AND (c.candidate->>'preference_fact_id')=(p.candidate->>'preference_fact_id'))
       )
   ) INTO allowed;
 END IF;
 IF NOT allowed THEN
   RAISE EXCEPTION 'preference_lineage_scope_invalid' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END
$lineage$;
REVOKE ALL ON FUNCTION ingest.guard_personal_preference_lineage_v1() FROM PUBLIC;
CREATE TRIGGER personal_preference_lineage_guard BEFORE INSERT ON ingest.artifact_links
FOR EACH ROW EXECUTE FUNCTION ingest.guard_personal_preference_lineage_v1();
"""
BODY = f"""
CREATE FUNCTION {FUNCTION}(p_issuer text,p_subject text,p_session text,p_write boolean)
RETURNS TABLE(principal_id uuid,person_id uuid,link_id uuid,authorization_generation bigint,
 echo_grant_revision bigint,victoria_grant_revision bigint,valid_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $kernel$
DECLARE anchor record; grant_row record; bindings integer:=0; grants integer:=0;
 echo_revision bigint; victoria_revision bigint; expiry timestamptz;
BEGIN
 IF NOT ({PAIR}) OR NOT EXISTS (
   SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
     AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT r.rolinherit
     AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication)
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname=current_user)
 OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
   JOIN pg_catalog.pg_roles target ON target.oid=m.roleid
   JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member
   WHERE target.rolname=current_user AND (member_role.rolname<>'home_agent_owner'
     OR m.admin_option OR m.inherit_option OR NOT m.set_option)) THEN
   RAISE EXCEPTION 'personal_preference_role_invalid' USING ERRCODE='42501';
 END IF;
 IF current_setting('transaction_isolation')<>'serializable' OR pg_is_in_recovery() OR
   (SELECT count(*) FROM public.alembic_version)<>1 OR NOT EXISTS (
     SELECT 1 FROM public.alembic_version a WHERE a.version_num='{revision}') THEN
   RAISE EXCEPTION 'personal_preference_transaction_invalid' USING ERRCODE='55000';
 END IF;
 IF p_issuer IS NULL OR p_issuer NOT IN ('home-assistant:echo','home-assistant:victoria') OR
   p_subject IS NULL OR length(p_subject) NOT BETWEEN 1 AND 64 OR p_subject<>btrim(p_subject) OR
   p_subject ~ '[[:cntrl:]]' OR p_session IS NULL OR p_session !~ '^[a-f0-9]{{64}}$' OR p_write IS NULL THEN
   RAISE EXCEPTION 'personal_preference_session_invalid' USING ERRCODE='22023';
 END IF;
 PERFORM privacy.lock_identity_semantic_write_fence();
 SELECT l.link_id,l.principal_id,l.person_id,l.authorization_generation INTO anchor
 FROM identity.shared_subject_bindings b
 JOIN identity.shared_owner_links l ON l.link_id=b.link_id
 JOIN identity.principals p ON p.principal_id=l.principal_id AND p.person_id=l.person_id
 JOIN identity.people person ON person.person_id=l.person_id
 WHERE b.issuer_id=p_issuer AND b.subject=p_subject AND b.revoked_at IS NULL
   AND l.revoked_at IS NULL AND p.status='active' AND p.kind='ha_user' AND person.status='active'
 FOR SHARE OF b,l,p,person;
 IF NOT FOUND THEN RAISE EXCEPTION 'personal_preference_unavailable' USING ERRCODE='42501'; END IF;
 FOR grant_row IN SELECT b.binding_id FROM identity.shared_subject_bindings b
   JOIN identity.shared_issuers i ON i.issuer_id=b.issuer_id
   WHERE b.link_id=anchor.link_id AND b.revoked_at IS NULL AND i.state='active'
     AND b.issuer_id IN ('home-assistant:echo','home-assistant:victoria')
   ORDER BY b.issuer_id FOR SHARE OF b,i
 LOOP bindings:=bindings+1; END LOOP;
 IF bindings<>2 OR privacy.identity_person_is_blocked(anchor.person_id) OR EXISTS (
   SELECT 1 FROM privacy.shared_link_session_revocations r WHERE r.issuer_id=p_issuer AND r.session_commitment=p_session) THEN
   RAISE EXCEPTION 'personal_preference_unavailable' USING ERRCODE='42501';
 END IF;
 expiry:=clock_timestamp()+interval '60 seconds';
 FOR grant_row IN SELECT g.site_id,g.capability,g.revision,g.expires_at
   FROM identity.shared_source_grants g
   JOIN identity.shared_sources s USING(site_id,source_id,capability)
   JOIN identity.shared_issuers i ON i.issuer_id=s.issuer_id AND i.site_id=s.site_id
   WHERE g.link_id=anchor.link_id AND g.authorization_generation=anchor.authorization_generation
     AND g.revoked_at IS NULL AND g.expires_at>clock_timestamp()
     AND g.source_id='core.personal-preferences.v1' AND g.site_id IN ('echo','victoria')
     AND (g.capability='memory.read' OR p_write AND g.capability='personal_memory.write')
     AND s.state='active' AND s.registration_revision=g.source_revision AND i.state='active'
   ORDER BY g.site_id,g.capability FOR SHARE OF g,s,i
 LOOP
   grants:=grants+1; expiry:=least(expiry,grant_row.expires_at);
   IF grant_row.capability=(CASE WHEN p_write THEN 'personal_memory.write' ELSE 'memory.read' END) THEN
     IF grant_row.site_id='echo' THEN echo_revision:=grant_row.revision;
     ELSE victoria_revision:=grant_row.revision; END IF;
   END IF;
 END LOOP;
 IF grants<>(CASE WHEN p_write THEN 4 ELSE 2 END) OR echo_revision IS NULL OR victoria_revision IS NULL OR expiry<=clock_timestamp() THEN
   RAISE EXCEPTION 'personal_preference_grants_unavailable' USING ERRCODE='42501';
 END IF;
 RETURN QUERY SELECT anchor.principal_id,anchor.person_id,anchor.link_id,anchor.authorization_generation,
   echo_revision,victoria_revision,expiry;
END
$kernel$;
"""


def previous():
    # Import the immutable previous migration, not evolving app metadata.
    spec=importlib.util.spec_from_file_location("_personal_memory_previous",Path(__file__).with_name("0046_shared_link_session_kernel.py"))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wrappers(old):
    return [(value,old.aligned_sql(value)) for value in old.WRAPPERS]+[(old.KERNEL,old.BODY)]


def advance(sql):
    needle=f"a.version_num='{down_revision}'"
    if sql.count(needle)!=1: raise ValueError("frozen revision guard drift")
    return sql.replace(needle,f"a.version_num='{revision}'")


def verify_lineage():
    source = LINEAGE_GUARD.split('$lineage$')[1].replace("'", "''")
    op.execute(f"""DO $verify$ BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_trigger t ON t.tgfoid=p.oid
        WHERE p.oid='ingest.guard_personal_preference_lineage_v1()'::regprocedure
          AND r.rolname='home_agent_owner' AND NOT p.prosecdef
          AND p.prorettype='pg_catalog.trigger'::regtype AND p.prosrc='{source}'
          AND p.proconfig=ARRAY['search_path=pg_catalog','row_security=on']::text[]
          AND t.tgrelid='ingest.artifact_links'::regclass
          AND t.tgname='personal_preference_lineage_guard' AND t.tgtype=7
          AND t.tgenabled='O' AND NOT t.tgisinternal AND t.tgnargs=0 AND t.tgqual IS NULL
      ) THEN RAISE EXCEPTION 'personal_preference_lineage_drift'; END IF;
    END $verify$;""")


def upgrade():
    old=previous();old._guard(down_revision)
    for wrapper,sql in wrappers(old): old._verify(wrapper,sql)
    op.execute(f"CREATE ROLE {ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 0 VALID UNTIL '1970-01-01';")
    op.execute(f"GRANT {ROLE} TO home_agent_owner WITH ADMIN FALSE,INHERIT FALSE,SET TRUE;")
    op.execute(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {ROLE};")
    for table,columns in READS.items():
        op.execute(f"GRANT SELECT ({columns}) ON {table} TO {ROLE};")
        if table!='public.alembic_version':
            op.execute(f"CREATE POLICY personal_preference_reader_boundary ON {table} AS RESTRICTIVE FOR ALL TO {ROLE} USING ({PAIR}) WITH CHECK (false);")
            op.execute(f"CREATE POLICY personal_preference_reader_select ON {table} FOR SELECT TO {ROLE} USING ({PAIR});")
    for table,column in LOCK_COLUMNS.items():
        op.execute(f"GRANT UPDATE ({column}) ON {table} TO {ROLE};")
        # FOR SHARE checks UPDATE USING, but not UPDATE WITH CHECK. Permit row
        # locks while forbidding every actual updated row, including no-ops.
        op.execute(f"CREATE POLICY personal_preference_reader_lock ON {table} FOR UPDATE TO {ROLE} USING ({PAIR}) WITH CHECK (false);")
    for helper in HELPERS: op.execute(f"GRANT EXECUTE ON FUNCTION {helper} TO {ROLE};")
    op.execute(BODY)
    op.execute(f"GRANT CREATE ON SCHEMA identity TO {ROLE};")
    op.execute(f"ALTER FUNCTION {FUNCTION}({SIGNATURE}) OWNER TO {ROLE};")
    op.execute(f"REVOKE CREATE ON SCHEMA identity FROM {ROLE};")
    op.execute(f"SET LOCAL ROLE {ROLE};")
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM PUBLIC;")
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
        CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid='{FUNCTION}({SIGNATURE})'::regprocedure AND a.grantee<>p.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {FUNCTION}({SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    op.execute('RESET ROLE;')
    for wrapper,sql in wrappers(old):
        old._replace(wrapper,advance(sql));old._verify(wrapper,advance(sql))
    old._verify(KERNEL,BODY)
    # INSERT remains ungranted to the API until explicit runtime activation.
    # Other existing ingest/maintenance writers retain their current behavior.
    op.execute(LINEAGE_GUARD)
    verify_lineage()


def downgrade():
    old=previous();old._guard(revision)
    for wrapper,sql in wrappers(old): old._verify(wrapper,advance(sql))
    old._verify(KERNEL,BODY)
    verify_lineage()
    op.execute("DROP TRIGGER personal_preference_lineage_guard ON ingest.artifact_links;")
    op.execute("DROP FUNCTION ingest.guard_personal_preference_lineage_v1();")
    op.execute(f"SET LOCAL ROLE {ROLE};")
    op.execute(f"DROP FUNCTION {FUNCTION}({SIGNATURE});")
    op.execute('RESET ROLE;')
    for helper in HELPERS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {helper} FROM {ROLE};")
    for table,column in LOCK_COLUMNS.items():
        op.execute(f"DROP POLICY personal_preference_reader_lock ON {table};")
        op.execute(f"REVOKE UPDATE ({column}) ON {table} FROM {ROLE};")
    for table,columns in READS.items():
        if table!='public.alembic_version':
            op.execute(f"DROP POLICY personal_preference_reader_boundary ON {table};")
            op.execute(f"DROP POLICY personal_preference_reader_select ON {table};")
        op.execute(f"REVOKE SELECT ({columns}) ON {table} FROM {ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA identity,privacy,public FROM {ROLE};")
    op.execute(f"REVOKE {ROLE} FROM home_agent_owner;")
    op.execute(f"DROP ROLE {ROLE};")
    for wrapper,sql in wrappers(old): old._replace(wrapper,sql);old._verify(wrapper,sql)
