from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
from typing import Iterator

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
APPLY_GRANTS = ROOT.parents[1] / "home-agent-deploy/apply-grants.sh"
PROVISION_ROLES = ROOT.parents[1] / "home-agent-deploy/provision-roles.sh"
ADMIN_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_ADMIN_DATABASE_URL"
TEMPLATE_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_TEMPLATE_DATABASE"
OWNER_PASSWORD_FILE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_OWNER_PASSWORD_FILE"
RUN_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
SYSTEM_IDENTIFIER_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_SYSTEM_IDENTIFIER"
DATABASE_ALLOWLIST_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_DATABASE_ALLOWLIST"
REVISION_0007 = "0007_phase3_identity_authority"
REVISION_0010 = "0010_identity_erasure_source"
REVISION_0011 = "0011_identity_erasure_e1"
CASE_DATABASE = "home_agent"
ADMIN_DATABASE = "postgres"
ADMIN_HOST = "postgres"
SYSTEM_DATABASES = {"postgres", "template0", "template1"}
FOUNDATION_TABLES = (
    "privacy.person_erasure_scopes",
    "privacy.subject_retrieval_blocks",
    "operations.reviewed_identity_migration_erasure_receipts",
)
E1_SUPPORT_CONSTRAINTS = (
    "identity_erasure_principal_kind_authority",
    "identity_erasure_ha_binding_authority",
    "identity_erasure_confirmation_authority",
    "identity_erasure_request_authority",
    "identity_erasure_operation_request_source",
    "identity_erasure_impact_receipt_source",
    "identity_person_erasure_scope_commitment_shape",
)
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SENTINEL = re.compile(r"^[0-9a-f]{64}$")
SYSTEM_IDENTIFIER = re.compile(r"^[0-9]{10,24}$")
MANAGED_ROLE_NAMES = (
    "home_agent_owner",
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
    "home_agent_identity_migration",
    "home_agent_identity_finalizer",
    "home_agent_identity_kernel",
    "home_agent_identity_finalizer_kernel",
    "home_agent_identity_erasure_kernel",
)
CLUSTER_TAMPER_ROLES = (
    "e1_unreviewed_support_acl",
    "e1_unreviewed_api_parent",
    "e1_unreviewed_api_member",
    "e1_unreviewed_cluster_superuser",
    "e1_unreviewed_owner_member",
    "e1_unreviewed_plain_role",
    "e1_unreviewed_predefined_member",
    "e1_unreviewed_worker_member",
)


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.fail(f"required E1 PostgreSQL harness environment is missing: {name}")
    return value


def database_url(database: str) -> str:
    if not IDENTIFIER.fullmatch(database):
        raise ValueError(f"unsafe generated database name: {database!r}")
    url = make_url(_required_environment(ADMIN_DATABASE_ENV)).set(database=database)
    return url.render_as_string(hide_password=False)


def _admin_engine():
    return create_engine(
        _required_environment(ADMIN_DATABASE_ENV),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )


def _terminate_database_connections(connection, database: str) -> None:
    connection.execute(
        text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :database AND pid <> pg_backend_pid()"
        ),
        {"database": database},
    )


def _destructive_database_allowlist() -> set[str]:
    sentinel = _required_environment(RUN_SENTINEL_ENV)
    if not SENTINEL.fullmatch(sentinel):
        pytest.fail("E1 PostgreSQL run sentinel is not a 256-bit lowercase hex value")
    raw_allowlist = _required_environment(DATABASE_ALLOWLIST_ENV)
    values = raw_allowlist.split(",")
    if (
        not values
        or any(not IDENTIFIER.fullmatch(value) for value in values)
        or len(values) != len(set(values))
    ):
        pytest.fail("E1 PostgreSQL destructive database allowlist is invalid")
    return set(values)


def _assert_database_url_contract(url, *, expected_database: str) -> None:
    if (
        url.drivername != "postgresql+psycopg"
        or url.username != "home_agent_owner"
        or url.host != ADMIN_HOST
        or (url.port or 5432) != 5432
        or url.database != expected_database
        or not url.password
        or bool(url.query)
    ):
        pytest.fail("E1 database URL is outside the fixed local-container contract")


