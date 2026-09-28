"""Activate or deactivate the dedicated shared-preference database logins.

Reviewed operator maintenance only, run in one SERIALIZABLE owner transaction
at exactly schema 0047 after source registration and preference permissions.
Migrations create these roles dormant (NOLOGIN, connection limit 0, expired)
and grant EXECUTE on the identity kernels only to their NOLOGIN owners. This
command adds exactly the privileges each private listener needs: CONNECT,
USAGE on the identity schema and EXECUTE on the fixed kernel functions, plus a
LOGIN password taken from a staged file and a bounded connection limit.

Deactivation returns every role to its dormant shape, revokes those grants and
terminates remaining sessions. Roles are never dropped, and the consent role's
reviewed table grants (from ``install_dormant_role``) are left intact.
Passwords are hashed to SCRAM verifiers locally; neither they nor the verifiers
are printed.
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .personal_memory_grant_permissions import install_dormant_role
from .personal_memory_grants import ROLE as CONSENT

REVISION = "0047_personal_pref_authority_v1"
COORDINATOR = "home_agent_shared_link_coordinator"
PROOF = ("home_agent_shared_echo_proof_ingress", "home_agent_shared_victoria_proof_ingress")
SESSION = ("home_agent_shared_echo_session_ingress", "home_agent_shared_victoria_session_ingress")

ISSUANCE_ARGS = "uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text"
CONFIRM_ARGS = "uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid"
PROOF_ARGS = "uuid,text,text,text,timestamptz,bigint"

# role -> (connection limit, kernel functions it may execute)
ROLES = {
    COORDINATOR: (2, (
        f"identity.issue_shared_link_ceremony_v1({ISSUANCE_ARGS})",
        "identity.resolve_shared_link_owner_v1(text)",
        f"identity.inspect_shared_link_issuance_v1({ISSUANCE_ARGS})",
        f"identity.confirm_shared_link_ceremony_v1({CONFIRM_ARGS})",
        f"identity.inspect_shared_link_confirmation_v1({CONFIRM_ARGS})",
    )),
    **{role: (2, (
        f"identity.issue_shared_link_auth_proof_v1({PROOF_ARGS})",
        f"identity.inspect_shared_link_auth_proof_v1({PROOF_ARGS})",
    )) for role in PROOF},
    **{role: (1, ("identity.revoke_shared_link_session_bound_v1(text,uuid)",)) for role in SESSION},
    CONSENT: (2, ()),
}
PASSWORD = re.compile(r"^[A-Za-z0-9._~+/=-]{32,256}$")
SCRAM_ITERATIONS = 4096


def scram_verifier(password, *, salt=None):
    """PostgreSQL's SCRAM-SHA-256 verifier; the server never sees the password."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    encode = lambda value: base64.b64encode(value).decode("ascii")
    return f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encode(salt)}${encode(stored_key)}:{encode(server_key)}"


def read_passwords(directory):
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("mounted password directory required")
    passwords = {}
    for role in ROLES:
        path = root / f"{role}.password"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512:
            raise ValueError("staged role password required")
        value = path.read_text(encoding="utf-8").strip()
        if not PASSWORD.fullmatch(value):
            raise ValueError("staged role password rejected")
        passwords[role] = value
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("distinct role passwords required")
    return passwords


def _require_owner_transaction(connection):
    state = connection.execute(text("SELECT current_user,session_user,current_setting('transaction_isolation'),"
        "(SELECT array_agg(version_num::text) FROM public.alembic_version)")).one()
    if tuple(state) != ("home_agent_owner", "home_agent_owner", "serializable", [REVISION]):
        raise ValueError("exact migrated owner transaction required")


def _require_bounded_role(connection, role):
    row = connection.execute(text("""SELECT r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,
        r.rolreplication,r.rolbypassrls,
        EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid)
        FROM pg_catalog.pg_roles r WHERE r.rolname=:role"""), {"role": role}).one_or_none()
    if row is None:
        raise ValueError("dormant shared role missing")
    if tuple(row) != (False, False, False, False, False, False, False):
        raise ValueError("shared role attributes or memberships differ")


def _function_grantees(connection, signature):
    oid = connection.execute(text("SELECT pg_catalog.to_regprocedure(:signature)::oid"),
                             {"signature": signature}).scalar_one()
    if oid is None:
        raise ValueError("shared identity kernel function missing")
    return set(connection.execute(text("""SELECT r.rolname FROM pg_catalog.pg_proc p,
        LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
        JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid=:oid AND a.grantee<>p.proowner AND a.privilege_type='EXECUTE'"""),
        {"oid": oid}).scalars())


