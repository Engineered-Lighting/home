from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOGIN_ROLE = "home_agent_identity_cutover"
KERNEL_ROLE = "home_agent_identity_cutover_kernel"
AUTHORITY_KERNEL_ROLE = "home_agent_identity_authority_kernel"
SERVICE = "identity-cutover"
PINNED_E4_CATALOG_SHA256 = (
    "a96aeb68c7c5656988088ae74539760c6a811320849f01c122e02141f87eff27"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _compose_service(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        source,
    )
    assert match is not None
    return match.group(1)


def _grant_section(marker: str) -> str:
    source = _read("stack/home-agent-deploy/apply-grants.sh")
    return source.split(f"DO ${marker}$", 1)[1].split(f"${marker}$;", 1)[0]


def test_cutover_role_pair_is_distinct_expired_and_non_escalatable() -> None:
    historical_roles = _read("stack/home-agent-deploy/provision-roles.sh")
    roles = _read(
        "stack/home-agent-deploy/provision-identity-cutover-roles.sh"
    )
    hba = _read("stack/home-agent-deploy/postgres-pg_hba.conf")

    assert LOGIN_ROLE not in historical_roles
    assert KERNEL_ROLE not in historical_roles
    assert AUTHORITY_KERNEL_ROLE not in historical_roles
    assert "0013_identity_finalizer_e3" in roles
    assert "identity cutover role ceremony requires revision 0013" in roles
    assert f"CREATE ROLE {LOGIN_ROLE} LOGIN" in roles
    assert f"CREATE ROLE {KERNEL_ROLE} NOLOGIN" in roles
    assert f"CREATE ROLE {AUTHORITY_KERNEL_ROLE} NOLOGIN" in roles
    assert re.search(
        rf"ALTER ROLE {LOGIN_ROLE} PASSWORD "
        rf":'identity_cutover_password'[\s\S]*?NOSUPERUSER NOCREATEDB "
        rf"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS\s+"
        rf"CONNECTION LIMIT 1 VALID UNTIL '1970-01-01 00:00:00\+00';",
        roles,
    )
    assert re.search(
        rf"ALTER ROLE {KERNEL_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB\s+"
        rf"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS "
        rf"CONNECTION LIMIT 0;",
        roles,
    )
    assert re.search(
        rf"ALTER ROLE {AUTHORITY_KERNEL_ROLE}\s+"
        rf"NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION\s+"
        rf"NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;",
        roles,
    )
    assert (
        f"GRANT {KERNEL_ROLE} TO home_agent_owner\n"
        "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
    ) in roles
    assert (
        f"GRANT {AUTHORITY_KERNEL_ROLE} TO home_agent_owner\n"
        "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
    ) in roles
    assert f"GRANT {KERNEL_ROLE} TO {LOGIN_ROLE}" not in roles
    assert f"GRANT {AUTHORITY_KERNEL_ROLE} TO {LOGIN_ROLE}" not in roles
    assert (
        f"REVOKE CREATE, TEMPORARY ON DATABASE home_agent\n  FROM {LOGIN_ROLE};"
    ) in roles
    assert (
        "REVOKE ALL PRIVILEGES ON DATABASE home_agent\n"
        f"  FROM {LOGIN_ROLE}, {KERNEL_ROLE},\n"
        f"       {AUTHORITY_KERNEL_ROLE};"
    ) in roles
    cleanup = roles.split(
        "DO $identity_cutover_role_session_cleanup$", 1
    )[1].split("$identity_cutover_role_session_cleanup$;", 1)[0]
    assert roles.index("ALTER ROLE %I VALID UNTIL %L") < roles.index(
        "DO $identity_cutover_role_ceremony_preflight$"
    )
    assert "pg_catalog.pg_terminate_backend(activity.pid, 5000)" in cleanup
    assert "FROM pg_catalog.pg_stat_activity AS activity" in cleanup
    assert "remaining_sessions <> 0" in cleanup
    assert (
        "identity cutover role session cleanup could not be proven" in cleanup
    )
    validation = roles.split(
        "DO $identity_cutover_role_ceremony_validation$", 1
    )[1].split("$identity_cutover_role_ceremony_validation$;", 1)[0]
    assert "FROM pg_catalog.pg_stat_activity AS activity" in validation
    assert f"activity.usename = '{LOGIN_ROLE}'" in validation

    hba_rules = [
        line.split()
        for line in hba.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert hba_rules[:6] == [
        ["local", "home_agent", LOGIN_ROLE, "scram-sha-256"],
        ["local", "all", LOGIN_ROLE, "reject"],
        ["host", "home_agent", LOGIN_ROLE, "0.0.0.0/0", "scram-sha-256"],
        ["host", "all", LOGIN_ROLE, "0.0.0.0/0", "reject"],
        ["host", "home_agent", LOGIN_ROLE, "::/0", "scram-sha-256"],
        ["host", "all", LOGIN_ROLE, "::/0", "reject"],
    ]

    for setting in (
        "default_transaction_isolation =\n  'serializable'",
        "statement_timeout = '120s'",
        "lock_timeout = '5s'",
        "idle_in_transaction_session_timeout =\n  '15s'",
        "transaction_timeout = '180s'",
        "log_parameter_max_length_on_error = 0",
    ):
        assert f"ALTER ROLE {LOGIN_ROLE} SET {setting};" in roles


def test_cutover_secret_pair_is_independent_atomic_and_exactly_materialized() -> None:
    bootstrap = _read("stack/home-agent-deploy/bootstrap-secrets.sh")
    additive = _read(
        "stack/home-agent-deploy/add-identity-cutover-role-secrets.sh"
    )
    materializer = _read("stack/home-agent-deploy/materialize-secrets.sh")
    preflight = _read(
        "stack/home-agent-deploy/preflight-identity-cutover-roles.sh"
    )
    main_preflight = _read("stack/home-agent-deploy/preflight.sh")

    assert "identity_cutover_password" not in bootstrap
    assert "identity-cutover/" not in bootstrap

    assert 'target="$master_root/identity-cutover"' in additive
    assert "openssl rand -hex 32" in additive
    assert "mv -T --no-clobber" in additive
    assert "already exists or is partial" in additive
    assert "chmod 0600" in additive
    assert "chown root:root" in additive
    assert "materialize-secrets.sh" in additive
    assert 'echo "$password"' not in additive
    for other_secret in (
        "postgres_owner_password",
        "postgres_api_password",
        "postgres_binding_operator_password",
        "postgres_identity_migration_password",
        "postgres_identity_finalizer_password",
        "postgres_ingest_password",
        "postgres_worker_password",
        "postgres_erasure_password",
        "postgres_rollout_password",
        "postgres_backup_password",
    ):
        assert other_secret in additive

    assert "identity-cutover/postgres_identity_cutover_password" in materializer
    assert (
        "install_exact_single_secret_service identity-cutover" in materializer
    )
    assert (
        "identity-cutover/database_url_identity_cutover database_url"
        in materializer
    )
    assert (
        "install_exact_secret_pair_service "
        "provision-identity-cutover-roles" in materializer
    )
    assert "postgres_owner_password postgres_owner_password" in materializer
    assert (
        "identity-cutover/postgres_identity_cutover_password" in materializer
    )

    for expected in (
        "identity cutover master secret directory must contain exactly two files",
        "identity cutover password is not lowercase hex",
        "identity cutover password has the wrong length",
        "identity cutover password must differ from every database role",
        f"postgresql+psycopg://{LOGIN_ROLE}:",
        "identity cutover runtime directory must contain only database_url",
    ):
        assert expected in preflight
    assert "historical role provisioner must not receive the E4 credential" in (
        preflight
    )
    assert "preflight-identity-cutover-roles.sh" in main_preflight


def test_cutover_secret_publication_has_non_regenerating_resume_contract() -> None:
    additive = _read(
        "stack/home-agent-deploy/add-identity-cutover-role-secrets.sh"
    )
    materializer = _read("stack/home-agent-deploy/materialize-secrets.sh")
    preflight = _read(
        "stack/home-agent-deploy/preflight-identity-cutover-roles.sh"
    )
    docs = _read("stack/home-agent-deploy/IDENTITY-CUTOVER-ROLE.md")

    assert "--resume-existing" in additive
    assert "validate_published_cutover_pair" in additive
    assert "validate_existing_role_passwords" in additive
    assert "identity cutover master secret set is preserved" in additive
    assert (
        additive.index('if [ "$resume_existing" -eq 1 ]')
        < additive.index("password=\"$(openssl rand -hex 32)\"")
    )
    create_branch = additive.split(
        'if [ "$resume_existing" -eq 1 ]; then', 1
    )[1]
    resume_arm, create_arm = create_branch.split("else", 1)
    assert "validate_published_cutover_pair" in resume_arm
    assert "openssl" not in resume_arm
    assert "openssl rand -hex 32" in create_arm
    assert 'wc -c < "$target/postgres_identity_cutover_password"' in additive
    assert "published identity cutover database URL is malformed" in additive
    assert "stale identity cutover master publication exists" in additive
    assert "stale identity cutover runtime publication exists" in additive
    assert ".identity-cutover.previous.*" in additive
    for source in (materializer, preflight):
        assert "orphaned identity cutover runtime exists without its master" in (
            source
        )
        assert "-name '.identity-cutover.previous.*'" in source
        assert "-name 'provision-identity-cutover-roles'" in source
    assert "role-secret preflight passed (not installed)" in preflight

    for source in (additive, materializer, preflight):
        assert '[ ! -L "$secrets_root" ]' in source
        assert '[ ! -L "$master_root" ]' in source
        assert '[ ! -L "$runtime_root" ]' in source
    assert "stale runtime publication exists" in materializer
    assert "-name \".$service.previous.*\"" in materializer

    assert "Safe recovery after master publication" in docs
    assert '"$secrets_root" --resume-existing' in docs
    assert "does not invoke the random generator" in docs
    assert "never writes the E4 master pair" in docs
    assert "not a live\ndeployment procedure" in docs


def test_hosted_gate_exercises_real_secret_lifecycle_and_compose_render() -> None:
    lifecycle = _read(
        "stack/home-agent-deploy/test-identity-cutover-secret-lifecycle.sh"
    )
    compose = _read("stack/home-agent-compose.yml")
    workflow = _read(".github/workflows/home-agent-e1-postgres.yml")

    assert "HOME_AGENT_SECRET_LIFECYCLE_RUNNER_ENVIRONMENT" in lifecycle
    assert '"github-hosted"' in lifecycle
    assert "bootstrap-secrets.sh" in lifecycle
    assert "add-identity-cutover-role-secrets.sh" in lifecycle
    assert "materialize-secrets.sh" in lifecycle
    assert "preflight-identity-cutover-roles.sh" in lifecycle
    assert "master_fingerprint" in lifecycle
    assert "--resume-existing" in lifecycle
    assert 'PATH="$workspace/no-generator:$PATH"' in lifecycle
    for rejection_case in (
        "partial-target",
        "symlink-root",
        "symlink-master",
        "symlink-runtime",
        "symlink-runtime-target",
        "stale-master-publication",
        "stale-master-previous-publication",
        "stale-runtime-publication",
        "stale-provisioner-publication",
        "orphaned-runtime-materialize",
        "orphaned-runtime-preflight",
        "malformed-historical",
        "malformed-resume",
        "mismatched-url-resume",
        "matching-resume",
    ):
        assert rejection_case in lifecycle
    assert "pristine-rematerialize" in lifecycle
    assert "pristine-preflight" in lifecycle
    assert "docker compose --profile operator" in lifecycle
    assert "config --format json" in lifecycle
    assert "identity-cutover" in lifecycle
    assert "provision-identity-cutover-roles" in lifecycle
    assert "secret-output-detected" in lifecycle
    assert 'cat "$output_log"' not in lifecycle
    required_compose_environment = set(
        re.findall(r"\$\{(HOME_AGENT_[A-Z0-9_]+):\?", compose)
    )
    assert required_compose_environment
    assert not {
        name
        for name in required_compose_environment
        if f"{name}=" not in lifecycle
    }

    lifecycle_path = (
        "stack/home-agent-deploy/"
        "test-identity-cutover-secret-lifecycle.sh"
    )
    assert workflow.count(f'- "{lifecycle_path}"') == 2
    assert workflow.count(
        '- "stack/home-agent-deploy/postgres-pg_hba.conf"'
    ) == 2
    assert "Exercise E4 additive secret lifecycle" in workflow
    assert (
        "sudo --non-interactive env" in workflow
        and f"sh {lifecycle_path}" in workflow
    )
    assert workflow.index("Exercise E4 additive secret lifecycle") < (
        workflow.index(
                "Run isolated PostgreSQL 17 "
                "E1/E2/E3/E4/E5a/E5b/E5c/E5d/E5e/E5f/E5g/E5h/E5i/E5j/E5k authority gate"
        )
    )


def test_operator_services_separate_additive_ceremony_from_inert_surface() -> None:
    compose = _read("stack/home-agent-compose.yml")
    service = _compose_service(compose, SERVICE)
    provisioner = _compose_service(
        compose, "provision-identity-cutover-roles"
    )

    assert "profiles: [operator]" in service
    assert "/run/secrets/database_url" in service
    assert "database_url_identity_cutover_identity_cutover" in service
    assert "identity semantic-authority cutover is dormant" in service
    assert "exit 78" in service
    assert "networks: [postgres-net]" in service
    assert "grant-runtime: {condition: service_completed_successfully}" in service
    assert "read_only: true" in service
    assert "no-new-privileges:true" in service
    assert "cap_drop: [ALL]" in service
    assert "driver: none" in service
    assert "api-net" not in service
    assert "edge-public" not in service
    assert not re.search(r"(?m)^\s+ports:", service)
    for forbidden in (
        "operator_token",
        "bootstrap_token",
        "service_token",
        "knowledge_encryption_key",
        "runtime_spool_key",
        "erasure_ledger_key",
        "postgres_owner_password",
        "postgres_identity_finalizer_password",
    ):
        assert forbidden not in service

    assert "profiles: [operator]" in provisioner
    assert "/deploy/provision-identity-cutover-roles.sh" in provisioner
    assert "postgres_owner_password_identity_cutover_provision" in provisioner
    assert (
        "postgres_identity_cutover_password_identity_cutover_provision"
        in provisioner
    )
    assert "networks: [postgres-net]" in provisioner
    assert "no-new-privileges:true" in provisioner
    assert "cap_drop: [ALL]" in provisioner
    assert "driver: none" in provisioner
    assert "api-net" not in provisioner
    assert not re.search(r"(?m)^\s+ports:", provisioner)


def test_grant_replay_quarantines_e4_and_pins_reviewed_catalog() -> None:
    source = _read("stack/home-agent-deploy/apply-grants.sh")
    quarantine = _grant_section("identity_cutover_e4_quarantine")
    admission = _grant_section("identity_cutover_e4_acl")

    assert source.index("$identity_cutover_e4_quarantine$;") < source.index(
        "DO $identity_cutover_e4_acl$"
    )
    assert "REVOKE ALL PRIVILEGES ON TABLE %I.%I" in quarantine
    assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)" in quarantine
    assert "REFERENCES (%1$s)" in quarantine
    assert (
        "operations.reviewed_identity_cutover_admissions" in quarantine
    )
    assert "operations.enforced_legacy_identity_writer_freezes" in quarantine
    assert "operations.semantic_authority_promotions" in quarantine
    assert (
        "operations.commit_reviewed_identity_cutover(bytea,uuid)" in quarantine
    )
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles" in quarantine
    assert "UNION ALL SELECT 'PUBLIC'" in quarantine
    assert "role_row.rolname IN (" in quarantine
    assert "'home_agent_identity_cutover'," in quarantine
    assert "'home_agent_identity_cutover_kernel'" in quarantine
    assert "FROM %4$I" in quarantine
    for cascade_template in (
        "REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %s CASCADE",
        "REVOKE ALL PRIVILEGES ON TABLE %s FROM %s CASCADE",
        "REFERENCES (%1$s) ON TABLE %2$s FROM %3$s CASCADE",
        "REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I CASCADE",
        "REFERENCES (%1$s) ON TABLE %2$I.%3$I FROM %4$I CASCADE",
        "engagement, privacy, operations, media FROM %I CASCADE",
        "REVOKE USAGE ON TYPE %I.%I FROM %I CASCADE",
        "REVOKE ALL PRIVILEGES ON TABLES FROM %I CASCADE",
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I CASCADE",
        "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %I CASCADE",
        "REVOKE ALL PRIVILEGES ON TYPES FROM %I CASCADE",
    ):
        assert cascade_template in quarantine

    assert f"'{PINNED_E4_CATALOG_SHA256}'" in admission
    assert "PENDING_E4_CATALOG_SHA256" not in admission
    assert "expected_e4_catalog_sha256 !~ '^[0-9a-f]{64}$'" in admission
    assert "actual_e4_catalog_sha256" in admission
    assert "Hash the post-quarantine catalog" in admission
    assert "'owner'," in admission
    assert "relation_row.relowner::regrole::text" in admission
    assert "'row_security'," in admission
    assert "relation_row.relrowsecurity" in admission
    assert "'force_row_security'," in admission
    assert "relation_row.relforcerowsecurity" in admission
    assert "'table_acl'," in admission
    assert "'column_acl'," in admission
    assert "'user_triggers'," in admission
    assert "NOT trigger_row.tgisinternal" in admission
    assert "pg_catalog.pg_get_triggerdef" in admission
    assert "'rules'," in admission
    assert "pg_catalog.pg_get_ruledef" in admission
    assert "'policies'," in admission
    assert "policy_row.polroles" in admission
    assert "policy_row.polqual" in admission
    assert "policy_row.polwithcheck" in admission
    assert "function_row.proacl" in admission
    assert "'role_acl_inventory'," in admission
    assert "'dependency_effective_access'," in admission
    assert "pg_catalog.has_schema_privilege" in admission
    assert "pg_catalog.has_table_privilege" in admission
    assert "pg_catalog.has_function_privilege" in admission
    for acl_catalog in (
        "pg_database",
        "pg_namespace",
        "pg_class",
        "pg_attribute",
        "pg_proc",
        "pg_type",
        "pg_default_acl",
        "pg_parameter_acl",
    ):
        assert f"pg_catalog.{acl_catalog}" in admission
    assert "0014_identity_cutover_e4" in admission
    reviewed_revisions = re.search(
        r"reviewed_e4_catalog_revisions constant text\[\] := ARRAY\["
        r"(.*?)\]\:\:text\[\]",
        admission,
        re.DOTALL,
    )
    assert reviewed_revisions is not None
    assert re.findall(
        r"'([0-9a-z_]+)'", reviewed_revisions.group(1)
        ) == [
            "0014_identity_cutover_e4",
            "0015_current_authority_e5a",
            "0016_principal_binding_e5b",
                "0017_authenticated_binding_e5c",
                    "0018_parent_relationship_e5d",
                        "0019_parent_stage_e5e",
                        "0020_parent_commit_e5f",
                        "0021_parent_status_e5h",
                    ]
    assert "identity_authority_e5_select" in admission
    assert "identity cutover E4 reviewed E5 policy mismatch" in admission
    assert "object_count = 0" in admission
    assert "object_count <> 4" in admission
    assert "current_revision = ANY (pre_e4_revisions)" in admission
    assert (
        "partial or revision-mismatched identity cutover E4 object set"
        in admission
    )
    assert (
        "identity cutover E4 catalog admission is pending reviewed digest"
        in admission
    )
    assert re.search(
        r"IF current_revision = '0014_identity_cutover_e4' THEN\s+"
        r"RAISE EXCEPTION "
        r"'identity cutover E4 activation contract is not installed'",
        admission,
    )
    assert "GRANT " not in admission
    assert "identity cutover E4 role ceremony was omitted" in admission


def test_historical_e1_catalog_is_not_rewritten_for_e4() -> None:
    migration = _read(
        "stack/services/home-agent-core/alembic/versions/"
        "0011_identity_erasure_schema_foundation.py"
    )

    assert LOGIN_ROLE not in migration
    assert KERNEL_ROLE not in migration
    assert "PENDING_E4_SYSTEM_CATALOG_SHA256" not in migration
    # E5c's dormant login changes only the reviewed database ACL fingerprint;
    # the E4 login and kernel remain absent from the historical migration.
    assert "5f9ee4e902a42d5880545f7d619a8fb9" in migration


def test_production_pin_and_rollout_mode_remain_inert() -> None:
    env = _read("stack/home-agent.env.example")
    docs = _read("stack/home-agent-deploy/IDENTITY-CUTOVER-ROLE.md")
    normalized_docs = " ".join(docs.split())

    assert (
        "HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration" in env
    )
    assert "HOME_AGENT_ROLLOUT_MODE=record_only" in env
    assert PINNED_E4_CATALOG_SHA256 in docs
    assert "PENDING_E4_CATALOG_SHA256" not in docs
    assert "E4_CATALOG_SHA256=" not in docs
    assert "raw PostgreSQL failure output" in normalized_docs
    assert "Do not prepare or run this role on the live host yet" in docs
    assert "adds no positive grant" in normalized_docs
    assert "PENDING_E4_SYSTEM_CATALOG_SHA256" not in docs

    deploy_names = "\n".join(
        path.name for path in (ROOT / "stack/home-agent-deploy").iterdir()
    )
    assert "activate-identity-cutover" not in deploy_names
    assert "enable-identity-cutover" not in deploy_names
