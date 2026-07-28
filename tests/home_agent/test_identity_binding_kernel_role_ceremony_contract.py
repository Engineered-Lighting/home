from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROLE = "home_agent_identity_binding_kernel"
SCRIPT = "stack/home-agent-deploy/" "provision-identity-binding-kernel-role.sh"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _sql(source: str) -> str:
    return source.split("<<'SQL'", 1)[1].rsplit("\nSQL", 1)[0]


def _block(source: str, marker: str) -> str:
    return source.split(f"DO ${marker}$", 1)[1].split(f"${marker}$;", 1)[0]


def test_e5b_kernel_role_ceremony_is_separate_owner_only_and_dormant() -> None:
    source = _read(SCRIPT)
    historical = _read("stack/home-agent-deploy/provision-roles.sh")
    cutover = _read("stack/home-agent-deploy/provision-identity-cutover-roles.sh")

    assert source.startswith("#!/bin/sh\nset -eu\numask 077\n")
    assert "must run as root" in source
    assert 'read_secret "$POSTGRES_OWNER_PASSWORD_FILE"' in source
    assert source.count("POSTGRES_OWNER_PASSWORD_FILE") == 1
    assert "/run/secrets/" not in source
    assert "database_url" not in source
    assert f"CREATE ROLE {ROLE} LOGIN" not in source
    assert "PASSWORD :'" not in source
    assert "ALTER ROLE home_agent_identity_binding_kernel PASSWORD NULL;" in (source)

    assert ROLE not in historical
    assert ROLE not in cutover
    assert f"CREATE ROLE {ROLE} NOLOGIN" in source
    assert re.search(
        rf"ALTER ROLE {ROLE}\s+"
        r"NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION\s+"
        r"NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;",
        source,
    )
    assert f"ALTER ROLE {ROLE} RESET ALL;" in source
    assert "BEGIN;\nSET LOCAL lock_timeout = '5s';" in source
    assert "COMMIT;" in source

    executor = _block(source, "identity_binding_kernel_executor_preflight")
    assert "SESSION_USER <> 'home_agent_owner'" in executor
    assert "CURRENT_USER <> 'home_agent_owner'" in executor
    assert "role_row.rolsuper" in executor
    assert "requires the database owner" in executor

    preflight = _block(source, "identity_binding_kernel_role_ceremony_preflight")
    assert "SESSION_USER <> 'home_agent_owner'" in preflight
    assert "CURRENT_USER <> 'home_agent_owner'" in preflight
    assert "0015_current_authority_e5a" in preflight
    assert "identity binding kernel role ceremony requires revision 0015" in preflight
    assert "evaluate_current_identity_semantic_authority" in preflight
    assert "home_agent_identity_authority_kernel" in preflight
    assert "home_agent_binding_operator" in preflight
    assert "operations.principal_binding_authority_receipts" in preflight
    assert "identity.commit_authenticated_principal_binding_e5b(" in preflight
    assert "authority_receipt_id" in preflight
    assert "pg_catalog.pg_policy" in preflight
    assert "pg_catalog.pg_trigger" in preflight
    assert "pg_catalog.pg_constraint" in preflight
    assert "relation_row.relkind = 'i'" in preflight
    assert "dependency_row.deptype = 'o'" in preflight
    assert "identity binding kernel role already owns objects" in preflight


def test_e5b_kernel_role_ceremony_quarantines_sessions_and_memberships() -> None:
    source = _read(SCRIPT)
    cleanup = _block(source, "identity_binding_kernel_session_cleanup")
    membership_cleanup = source.split(
        "-- Remove every membership edge", 1
    )[1].split("DO $identity_binding_kernel_session_cleanup$", 1)[0]

    assert source.index(
        "ALTER ROLE home_agent_identity_binding_kernel NOLOGIN PASSWORD NULL"
    ) < source.index("-- Remove every membership edge")
    assert source.index("-- Remove every membership edge") < source.index(
        "DO $identity_binding_kernel_session_cleanup$"
    )
    assert source.index(
        "DO $identity_binding_kernel_session_cleanup$"
    ) < source.index("BEGIN;\nSET LOCAL lock_timeout = '5s';")
    assert "pg_catalog.pg_terminate_backend(activity.pid, 5000)" in cleanup
    assert "activity.pid <> pg_catalog.pg_backend_pid()" in cleanup
    assert cleanup.count("activity.backend_type = 'client backend'") == 2
    assert "activity.datname = pg_catalog.current_database()" in cleanup
    assert f"activity.usename = '{ROLE}'" in cleanup
    assert "remaining_sessions <> 0" in cleanup
    assert "role_row.rolcanlogin" in cleanup
    assert "role_row.rolpassword IS NOT NULL" in cleanup
    assert "FROM pg_catalog.pg_authid AS role_row" in cleanup
    assert "FROM pg_catalog.pg_auth_members AS membership" in cleanup
    assert f"parent.rolname = '{ROLE}'" in cleanup
    assert f"member.rolname = '{ROLE}'" in cleanup
    assert "session cleanup could not be proven" in cleanup

    assert "'REVOKE %I FROM %I CASCADE'" in membership_cleanup
    assert f"parent.rolname = '{ROLE}'" in membership_cleanup
    assert f"member.rolname = '{ROLE}'" in membership_cleanup
    assert (
        f"GRANT {ROLE} TO home_agent_owner\n"
        "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
    ) in source
    assert f"GRANT {ROLE} TO home_agent_binding_operator" not in source