def _assert_admin_url_contract() -> None:
    url = make_url(_required_environment(ADMIN_DATABASE_ENV))
    _assert_database_url_contract(url, expected_database=ADMIN_DATABASE)


def assert_guarded_cluster() -> set[str]:
    _assert_admin_url_contract()
    sentinel = _required_environment(RUN_SENTINEL_ENV)
    expected_system_identifier = _required_environment(SYSTEM_IDENTIFIER_ENV)
    if not SENTINEL.fullmatch(sentinel):
        pytest.fail("E1 PostgreSQL run sentinel is invalid")
    if not SYSTEM_IDENTIFIER.fullmatch(expected_system_identifier):
        pytest.fail("E1 PostgreSQL system identifier is invalid")
    allowlist = _destructive_database_allowlist()
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            observed_sentinel, observed_system_identifier = connection.execute(
                text(
                    "SELECT current_setting('home_agent_e1.run_id', true), "
                    "system_identifier::text FROM pg_control_system()"
                )
            ).one()
            inventory = set(
                connection.execute(
                    text("SELECT datname FROM pg_database ORDER BY datname")
                ).scalars()
            )
    finally:
        engine.dispose()
    if observed_sentinel != sentinel:
        pytest.fail("E1 PostgreSQL cluster sentinel mismatch")
    if observed_system_identifier != expected_system_identifier:
        pytest.fail("E1 PostgreSQL system identifier mismatch")
    if (
        not SYSTEM_DATABASES <= inventory
        or not inventory <= SYSTEM_DATABASES | allowlist
    ):
        pytest.fail(f"E1 PostgreSQL database inventory is not exact: {inventory!r}")
    return inventory


def assert_guarded_database_url(database_url_value: str) -> str:
    url = make_url(database_url_value)
    database = str(url.database or "")
    _assert_database_url_contract(url, expected_database=database)
    allowlist = _destructive_database_allowlist()
    if database not in allowlist:
        pytest.fail(f"database is outside the exact E1 allowlist: {database!r}")
    inventory = assert_guarded_cluster()
    if database not in inventory:
        pytest.fail(f"guarded E1 database does not exist: {database!r}")
    engine = create_engine(database_url_value)
    try:
        with engine.connect() as connection:
            observed = connection.execute(text("SELECT current_database()"))
            observed_database = observed.scalar_one()
    except BaseException as error:
        pytest.fail(f"guarded E1 database is unavailable: {database!r}: {error}")
    finally:
        engine.dispose()
    if observed_database != database:
        pytest.fail(f"database identity mismatch for destructive target: {database!r}")
    return database


def _assert_guarded_database(database: str) -> None:
    assert_guarded_database_url(database_url(database))


def clone_database(source: str, target: str) -> None:
    if not IDENTIFIER.fullmatch(source) or not IDENTIFIER.fullmatch(target):
        raise ValueError("database clone names must be generated identifiers")
    allowlist = _destructive_database_allowlist()
    if source not in allowlist or target not in allowlist:
        pytest.fail("clone source or target is outside the exact E1 allowlist")
    inventory = assert_guarded_cluster()
    if source not in inventory or target in inventory:
        pytest.fail("E1 template/target inventory is not safe for cloning")
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            template = (
                connection.execute(
                    text(
                        "SELECT datistemplate, datallowconn, "
                        "shobj_description(oid, 'pg_database') AS description "
                        "FROM pg_database WHERE datname = :name"
                    ),
                    {"name": source},
                )
                .mappings()
                .one()
            )
            expected_description = (
                f"home-agent-e1:{_required_environment(RUN_SENTINEL_ENV)}:"
                f"{REVISION_0007}"
            )
            if template != {
                "datistemplate": True,
                "datallowconn": False,
                "description": expected_description,
            }:
                pytest.fail("revision-0007 template guard is invalid")
            connection.exec_driver_sql(
                f'CREATE DATABASE "{target}" WITH TEMPLATE "{source}" '
                'OWNER "home_agent_owner"'
            )
    finally:
        engine.dispose()
    _assert_guarded_database(target)


