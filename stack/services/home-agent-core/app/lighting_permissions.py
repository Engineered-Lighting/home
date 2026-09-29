"""Register the lighting sources and install the dormant lighting role.

Reviewed owner maintenance only, in one SERIALIZABLE owner transaction at
exactly schema 0047, after the preference sources are registered (the two home
issuers must already exist). This creates no login, password, listener or
grant. ``activate`` later adds LOGIN from a staged password file, with a
connection limit of 2; ``deactivate`` returns the role to its dormant shape.

The ``home_agent_lighting`` role reads the owner's linked accounts and can write
only ``core.lighting.v1`` / ``lighting.execute`` grants; row security confines
it to exactly that source and capability.
"""
import argparse
import sys

from sqlalchemy import text

from .lighting_authority import REVISION, ROLE, SITES
from .lighting_contract import CAPABILITY, SOURCE
from .shared_preference_roles import (PASSWORD, _engine, _require_bounded_role, _require_owner_transaction,
                                      scram_verifier)

PAIR = f"current_user='{ROLE}' AND session_user='{ROLE}'"
SCOPE = f"source_id='{SOURCE}' AND site_id IN ('echo','victoria') AND capability='{CAPABILITY}'"
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


def _protected(connection, table):
    return connection.execute(text("""SELECT c.relrowsecurity AND c.relforcerowsecurity
        AND r.rolname='home_agent_owner' FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_roles r ON r.oid=c.relowner WHERE c.oid=to_regclass(:name)"""),
        {"name": table}).scalar_one_or_none() is True


def register_sources(connection):
    """Add the two lighting sources; existing rows must match exactly."""
    _require_owner_transaction(connection)
    if not all(_protected(connection, "identity." + table) for table in ("shared_issuers", "shared_sources")):
        raise ValueError("registration ownership or row security differs")
    connection.execute(text("LOCK TABLE identity.shared_issuers,identity.shared_sources IN SHARE ROW EXCLUSIVE MODE"))
    missing = []
    for site in SITES:
        issuer = "home-assistant:" + site
        rows = connection.execute(text("SELECT issuer_id,site_id,registration_revision,state FROM identity.shared_issuers "
                                       "WHERE issuer_id=:issuer OR site_id=:site"), {"issuer": issuer, "site": site}).all()
        if [tuple(row) for row in rows] != [(issuer, site, 1, "active")]:
            raise ValueError("home issuers must be registered first")
        params = {"site": site, "issuer": issuer, "source": SOURCE, "capability": CAPABILITY}
        row = connection.execute(text("SELECT issuer_id,registration_revision,state FROM identity.shared_sources "
            "WHERE site_id=:site AND source_id=:source AND capability=:capability"), params).one_or_none()
        if row is None:
            missing.append(params)
        elif tuple(row) != (issuer, 1, "active"):
            raise ValueError("existing lighting source registration requires review")
    for params in missing:
        connection.execute(text("INSERT INTO identity.shared_sources "
            "(site_id,source_id,capability,issuer_id,registration_revision,state) "
            "VALUES (:site,:source,:capability,:issuer,1,'active')"), params)
    return len(missing)