def test_e5b_kernel_role_ceremony_scrubs_every_privilege_catalog() -> None:
    source = _read(SCRIPT)

    for expected in (
        "REVOKE ALL PRIVILEGES ON DATABASE %I",
        "REVOKE ALL PRIVILEGES ON SCHEMA %I",
        "REVOKE ALL PRIVILEGES ON %s %I.%I",
        "REVOKE SELECT (%3$I), INSERT (%3$I), UPDATE (%3$I)",
        "REVOKE ALL PRIVILEGES ON ROUTINE %I.%I(%s)",
        "REVOKE ALL PRIVILEGES ON TYPE %I.%I",
        "REVOKE ALL PRIVILEGES ON PARAMETER %I",
        "ALTER DEFAULT PRIVILEGES FOR ROLE %I%s",
        "WHEN 'r' THEN 'TABLES'",
        "WHEN 'S' THEN 'SEQUENCES'",
        "WHEN 'f' THEN 'ROUTINES'",
        "WHEN 'T' THEN 'TYPES'",
        "WHEN 'n' THEN 'SCHEMAS'",
    ):
        assert expected in source

    for predefined_role in (
        "pg_monitor",
        "pg_read_all_settings",
        "pg_read_all_stats",
        "pg_stat_scan_tables",
        "pg_read_all_data",
        "pg_write_all_data",
        "pg_read_server_files",
        "pg_write_server_files",
        "pg_execute_server_program",
        "pg_checkpoint",
        "pg_maintain",
        "pg_signal_backend",
        "pg_use_reserved_connections",
        "pg_create_subscription",
    ):
        assert predefined_role in source

    for catalog, acl_column in (
        ("pg_database", "datacl"),
        ("pg_namespace", "nspacl"),
        ("pg_class", "relacl"),
        ("pg_attribute", "attacl"),
        ("pg_proc", "proacl"),
        ("pg_type", "typacl"),
        ("pg_parameter_acl", "paracl"),
        ("pg_default_acl", "defaclacl"),
    ):
        assert f"pg_catalog.{catalog}" in source
        assert acl_column in source
    assert source.count("pg_catalog.aclexplode(") >= 16


def test_e5b_kernel_role_validation_proves_exact_inert_shape() -> None:
    source = _read(SCRIPT)
    validation = _block(source, "identity_binding_kernel_role_ceremony_validation")

    for expected in (
        "NOT role_row.rolcanlogin",
        "NOT role_row.rolinherit",
        "NOT role_row.rolsuper",
        "NOT role_row.rolcreatedb",
        "NOT role_row.rolcreaterole",
        "NOT role_row.rolreplication",
        "NOT role_row.rolbypassrls",
        "role_row.rolconnlimit = 0",
        "role_row.rolpassword IS NULL",
        "role_row.rolconfig IS NULL",
        "NOT membership.admin_option",
        "NOT membership.inherit_option",
        "membership.set_option",
        "membership.member = binding_kernel_oid",
        "setting_row.setrole = binding_kernel_oid",
        "dependency_row.deptype IN ('a', 'o')",
        "privilege_row.grantee = binding_kernel_oid",
        f"activity.usename = '{ROLE}'",
        "identity binding kernel role ceremony validation failed",
    ):
        assert expected in validation

    assert "FROM pg_catalog.pg_authid AS role_row" in validation
    assert validation.count("privilege_row.grantee = binding_kernel_oid") == 8
    assert (
        "WHERE membership.roleid = binding_kernel_oid" in validation
        and ") <> 1" in validation
    )
    assert "WHERE membership.member = binding_kernel_oid" in validation


def test_e5b_kernel_role_sql_heredoc_contains_only_sql_comments() -> None:
    sql = _sql(_read(SCRIPT))

    assert re.search(r"(?m)^-- .*reserved role", sql)
    assert re.search(r"(?m)^\s*#", sql) is None
