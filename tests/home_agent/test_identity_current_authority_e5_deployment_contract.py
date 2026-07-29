from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REVISION = "0015_current_authority_e5a"
DOWN_REVISION = "0014_identity_cutover_e4"
DESCENDANT_REVISION = "0016_principal_binding_e5b"
ACTIVATION_REVISION = "0017_authenticated_binding_e5c"
FOUNDATION_REVISION = "0018_parent_relationship_e5d"
STAGING_REVISION = "0019_parent_stage_e5e"
COMMIT_REVISION = "0020_parent_commit_e5f"
STATUS_REVISION = "0021_parent_status_e5h"
FUNCTION = "operations.evaluate_current_identity_semantic_authority(uuid)"
CALLER_ROLE = "home_agent_binding_operator"
KERNEL_ROLE = "home_agent_identity_authority_kernel"
PINNED_E3_CATALOG_SHA256 = (
    "123326a4620d3dd123773819d95255e40813a5a949f406570252ff1f7031f29a"
)
PINNED_E4_CATALOG_SHA256 = (
    "a96aeb68c7c5656988088ae74539760c6a811320849f01c122e02141f87eff27"
)
PINNED_E5_CATALOG_SHA256 = (
    "e90689e66cc5ca2131d08b597e21651a9f99e0d3f1ab1b1bfd20c399cf1bc3c7"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _grant_section(marker: str) -> str:
    source = _read("stack/home-agent-deploy/apply-grants.sh")
    return source.split(f"DO ${marker}$", 1)[1].split(f"${marker}$;", 1)[0]


def _revision_array(section: str, name: str) -> list[str]:
    match = re.search(
        rf"{re.escape(name)} constant text\[\] := ARRAY\[(.*?)\]"
        r"::text\[\]",
        section,
        re.DOTALL,
    )
    assert match is not None
    return re.findall(r"'([0-9a-z_]+)'", match.group(1))


def test_e5_role_is_additive_dormant_and_has_no_new_secret() -> None:
    historical = _read("stack/home-agent-deploy/provision-roles.sh")
    provisioner = _read(
        "stack/home-agent-deploy/provision-identity-cutover-roles.sh"
    )
    migration = _read(
        "stack/services/home-agent-core/alembic/versions/"
        "0015_identity_current_authority_e5.py"
    )

    assert f'revision: str = "{REVISION}"' in migration
    assert f'down_revision: str | None = "{DOWN_REVISION}"' in migration
    assert f'CALLER_ROLE = "{CALLER_ROLE}"' in migration
    assert f'KERNEL_ROLE = "{KERNEL_ROLE}"' in migration
    assert FUNCTION in migration

    assert "ALTER ROLE home_agent_binding_operator RESET ALL;" not in historical
    assert re.search(
        r"ALTER ROLE home_agent_binding_operator PASSWORD "
        r":'binding_operator_password'\s+NOSUPERUSER NOCREATEDB "
        r"NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;",
        historical,
    )
    operator_role_statement = historical.split(
        "ALTER ROLE home_agent_binding_operator PASSWORD", 1
    )[1].split("ALTER ROLE home_agent_binding_committer", 1)[0]
    assert "CONNECTION LIMIT 8" not in operator_role_statement
    assert (
        "ALTER ROLE home_agent_binding_operator SET "
        "statement_timeout = '15s';" in historical
    )
    assert (
        "ALTER ROLE home_agent_binding_operator SET lock_timeout"
        not in historical
    )
    assert (
        "ALTER ROLE home_agent_binding_operator SET\n"
        "  idle_in_transaction_session_timeout" not in historical
    )
    assert (
        "ALTER ROLE home_agent_binding_operator SET transaction_timeout"
        not in historical
    )

    assert "ALTER ROLE home_agent_binding_operator RESET ALL;" in provisioner
    assert re.search(
        r"ALTER ROLE home_agent_binding_operator\s+"
        r"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION\s+"
        r"NOINHERIT NOBYPASSRLS CONNECTION LIMIT 8;",
        provisioner,
    )
    for setting in (
        "statement_timeout = '15s'",
        "lock_timeout = '5s'",
        "idle_in_transaction_session_timeout = '15s'",
        "transaction_timeout = '30s'",
    ):
        assert re.search(
            r"ALTER ROLE home_agent_binding_operator SET\s+"
            + re.escape(setting)
            + r";",
            provisioner,
        )
    assert "caller_role.rolconnlimit = 8" in provisioner
    assert "caller_role.rolconfig = ARRAY[" in provisioner

    assert KERNEL_ROLE not in historical
    assert f"CREATE ROLE {KERNEL_ROLE} NOLOGIN" in provisioner
    assert "authority_kernel_count NOT IN (0, 1)" in provisioner
    assert "cutover_role_count NOT IN (0, 2)" in provisioner
    assert "PASSWORD :'identity_authority" not in provisioner
    assert "postgres_identity_authority" not in provisioner
    assert re.search(
        rf"ALTER ROLE {KERNEL_ROLE}\s+"
        r"NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION\s+"
        r"NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;",
        provisioner,
    )
    assert (
        f"GRANT {KERNEL_ROLE} TO home_agent_owner\n"
        "  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
    ) in provisioner
    assert f"GRANT {KERNEL_ROLE} TO {CALLER_ROLE}" not in provisioner
    assert "authority_kernel_role.rolconfig IS NULL" in provisioner
    assert "roleid = authority_kernel_oid" in provisioner
    assert "member = owner_oid" in provisioner
    assert "NOT admin_option" in provisioner
    assert "NOT inherit_option" in provisioner
    assert "set_option" in provisioner
    assert "refobjid IN (" in provisioner
    assert "authority_kernel_oid, caller_oid" in provisioner


def test_e5_role_provisioning_sql_heredocs_have_only_sql_comments() -> None:
    for relative_path in (
        "stack/home-agent-deploy/provision-roles.sh",
        "stack/home-agent-deploy/provision-identity-cutover-roles.sh",
    ):
        source = _read(relative_path)
        sql = source.split("<<'SQL'", 1)[1].rsplit("\nSQL", 1)[0]
        assert re.search(r"(?m)^-- .*historical", sql)
        assert re.search(r"(?m)^\s*#", sql) is None


def test_e5_overlay_preserves_exact_e3_e4_pins_and_stop_boundary() -> None:
    source = _read("stack/home-agent-deploy/apply-grants.sh")
    e3 = _grant_section("identity_finalizer_e3_acl")
    e4 = _grant_section("identity_cutover_e4_acl")
    e5 = _grant_section("identity_current_authority_e5_acl")

    assert source.index(
        "DO $identity_current_authority_e5_overlay_quarantine$"
    ) < source.index("DO $identity_finalizer_e3_quarantine$")
    assert source.index("DO $identity_finalizer_e3_acl$") < source.index(
        "DO $identity_cutover_e4_quarantine$"
    )
    assert source.index("DO $identity_cutover_e4_acl$") < source.index(
        "DO $identity_current_authority_e5_quarantine$"
    )
    assert source.index(
        "DO $identity_current_authority_e5_quarantine$"
    ) < source.index("DO $identity_current_authority_e5_acl$")
    assert (
        "GRANT USAGE ON SCHEMA identity TO home_agent_binding_operator;"
        in source
    )
    assert (
        "GRANT USAGE ON SCHEMA operations TO home_agent_binding_operator"
        not in source
    )

    assert f"'{PINNED_E3_CATALOG_SHA256}'" in e3
    assert f"'{PINNED_E4_CATALOG_SHA256}'" in e4
    assert "PENDING_E3_CATALOG_SHA256" not in e3
    assert "PENDING_E4_CATALOG_SHA256" not in e4
    assert _revision_array(e3, "reviewed_e3_catalog_revisions") == [
        "0013_identity_finalizer_e3",
        DOWN_REVISION,
        REVISION,
        DESCENDANT_REVISION,
        ACTIVATION_REVISION,
        FOUNDATION_REVISION,
        STAGING_REVISION,
        COMMIT_REVISION,
        STATUS_REVISION,
    ]
    assert _revision_array(e4, "reviewed_e4_catalog_revisions") == [
        DOWN_REVISION,
        REVISION,
        DESCENDANT_REVISION,
        ACTIVATION_REVISION,
        FOUNDATION_REVISION,
        STAGING_REVISION,
        COMMIT_REVISION,
        STATUS_REVISION,
    ]
    for section, marker in (
        (e3, "identity finalizer E3 reviewed E5 policy mismatch"),
        (e4, "identity cutover E4 reviewed E5 policy mismatch"),
    ):
        assert "identity_authority_e5_select" in section
        assert "policy_row.polroles" in section
        assert "authority_kernel_oid" in section
        assert "policy_row.polqual::text" in section
        assert marker in section

    assert re.search(
        rf"IF current_revision = '{DOWN_REVISION}' THEN\s+"
        r"RAISE EXCEPTION "
        r"'identity cutover E4 activation contract is not installed'",
        e4,
    )
    assert "current_revision IN (" in e4
    assert f"'{REVISION}'" in e4
    assert f"'{DESCENDANT_REVISION}'" in e4
    assert f"'{ACTIVATION_REVISION}'" in e4
    assert e5.count(
        "identity cutover E4 activation contract is not installed"
    ) == 1
    assert "GRANT " not in e5


def test_e5_run_lock_is_validated_before_e3_manifest_projection() -> None:
    e3 = _grant_section("identity_finalizer_e3_acl")
    validation = e3.split(
        "-- Revision 0015 adds a second, separately owned SELECT overlay", 1
    )[1].split(
        "identity finalizer E3 reviewed E5 policy mismatch", 1
    )[0]
    manifest = e3.split("'policies',", 1)[1].split("'triggers',", 1)[0]
    run_lock_projection = manifest.rsplit("OR (", 1)[1]
    relation_filter = manifest.index(
        "WHERE policy_row.polrelid = relation.oid"
    )
    not_start = manifest.index("AND NOT (")
    not_open = manifest.index("(", not_start)
    depth = 0
    not_close = None
    for offset, character in enumerate(manifest[not_open:], start=not_open):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                not_close = offset
                break

    assert (
        "e5_run_lock_policy constant text := "
        "'identity_authority_e5_run_lock'"
    ) in e3
    assert "pg_catalog.count(*)" in validation
    assert "WHERE policy_row.polname = e5_run_lock_policy" in validation
    assert (
        "'operations.reviewed_identity_migration_runs'::regclass"
        in validation
    )
    assert "lock_policy.polcmd = 'w'" in validation
    assert (
        "lock_policy.polroles =\n"
        "                  ARRAY[authority_kernel_oid]::oid[]"
    ) in validation
    assert "lock_policy.polqual IS NOT NULL" in validation
    assert "lock_policy.polwithcheck IS NOT NULL" in validation
    assert (
        "lock_policy.polqual::text = select_policy.polqual::text"
        in validation
    )
    assert (
        "lock_policy.polwithcheck::text =\n"
        "                  select_policy.polqual::text"
    ) in validation

    assert "policy_row.polname = e5_run_lock_policy" in manifest
    assert "current_revision IN (" in run_lock_projection
    assert f"'{REVISION}'" in run_lock_projection
    assert f"'{DESCENDANT_REVISION}'" in run_lock_projection
    assert re.search(
        r"'operations\.reviewed_identity_migration_runs'\s+::regclass",
        run_lock_projection,
    )
    assert "policy_row.polpermissive" in run_lock_projection
    assert "policy_row.polcmd = 'w'" in run_lock_projection
    assert (
        "policy_row.polroles =\n"
        "                           ARRAY[authority_kernel_oid]::oid[]"
    ) in run_lock_projection
    assert "policy_row.polqual IS NOT NULL" in run_lock_projection
    assert "policy_row.polwithcheck IS NOT NULL" in run_lock_projection
    assert re.search(
        r"policy_row\.polqual::text =\s+"
        r"policy_row\.polwithcheck::text",
        run_lock_projection,
    )
    assert (
        "reference_policy.polname = e5_select_policy"
        in run_lock_projection
    )
    assert relation_filter < not_start
    assert not_close is not None
    assert manifest.index(
        "policy_row.polname = e5_run_lock_policy"
    ) < not_close
    assert e3.index(
        "identity finalizer E3 reviewed E5 policy mismatch"
    ) < e3.index("WITH target_relations(target_name, relation_oid)")


def test_e5_replay_quarantines_and_pins_the_complete_direct_acl_surface() -> None:
    overlay = _grant_section(
        "identity_current_authority_e5_overlay_quarantine"
    )
    quarantine = _grant_section("identity_current_authority_e5_quarantine")
    admission = _grant_section("identity_current_authority_e5_acl")

    assert FUNCTION in overlay
    assert "home_agent_identity_authority_kernel" in overlay
    assert "REVOKE ALL PRIVILEGES ON TABLE %s" in overlay
    assert "REVOKE USAGE, CREATE ON SCHEMA operations, privacy" in overlay
    assert "'FROM home_agent_binding_operator CASCADE'" in overlay
    assert "authority_function IS NOT NULL" in overlay
    assert "identity_authority_e5_select" in overlay
    assert "identity_authority_e5_run_lock" in overlay

    assert "function_row.proname =" in quarantine
    assert "'evaluate_current_identity_semantic_authority'" in quarantine
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles" in quarantine
    assert "UNION ALL SELECT 'PUBLIC'" in quarantine
    for template in (
        "REVOKE ALL PRIVILEGES ON DATABASE %I",
        "REVOKE ALL PRIVILEGES ON TABLE %I.%I",
        "REFERENCES (%1$s) ON TABLE %2$I.%3$I",
        "REVOKE USAGE, CREATE ON SCHEMA",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA",
        "REVOKE USAGE ON TYPE %I.%I",
        "REVOKE ALL PRIVILEGES ON TABLES",
        "REVOKE ALL PRIVILEGES ON SEQUENCES",
        "REVOKE ALL PRIVILEGES ON FUNCTIONS",
        "REVOKE ALL PRIVILEGES ON TYPES",
    ):
        assert template in quarantine
    assert (
        "REVOKE USAGE, CREATE ON SCHEMA operations, privacy\n"
        "      FROM home_agent_binding_operator CASCADE;"
    ) in quarantine
    assert "e5_catalog_present boolean;" in quarantine
    assert "IF e5_catalog_present" in quarantine
    assert "identity_authority_e5_select" in quarantine
    assert "identity_authority_e5_run_lock" in quarantine

    assert _revision_array(admission, "reviewed_e5_catalog_revisions") == [
        REVISION,
        DESCENDANT_REVISION,
        ACTIVATION_REVISION,
        FOUNDATION_REVISION,
        STAGING_REVISION,
        COMMIT_REVISION,
        STATUS_REVISION,
    ]
    assert "NOT current_revision = ANY (reviewed_e5_catalog_revisions)" in (
        admission
    )
    assert "function_name_count <> 1" in admission
    assert "policy_count <> 13" in admission
    assert "kernel_owned_object_count <> 1" in admission
    assert "'pg_catalog.pg_proc'::regclass" in admission
    assert "identity_authority_e5_select" in admission
    assert "identity_authority_e5_run_lock" in admission
    assert "pg_catalog.cardinality(rls_relations)" in admission
    assert "policy_row.polcmd = 'r'" in admission
    assert "policy_row.polcmd = 'w'" in admission

    assert f"'{PINNED_E5_CATALOG_SHA256}'" in admission
    assert "PENDING_E5_CATALOG_SHA256" not in admission
    assert "expected_e5_catalog_sha256 !~ '^[0-9a-f]{64}$'" in admission
    assert "actual_e5_catalog_sha256" in admission
    assert (
        "identity current-authority E5 catalog admission digest mismatch"
    ) in admission
    for manifest_field in (
        "'function'",
        "'body_sha256'",
        "'policies'",
        "'caller_role'",
        "'kernel_role'",
        "'owner_membership'",
        "'role_acl_inventory'",
        "'caller_effective_access'",
        "'effective_execute'",
        "'owned_object_count'",
    ):
        assert manifest_field in admission

    # The E5 digest is computed only after an exact no-direct-ACL check. The
    # same catalog families are included in the digest, so a later pin cannot
    # silently admit one of the migration's broad temporary grants.
    for catalog, acl_column in (
        ("pg_database", "datacl"),
        ("pg_namespace", "nspacl"),
        ("pg_class", "relacl"),
        ("pg_attribute", "attacl"),
        ("pg_proc", "proacl"),
        ("pg_type", "typacl"),
        ("pg_default_acl", "defaclacl"),
        ("pg_parameter_acl", "paracl"),
    ):
        assert f"pg_catalog.{catalog}" in admission
        assert acl_column in admission
    assert admission.count("grantee = authority_kernel_oid") >= 14
    assert "current-authority E5 quarantine mismatch" in admission
    assert "expected_caller_role_config constant text[]" in admission
    for setting in (
        "'statement_timeout=15s'",
        "'lock_timeout=5s'",
        "'idle_in_transaction_session_timeout=15s'",
        "'transaction_timeout=30s'",
    ):
        assert setting in admission
    assert "caller_role.rolconnlimit = 8" in admission
    assert "caller_role.rolconfig = expected_caller_role_config" in admission
    assert "current-authority E5 caller role contract mismatch" in admission
    caller_manifest = admission.split("'caller_role',", 1)[1].split(
        "'kernel_role',", 1
    )[0]
    assert "caller_role.rolconnlimit" in caller_manifest
    assert "caller_role.rolvaliduntil" in caller_manifest
    assert "caller_role.rolconfig" in caller_manifest
    assert (
        "caller_oid, 'operations', 'USAGE,CREATE'" in admission
    )
    assert "caller_oid, 'privacy', 'USAGE,CREATE'" in admission
    caller_access_manifest = admission.split(
        "'caller_effective_access',", 1
    )[1].split("'effective_execute',", 1)[0]
    assert caller_access_manifest.count(
        "'home_agent_binding_operator'"
    ) == 5
    assert "'operations'" in caller_access_manifest
    assert "'privacy'" in caller_access_manifest
    assert "GRANT " not in admission


def test_hosted_runner_provisions_before_0015_and_enforces_pinned_catalog() -> None:
    runner = _read("tools/run-home-agent-e1-postgres-gate.py")
    workflow = _read(".github/workflows/home-agent-e1-postgres.yml")
    phase = runner.split("def _run_e4_scaffold_phase(", 1)[1].split(
        "\ndef _build_test_image(", 1
    )[0]

    provision = phase.index("_provision_identity_cutover_roles")
    e4_upgrade = phase.index("REVISION_0014", provision)
    e5_upgrade = phase.index("REVISION_0015", e4_upgrade)
    assert provision < e4_upgrade < e5_upgrade
    assert "test_phase3_identity_current_authority_e5_schema.py" in phase
    assert "test_phase3_identity_current_authority_e5_runtime_postgres.py" in (
        phase
    )
    assert "test_identity_current_authority_e5_deployment_contract.py" in phase
    assert (
        "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_OWNER_DATABASE_URL"
        in runner
    )
    assert "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_DATABASE_URL" in runner
    assert phase.count("REVISION_0015") >= 4
    assert "_alembic_downgrade(" in phase
    assert phase.count(
        "identity cutover E4 activation contract is not installed"
    ) == 2
    assert "identity principal-binding E5b catalog admission is pending " not in (
        phase
    )
    assert "pinned dormant E5b catalog" in phase
    assert "unpinned dormant E5b catalog" not in phase
    assert "capture_e5_catalog_digest" not in runner
    assert "PENDING_E5_CATALOG_SHA256" not in runner
    assert "pending_e5_catalog_digest" not in runner
    assert "_extract_e5_catalog_digest" not in runner
    assert "_classify_e5_catalog_failure" not in runner
    assert "E5_CATALOG_FAILURE_CODES" not in runner
    assert "E5_CATALOG_SHA256=" not in runner
    assert "pending_e5b_catalog_digest" not in runner
    assert "_extract_e5b_catalog_digest" not in runner
    assert "_classify_e5b_catalog_failure" not in runner
    assert "E5B_CATALOG_FAILURE_CODES" not in runner
    assert "capture_e5b_catalog_digest" not in runner
    assert "E5B_CATALOG_SHA256=" not in runner
    assert runner.index("if cleanup_failure is not None:") < runner.index(
        '"E1/E2/E3/E4 PostgreSQL 17 gate passed; "'
    )
    assert "verify rejected E5 catalog remains broadly quarantined" in phase
    assert "binding_operator AS caller" in phase
    assert "namespace_row.nspname IN ('operations','privacy')" in phase
    for catalog in (
        "pg_database",
        "pg_namespace",
        "pg_class",
        "pg_attribute",
        "pg_proc",
        "pg_type",
        "pg_default_acl",
        "pg_parameter_acl",
    ):
        assert f"pg_catalog.{catalog}" in phase

    assert (
            "home agent E1/E2/E3/E4/E5a/E5b/E5c/E5d/E5e/E5f/E5g/E5h/E5i/E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t "
            "PostgreSQL gate"
        in workflow
    )
    assert (
            "Run isolated PostgreSQL 17 "
            "E1/E2/E3/E4/E5a/E5b/E5c/E5d/E5e/E5f/E5g/E5h/E5i/E5j/E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t authority gate"
            in workflow
    )
    assert (
        workflow.count(
            '- "tests/home_agent/'
            'test_identity_current_authority_e5_deployment_contract.py"'
        )
        == 2
    )
    production = _read("stack/home-agent.env.example")
    assert (
        "HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration"
        in production
    )
    assert "HOME_AGENT_ROLLOUT_MODE=record_only" in production