def activate(connection, passwords):
    _require_owner_transaction(connection)
    if set(passwords) != set(ROLES):
        raise ValueError("a staged password is required for every shared role")
    if not connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:role)"),
                              {"role": CONSENT}).scalar_one():
        install_dormant_role(connection)
    for role in ROLES:
        _require_bounded_role(connection, role)
    # Fail before changing anything if any kernel ACL already differs.
    allowed = {}
    for role, (_limit, functions) in ROLES.items():
        for signature in functions:
            allowed.setdefault(signature, set()).add(role)
    for signature, roles in allowed.items():
        if not _function_grantees(connection, signature) <= roles:
            raise ValueError("unexpected kernel function grantee")
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    for role, (limit, functions) in ROLES.items():
        if functions:
            connection.execute(text(f"GRANT USAGE ON SCHEMA identity TO {role}"))
        for signature in functions:
            connection.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}"))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {role}'))
        verifier = scram_verifier(passwords[role])
        connection.execute(text(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{verifier}' "
                                f"CONNECTION LIMIT {int(limit)} VALID UNTIL 'infinity'"))


def deactivate(connection):
    _require_owner_transaction(connection)
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    for role, (_limit, functions) in ROLES.items():
        exists = connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=:role)"),
                                    {"role": role}).scalar_one()
        if not exists:
            continue
        connection.execute(text(f"ALTER ROLE {role} WITH NOLOGIN PASSWORD NULL "
                                "CONNECTION LIMIT 0 VALID UNTIL '1970-01-01'"))
        connection.execute(text(f'REVOKE CONNECT ON DATABASE "{database}" FROM {role}'))
        for signature in functions:
            connection.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {role}"))
        if functions:
            connection.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {role}"))
    connection.execute(text("""SELECT pg_catalog.pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity
        WHERE usename = ANY(:roles) AND pid<>pg_catalog.pg_backend_pid()"""), {"roles": list(ROLES)})


def status(connection):
    database = connection.execute(text("SELECT current_database()")).scalar_one()
    result = {}
    for role, (limit, functions) in ROLES.items():
        row = connection.execute(text("""SELECT rolcanlogin,rolconnlimit,rolvaliduntil IS NULL OR rolvaliduntil>now(),
            has_database_privilege(rolname,:database,'CONNECT'),
            (SELECT count(*) FROM pg_catalog.pg_stat_activity a WHERE a.usename=r.rolname)
            FROM pg_catalog.pg_roles r WHERE rolname=:role"""), {"role": role, "database": database}).one_or_none()
        if row is None:
            result[role] = {"present": False}
            continue
        granted = sum(1 for signature in functions if role in _function_grantees(connection, signature))
        result[role] = {"present": True, "login": row[0], "connection_limit": row[1], "expected_limit": limit,
            "unexpired": bool(row[2]), "connect": row[3], "sessions": row[4],
            "functions_granted": granted, "functions_expected": len(functions)}
    return result


def _engine(database_url_file):
    path = Path(database_url_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("mounted owner database credential required")
    url = make_url(path.read_text(encoding="utf-8").strip())
    if url.username != "home_agent_owner" or url.drivername not in ("postgresql", "postgresql+psycopg") or url.query:
        raise ValueError("fixed owner database URL required")
    return create_engine(url.set(drivername="postgresql+psycopg"), isolation_level="SERIALIZABLE",
        pool_size=1, max_overflow=0, hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("activate", "deactivate", "status"))
    parser.add_argument("--database-url-file", required=True)
    parser.add_argument("--password-dir")
    args = parser.parse_args()
    if (args.mode == "activate") != (args.password_dir is not None):
        raise ValueError("password directory is required for activation only")
    passwords = read_passwords(args.password_dir) if args.mode == "activate" else None
    engine = _engine(args.database_url_file)
    try:
        with engine.begin() as connection:
            if args.mode == "status":
                print(json.dumps(status(connection), sort_keys=True, default=str))
                return
            _require_owner_transaction(connection)
            (activate(connection, passwords) if args.mode == "activate" else deactivate(connection))
        if args.mode == "deactivate":
            with engine.connect() as connection:
                remaining = connection.execute(text("SELECT count(*) FROM pg_catalog.pg_stat_activity "
                    "WHERE usename = ANY(:roles)"), {"roles": list(ROLES)}).scalar_one()
            if remaining:
                raise ValueError("shared role sessions remain after deactivation")
    finally:
        engine.dispose()
    print("Shared preference roles " + ("active" if args.mode == "activate" else "dormant"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Shared preference role operation failed", file=sys.stderr)
        raise SystemExit(78)
