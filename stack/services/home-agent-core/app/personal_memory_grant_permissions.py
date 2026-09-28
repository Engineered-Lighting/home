"""Install a dormant, narrowly scoped preference-consent database role.

Reviewed operator preparation only. No LOGIN, password, listener, account link,
or source grant is created. Existing roles and permissions remain unchanged.
An existing role is refused rather than silently adopted or broadened.
"""
from sqlalchemy import text

from .personal_memory_grants import ROLE

REVISION = "0047_personal_pref_authority_v1"
PAIR = f"current_user='{ROLE}' AND session_user='{ROLE}'"
SCOPE = "source_id='core.personal-preferences.v1' AND site_id IN ('echo','victoria') AND capability IN ('memory.read','personal_memory.write')"
READS = {
    "public.alembic_version": "version_num",
    "identity.shared_owner_links": "link_id,principal_id,person_id,revision,authorization_generation,revoked_at",
    "identity.shared_subject_bindings": "binding_id,link_id,issuer_id,subject,revoked_at",
    "identity.principals": "principal_id,person_id,status,kind",
    "identity.people": "person_id,status",
    "identity.shared_issuers": "issuer_id,site_id,state",
    "identity.shared_sources": "site_id,source_id,capability,issuer_id,state,registration_revision",
    "identity.shared_source_grants": "grant_id,link_id,site_id,source_id,capability,source_revision,authorization_generation,revision,approval_commitment,created_at,expires_at,revoked_at",
    "privacy.shared_link_session_revocations": "issuer_id,session_commitment",
}
LOCKS = {
    "identity.shared_owner_links": "link_id",
    "identity.shared_subject_bindings": "binding_id",
    "identity.principals": "principal_id",
    "identity.people": "person_id",
    "identity.shared_issuers": "issuer_id",
    "identity.shared_sources": "source_id",
}
HELPERS = ("privacy.identity_person_is_blocked(uuid)", "privacy.lock_identity_semantic_write_fence()")


def install_dormant_role(connection):
    state = connection.execute(text("SELECT current_user,session_user,current_setting('transaction_isolation'),"
        "(SELECT array_agg(version_num::text) FROM public.alembic_version)")).one()
    if tuple(state) != ("home_agent_owner", "home_agent_owner", "serializable", [REVISION]):
        raise ValueError("exact migrated owner transaction required")
    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:role)"),
                          {"role": ROLE}).scalar_one():
        raise ValueError("existing consent role requires explicit inspection")
    # Fail before changing anything if the target tables are not protected.
    tables = [table for table in READS if table != "public.alembic_version"]
    for table in tables:
        protected = connection.execute(text("""SELECT c.relrowsecurity AND c.relforcerowsecurity
            AND r.rolname='home_agent_owner' FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_roles r ON r.oid=c.relowner WHERE c.oid=to_regclass(:name)"""),
            {"name": table}).scalar_one_or_none()
        if protected is not True:
            raise ValueError("consent table ownership or row security differs")
    connection.execute(text(f"CREATE ROLE {ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 VALID UNTIL '1970-01-01'"))
    connection.execute(text(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {ROLE}"))
    for table, columns in READS.items():
        connection.execute(text(f"GRANT SELECT ({columns}) ON {table} TO {ROLE}"))
        if table == "public.alembic_version": continue
        scope = f"({PAIR})" + (f" AND ({SCOPE})" if table in (
            "identity.shared_sources", "identity.shared_source_grants") else "")
        check = scope if table == "identity.shared_source_grants" else "false"
        connection.execute(text(f"CREATE POLICY preference_consent_boundary ON {table} AS RESTRICTIVE FOR ALL TO {ROLE} USING ({scope}) WITH CHECK ({check})"))
        connection.execute(text(f"CREATE POLICY preference_consent_read ON {table} FOR SELECT TO {ROLE} USING ({scope})"))
    for table, column in LOCKS.items():
        connection.execute(text(f"GRANT UPDATE ({column}) ON {table} TO {ROLE}"))
        connection.execute(text(f"CREATE POLICY preference_consent_lock ON {table} FOR UPDATE TO {ROLE} USING ({PAIR}) WITH CHECK (false)"))
    table = "identity.shared_source_grants"
    # UPDATE only the revocation marker; immutable grant identity, scope and
    # revisions cannot be rewritten. No DELETE/TRUNCATE/table-level write grant.
    connection.execute(text(f"GRANT UPDATE (revoked_at) ON {table} TO {ROLE}"))
    connection.execute(text(f"CREATE POLICY preference_consent_retire ON {table} FOR UPDATE TO {ROLE} USING ({PAIR}) WITH CHECK ({PAIR} AND revoked_at IS NOT NULL)"))
    insert_columns = READS[table].replace(",revoked_at", "")
    connection.execute(text(f"GRANT INSERT ({insert_columns}) ON {table} TO {ROLE}"))
    connection.execute(text(f"CREATE POLICY preference_consent_create ON {table} FOR INSERT TO {ROLE} WITH CHECK ({PAIR} AND revoked_at IS NULL)"))
    for helper in HELPERS:
        connection.execute(text(f"GRANT EXECUTE ON FUNCTION {helper} TO {ROLE}"))