def drop_database(database: str) -> None:
    if not IDENTIFIER.fullmatch(database):
        raise ValueError("database cleanup name must be a generated identifier")
    inventory = assert_guarded_cluster()
    if database not in inventory or database not in _destructive_database_allowlist():
        pytest.fail(f"database is not an authorized cleanup target: {database!r}")
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            _terminate_database_connections(connection, database)
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        engine.dispose()


def restore_cluster_role_contract(database_url_value: str) -> None:
    """Undo only reviewed cluster-global tamper after sentinel verification."""

    assert_guarded_database_url(database_url_value)
    role_names = ",".join(
        "'" + role_name.replace("'", "''") + "'" for role_name in CLUSTER_TAMPER_ROLES
    )
    managed_role_names = ",".join(
        "'" + role_name.replace("'", "''") + "'" for role_name in MANAGED_ROLE_NAMES
    )
    engine = create_engine(database_url_value, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(
                """
                DO $cleanup_parameter_acl$
                DECLARE
                  parameter_grant record;
                BEGIN
                  FOR parameter_grant IN
                    SELECT DISTINCT parameter_acl.parname,
                           CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                                ELSE pg_catalog.format('%%I', grantee.rolname)
                           END AS grantee_sql
                      FROM pg_catalog.pg_parameter_acl AS parameter_acl
                     CROSS JOIN LATERAL
                           pg_catalog.aclexplode(parameter_acl.paracl) AS acl
                      LEFT JOIN pg_catalog.pg_roles AS grantee
                        ON grantee.oid = acl.grantee
                  LOOP
                    EXECUTE pg_catalog.format(
                      'REVOKE ALL ON PARAMETER %%I FROM %%s',
                      parameter_grant.parname,
                      parameter_grant.grantee_sql
                    );
                  END LOOP;
                END
                $cleanup_parameter_acl$;
                """
            )
            connection.exec_driver_sql(
                f"""
                DO $cleanup_memberships$
                DECLARE
                  membership record;
                BEGIN
                  FOR membership IN
                    SELECT parent.rolname AS parent_name,
                           member.rolname AS member_name
                      FROM pg_catalog.pg_auth_members AS edge
                      JOIN pg_catalog.pg_roles AS parent
                        ON parent.oid = edge.roleid
                      JOIN pg_catalog.pg_roles AS member
                        ON member.oid = edge.member
                     WHERE parent.rolname IN ({role_names})
                        OR member.rolname IN ({role_names})
                        OR parent.rolname IN ({managed_role_names})
                        OR member.rolname IN ({managed_role_names})
                  LOOP
                    EXECUTE pg_catalog.format(
                      'REVOKE %%I FROM %%I',
                      membership.parent_name, membership.member_name
                    );
                  END LOOP;
                END
                $cleanup_memberships$;
                """
            )
            for role_name in CLUSTER_TAMPER_ROLES:
                exists = connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"
                    ),
                    {"role": role_name},
                ).scalar_one()
                if exists:
                    connection.exec_driver_sql(f'DROP OWNED BY "{role_name}"')
                    connection.exec_driver_sql(f'DROP ROLE "{role_name}"')
            for role_name in MANAGED_ROLE_NAMES:
                if role_name != "home_agent_owner":
                    connection.exec_driver_sql(f'ALTER ROLE "{role_name}" RESET ALL')
            connection.exec_driver_sql('ALTER ROLE "home_agent_owner" RESET ALL')
    finally:
        engine.dispose()