def install_dormant_role(connection):
    _require_owner_transaction(connection)
    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:role)"),
                          {"role": ROLE}).scalar_one():
        raise ValueError("existing lighting role requires explicit inspection")
    if not all(_protected(connection, table) for table in READS if table != "public.alembic_version"):
        raise ValueError("lighting table ownership or row security differs")
    connection.execute(text(f"CREATE ROLE {ROLE} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                            "NOBYPASSRLS CONNECTION LIMIT 0 VALID UNTIL '1970-01-01'"))
    connection.execute(text(f"GRANT USAGE ON SCHEMA identity,privacy,public TO {ROLE}"))
    for table, columns in READS.items():
        connection.execute(text(f"GRANT SELECT ({columns}) ON {table} TO {ROLE}"))
        if table == "public.alembic_version":
            continue
        scope = f"({PAIR})" + (f" AND ({SCOPE})" if table in (
            "identity.shared_sources", "identity.shared_source_grants") else "")
        check = scope if table == "identity.shared_source_grants" else "false"
        connection.execute(text(f"CREATE POLICY lighting_boundary ON {table} AS RESTRICTIVE FOR ALL TO {ROLE} "
                                f"USING ({scope}) WITH CHECK ({check})"))
        connection.execute(text(f"CREATE POLICY lighting_read ON {table} FOR SELECT TO {ROLE} USING ({scope})"))
    for table, column in LOCKS.items():
        connection.execute(text(f"GRANT UPDATE ({column}) ON {table} TO {ROLE}"))
        connection.execute(text(f"CREATE POLICY lighting_lock ON {table} FOR UPDATE TO {ROLE} USING ({PAIR}) WITH CHECK (false)"))
    table = "identity.shared_source_grants"
    # Only the revocation marker may change; grant identity, scope and revisions are immutable.
    connection.execute(text(f"GRANT UPDATE (revoked_at) ON {table} TO {ROLE}"))
    connection.execute(text(f"CREATE POLICY lighting_retire ON {table} FOR UPDATE TO {ROLE} USING ({PAIR}) "
                            f"WITH CHECK ({PAIR} AND revoked_at IS NOT NULL)"))
    connection.execute(text(f"GRANT INSERT ({READS[table].replace(',revoked_at', '')}) ON {table} TO {ROLE}"))
    connection.execute(text(f"CREATE POLICY lighting_create ON {table} FOR INSERT TO {ROLE} "
                            f"WITH CHECK ({PAIR} AND revoked_at IS NULL)"))
    for helper in HELPERS:
        connection.execute(text(f"GRANT EXECUTE ON FUNCTION {helper} TO {ROLE}"))


CONNECTION_LIMIT = 2


def read_password(path):
    from pathlib import Path
    file = Path(path)
    if not file.is_absolute() or file.is_symlink() or not file.is_file() or file.stat().st_size > 512:
        raise ValueError("staged lighting role password required")
    value = file.read_text(encoding="utf-8").strip()
    if not PASSWORD.fullmatch(value):
        raise ValueError("staged lighting role password rejected")
    return value


def activate(connection, password):
    _require_owner_transaction(connection)
    _require_bounded_role(connection, ROLE)
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    # The SCRAM verifier is inlined below; keep it out of statement logs.
    connection.execute(text("SET LOCAL log_statement = 'none'"))
    connection.execute(text("SET LOCAL log_min_error_statement = 'panic'"))
    connection.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {ROLE}'))
    verifier = scram_verifier(password)
    connection.exec_driver_sql(f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{verifier}' "
                               f"CONNECTION LIMIT {CONNECTION_LIMIT} VALID UNTIL 'infinity'")


def deactivate(connection):
    _require_owner_transaction(connection)
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    connection.execute(text(f"ALTER ROLE {ROLE} WITH NOLOGIN PASSWORD NULL CONNECTION LIMIT 0 VALID UNTIL '1970-01-01'"))
    connection.execute(text(f'REVOKE CONNECT ON DATABASE "{database}" FROM {ROLE}'))


def status(connection):
    row = connection.execute(text("""SELECT rolcanlogin,rolconnlimit,
        (SELECT count(*) FROM pg_catalog.pg_stat_activity a WHERE a.usename=r.rolname)
        FROM pg_catalog.pg_roles r WHERE rolname=:role"""), {"role": ROLE}).one_or_none()
    sources = connection.execute(text("SELECT count(*) FROM identity.shared_sources WHERE source_id=:source "
        "AND capability=:capability AND state='active'"), {"source": SOURCE, "capability": CAPABILITY}).scalar_one()
    if row is None:
        return {"role_present": False, "sources": sources}
    return {"role_present": True, "login": row[0], "connection_limit": row[1], "sessions": row[2], "sources": sources}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "activate", "deactivate", "status"))
    parser.add_argument("--database-url-file", required=True)
    parser.add_argument("--password-file")
    args = parser.parse_args()
    if (args.mode == "activate") != (args.password_file is not None):
        raise ValueError("a password file is required for activation only")
    password = read_password(args.password_file) if args.mode == "activate" else None
    engine = _engine(args.database_url_file)
    try:
        with engine.begin() as connection:
            if args.mode == "status":
                print(json.dumps(status(connection), sort_keys=True))
                return
            if args.mode == "prepare":
                added = register_sources(connection)
                present = connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:role)"),
                                             {"role": ROLE}).scalar_one()
                if not present:
                    install_dormant_role(connection)
                print(f"Lighting sources registered ({added} new); lighting role "
                      + ("already present, unchanged" if present else "installed dormant"))
                return
            (activate(connection, password) if args.mode == "activate" else deactivate(connection))
        if args.mode == "deactivate":
            with engine.begin() as connection:
                connection.execute(text("SELECT pg_catalog.pg_terminate_backend(pid, 5000) FROM pg_catalog.pg_stat_activity "
                                        "WHERE usename=:role AND pid<>pg_catalog.pg_backend_pid()"), {"role": ROLE})
    finally:
        engine.dispose()
    print("Lighting role " + ("active" if args.mode == "activate" else "dormant"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Lighting registration failed; inspect through owner maintenance", file=sys.stderr)
        raise SystemExit(78) from None
