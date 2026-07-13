from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "stack/home-agent-deploy/provision-roles.sh"
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"
ROLE_DOC = ROOT / "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md"
ROLE = "home_agent_identity_erasure_kernel"


def test_kernel_role_is_nologin_noninheriting_and_owner_set_only() -> None:
    source = PROVISION.read_text(encoding="utf-8")

    assert f"CREATE ROLE {ROLE} NOLOGIN" in source
    assert (
        f"ALTER ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEDB\n"
        "  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;"
    ) in source
    assert f"ALTER ROLE {ROLE} RESET ALL;" in source
    assert f"REVOKE ALL PRIVILEGES ON DATABASE home_agent FROM {ROLE};" in source
    assert (
        f"GRANT {ROLE} TO home_agent_owner\n"
        "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
    ) in source
    assert f"WHERE parent.rolname = '{ROLE}'" in source
    assert "AND member.rolname <> 'home_agent_owner'" in source


def test_kernel_role_has_no_online_secret_or_service_contract() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    role_doc = ROLE_DOC.read_text(encoding="utf-8")

    assert f"GRANT CONNECT ON DATABASE home_agent TO {ROLE}" not in source
    assert "identity_erasure_kernel_password" not in source
    assert "database_url_identity_erasure" not in source
    assert "NOLOGIN" in role_doc
    assert "no erasure function or worker is installed" in role_doc
    assert "closure_limit_exceeded" in role_doc
    assert "migration_run_erased" in role_doc


def test_grant_replay_ends_with_global_kernel_quarantine() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    quarantine = source.index("DO $identity_erasure_kernel_quarantine$")
    identity_acl = source.index(
        'psql -v ON_ERROR_STOP=1 -f "$script_dir/identity-api-acl.sql"'
    )

    assert quarantine < identity_acl
    assert "REVOKE ALL PRIVILEGES ON DATABASE %I" in source[quarantine:]
    assert (
        "public, ingest, identity, knowledge, engagement, privacy, operations, media"
        in source[quarantine:].replace("\n      ", " ")
    )
    for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        assert (
            f"REVOKE ALL PRIVILEGES ON ALL {object_kind} IN SCHEMA"
            in source[quarantine:]
        )
    assert "REVOKE USAGE ON TYPE %I.%I" in source[quarantine:]
    assert "pg_catalog.pg_attribute" in source[quarantine:]
    assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)" in source[
        quarantine:
    ]
    assert "pg_catalog.pg_shdepend" in source[quarantine:]
    assert "kernel_role.rolconfig IS NULL" in source[quarantine:]
    assert source[quarantine:].count(
        "FROM home_agent_identity_erasure_kernel;"
    ) >= 8
    assert source.rstrip().endswith(
        'psql -v ON_ERROR_STOP=1 -f "$script_dir/identity-api-acl.sql"'
    )
    assert "DO $identity_erasure_e1_acl$" in source
    assert "privacy.person_erasure_scopes" in source
    assert "identity_person_scope_commitment" not in source[
        source.index("DO $identity_erasure_e1_acl$") : quarantine
    ]


def test_replay_scrubs_column_public_and_unknown_grantee_authority() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    identity_acl = (
        ROOT / "stack/home-agent-deploy/identity-api-acl.sql"
    ).read_text(encoding="utf-8")

    for contract in (source, identity_acl):
        assert "DO $identity_api_acl_reset$" in contract
        assert "ARRAY['home_agent_api','PUBLIC']" in contract
        assert "pg_catalog.pg_attribute" in contract
        assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)" in contract
        assert "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC" in contract

    e1 = source[source.index("DO $identity_erasure_e1_acl$") :]
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles" in e1
    assert "role_row.rolname <> 'home_agent_owner'" in e1
    assert "identity.confirmation_artifacts" in e1
    assert "identity.ha_user_bindings" in e1
    assert "privacy.erasure_requests" in e1