def assert_identity_kernel_ownership(expected_database: str | None) -> None:
    assert_guarded_cluster()
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            kernel_oid = connection.execute(
                text("SELECT oid FROM pg_roles WHERE rolname = :role"),
                {"role": "home_agent_identity_kernel"},
            ).scalar_one()
            caller_oid = connection.execute(
                text("SELECT oid FROM pg_roles WHERE rolname = :role"),
                {"role": "home_agent_identity_migration"},
            ).scalar_one()
            caller_ownership = connection.execute(
                text(
                    "SELECT count(*) FROM pg_shdepend "
                    "WHERE refobjid = :role AND deptype = 'o'"
                ),
                {"role": caller_oid},
            ).scalar_one()
            dependencies = (
                connection.execute(
                    text(
                        "SELECT dbid, classid, objid, objsubid FROM pg_shdepend "
                        "WHERE refobjid = :role AND deptype = 'o' "
                        "ORDER BY dbid, classid, objid, objsubid"
                    ),
                    {"role": kernel_oid},
                )
                .tuples()
                .all()
            )
    finally:
        admin.dispose()
    assert caller_ownership == 0
    if expected_database is None:
        assert dependencies == []
        return
    database_engine = create_engine(database_url(expected_database))
    try:
        with database_engine.connect() as connection:
            database_oid = connection.execute(
                text("SELECT oid FROM pg_database WHERE datname = current_database()")
            ).scalar_one()
            function_oids = {
                connection.execute(
                    text("SELECT to_regprocedure(:signature)::oid"),
                    {"signature": signature},
                ).scalar_one()
                for signature in (
                    "operations.reviewed_identity_migration_capabilities()",
                    "operations.register_reviewed_identity_migration(jsonb)",
                    "operations.bump_reviewed_identity_migration_replay_guard()",
                )
            }
            pg_proc_oid = connection.execute(
                text("SELECT 'pg_proc'::regclass::oid")
            ).scalar_one()
    finally:
        database_engine.dispose()
    assert len(dependencies) == 3
    assert {
        (dbid, classid, objid, objsubid)
        for dbid, classid, objid, objsubid in dependencies
    } == {
        (database_oid, pg_proc_oid, function_oid, 0) for function_oid in function_oids
    }


def assert_cluster_role_baseline() -> None:
    assert_guarded_cluster()
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            rogue_count = connection.execute(
                text(
                    "SELECT count(*) FROM pg_roles "
                    "WHERE rolname = ANY(CAST(:roles AS text[]))"
                ),
                {"roles": list(CLUSTER_TAMPER_ROLES)},
            ).scalar_one()
            role_flags = (
                connection.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole, "
                        "rolreplication, rolinherit, rolbypassrls, rolconfig, "
                        "rolname "
                        "FROM pg_roles WHERE rolname IN "
                        "('home_agent_api','home_agent_worker') "
                        "ORDER BY rolname"
                    )
                )
                .mappings()
                .all()
            )
            membership_rows = [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT parent.rolname::text, member.rolname::text, "
                        "grantor.rolname::text, edge.admin_option, "
                        "edge.inherit_option, edge.set_option "
                        "FROM pg_auth_members edge "
                        "JOIN pg_roles parent ON parent.oid = edge.roleid "
                        "JOIN pg_roles member ON member.oid = edge.member "
                        "JOIN pg_roles grantor ON grantor.oid = edge.grantor "
                        "WHERE parent.rolname::text = ANY(CAST(:roles AS text[])) "
                        "OR member.rolname::text = ANY(CAST(:roles AS text[])) "
                        "ORDER BY parent.rolname, member.rolname"
                    ),
                    {"roles": list(MANAGED_ROLE_NAMES)},
                )
            ]
            parameter_acl_item_count = connection.execute(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_parameter_acl "
                    "CROSS JOIN LATERAL "
                    "pg_catalog.aclexplode(paracl) AS acl"
                )
            ).scalar_one()
            owner_role_config = connection.execute(
                text(
                    "SELECT rolconfig FROM pg_roles "
                    "WHERE rolname = 'home_agent_owner'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    assert rogue_count == 0
    assert parameter_acl_item_count == 0
    assert owner_role_config is None
    assert role_flags == [
        {
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolinherit": False,
            "rolbypassrls": False,
            "rolconfig": ["statement_timeout=15s"],
            "rolname": "home_agent_api",
        },
        {
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
            "rolreplication": False,
            "rolinherit": False,
            "rolbypassrls": False,
            "rolconfig": ["statement_timeout=60s"],
            "rolname": "home_agent_worker",
        },
    ]
    assert membership_rows == [
        (
            "home_agent_identity_erasure_kernel",
            "home_agent_owner",
            "home_agent_owner",
            False,
            False,
            True,
        ),
        (
            "home_agent_identity_finalizer_kernel",
            "home_agent_owner",
            "home_agent_owner",
            False,
            False,
            True,
        ),
        (
            "home_agent_identity_kernel",
            "home_agent_owner",
            "home_agent_owner",
            False,
            False,
            True,
        ),
        (
            "pg_read_all_settings",
            "home_agent_backup",
            "home_agent_owner",
            False,
            True,
            True,
        ),
    ]


@pytest.fixture(name="e1_0010_database")
def _e1_0010_database() -> Iterator[str]:
    template = _required_environment(TEMPLATE_DATABASE_ENV)
    if not IDENTIFIER.fullmatch(template):
        pytest.fail("E1 PostgreSQL template database name is unsafe")
    if _destructive_database_allowlist() != {template, CASE_DATABASE}:
        pytest.fail("E1 admission database allowlist is not exact")
    database = CASE_DATABASE
    try:
        assert_identity_kernel_ownership(None)
        clone_database(template, database)
        provision = run_provision_roles(database_url(database))
        assert provision.returncode == 0, provision.stdout + provision.stderr
        assert_cluster_role_baseline()
        alembic_upgrade(database_url(database), REVISION_0010)
        replay = run_apply_grants(database_url(database))
        assert replay.returncode == 0, replay.stdout + replay.stderr
        assert_revision(database_url(database), REVISION_0010)
        assert_identity_kernel_ownership(database)
        yield database_url(database)
    finally:
        if database in assert_guarded_cluster():
            try:
                restore_cluster_role_contract(database_url(database))
                provision = run_provision_roles(database_url(database))
                assert provision.returncode == 0, provision.stdout + provision.stderr
                assert_cluster_role_baseline()
            finally:
                drop_database(database)
            assert_identity_kernel_ownership(None)
            assert_cluster_role_baseline()


@contextmanager
def _alembic_database(database_url_value: str) -> Iterator[None]:
    old_database_url = os.environ.get("HOME_AGENT_DATABASE_URL")
    old_cwd = Path.cwd()
    os.environ["HOME_AGENT_DATABASE_URL"] = database_url_value
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(old_cwd)
        if old_database_url is None:
            os.environ.pop("HOME_AGENT_DATABASE_URL", None)
        else:
            os.environ["HOME_AGENT_DATABASE_URL"] = old_database_url


def alembic_upgrade(database_url_value: str, revision: str = REVISION_0011) -> None:
    with _alembic_database(database_url_value):
        command.upgrade(Config(str(ALEMBIC_INI)), revision)


def alembic_downgrade(database_url_value: str, revision: str = REVISION_0010) -> None:
    with _alembic_database(database_url_value):
        command.downgrade(Config(str(ALEMBIC_INI)), revision)


def exception_sqlstate(error: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, "orig", None)
        sqlstate = getattr(original, "sqlstate", None) or getattr(
            current, "sqlstate", None
        )
        if sqlstate:
            return str(sqlstate)
        current = current.__cause__ or current.__context__
    return None


def exception_text(error: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(messages)


def assert_revision(database_url_value: str, revision: str) -> None:
    engine = create_engine(database_url_value)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                == revision
            )
    finally:
        engine.dispose()


def assert_failed_upgrade_is_atomic(database_url_value: str) -> None:
    engine = create_engine(database_url_value)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                == REVISION_0010
            )
            for table in FOUNDATION_TABLES:
                assert (
                    connection.execute(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": table},
                    ).scalar_one()
                    is None
                )
            assert (
                connection.execute(
                    text(
                        "SELECT to_regnamespace('e1_validation') IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM pg_attribute WHERE ("
                        "(attrelid = 'privacy.erasure_requests'::regclass AND "
                        "attname = 'identity_person_scope_commitment') OR "
                        "(attrelid = 'identity.ha_user_bindings'::regclass AND "
                        "attname = 'identity_erasure_binding_valid_until')) "
                        "AND attnum > 0 AND NOT attisdropped)"
                    )
                ).scalar_one()
                is True
            )
            installed_support = connection.execute(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conname = ANY(CAST(:names AS text[]))"
                ),
                {"names": list(E1_SUPPORT_CONSTRAINTS)},
            ).scalar_one()
            assert installed_support == 0
    finally:
        engine.dispose()


def _run_deploy_script(
    database_url_value: str, script: Path
) -> subprocess.CompletedProcess[str]:
    password_file = _required_environment(OWNER_PASSWORD_FILE_ENV)
    url = make_url(database_url_value)
    environment = os.environ.copy()
    environment.update(
        {
            "PGHOST": str(url.host or "postgres"),
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": str(url.database),
            "PGUSER": str(url.username or "home_agent_owner"),
            "POSTGRES_OWNER_PASSWORD_FILE": password_file,
        }
    )
    return subprocess.run(
        ["/bin/sh", str(script)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )


def run_apply_grants(database_url_value: str) -> subprocess.CompletedProcess[str]:
    return _run_deploy_script(database_url_value, APPLY_GRANTS)


def run_provision_roles(database_url_value: str) -> subprocess.CompletedProcess[str]:
    return _run_deploy_script(database_url_value, PROVISION_ROLES)


def direct_acl_count(connection, role: str) -> int:
    return int(
        connection.execute(
            text(
                "WITH target_role AS (SELECT oid FROM pg_roles "
                "WHERE rolname = :role) SELECT ("
                "SELECT count(*) FROM pg_database d "
                "CROSS JOIN LATERAL aclexplode(d.datacl) acl, target_role r "
                "WHERE d.datname = current_database() AND acl.grantee = r.oid"
                ") + (SELECT count(*) FROM pg_namespace n "
                "CROSS JOIN LATERAL aclexplode(n.nspacl) acl, target_role r "
                "WHERE n.nspname IN ('public','ingest','identity','knowledge',"
                "'engagement','privacy','operations','media') "
                "AND acl.grantee = r.oid) + ("
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(c.relacl) acl, target_role r "
                "WHERE n.nspname IN ('public','ingest','identity','knowledge',"
                "'engagement','privacy','operations','media') "
                "AND acl.grantee = r.oid) + ("
                "SELECT count(*) FROM pg_attribute a JOIN pg_class c "
                "ON c.oid = a.attrelid JOIN pg_namespace n "
                "ON n.oid = c.relnamespace "
                "CROSS JOIN LATERAL aclexplode(a.attacl) acl, target_role r "
                "WHERE n.nspname IN ('public','ingest','identity','knowledge',"
                "'engagement','privacy','operations','media') "
                "AND acl.grantee = r.oid) + ("
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid = p.pronamespace "
                "CROSS JOIN LATERAL aclexplode(p.proacl) acl, target_role r "
                "WHERE n.nspname IN ('public','ingest','identity','knowledge',"
                "'engagement','privacy','operations','media') "
                "AND acl.grantee = r.oid) + ("
                "SELECT count(*) FROM pg_type t JOIN pg_namespace n "
                "ON n.oid = t.typnamespace "
                "CROSS JOIN LATERAL aclexplode(t.typacl) acl, target_role r "
                "WHERE n.nspname IN ('public','ingest','identity','knowledge',"
                "'engagement','privacy','operations','media') "
                "AND acl.grantee = r.oid) + ("
                "SELECT count(*) FROM pg_default_acl d "
                "CROSS JOIN LATERAL aclexplode(d.defaclacl) acl, target_role r "
                "WHERE acl.grantee = r.oid)"
            ),
            {"role": role},
        ).scalar_one()
    )
