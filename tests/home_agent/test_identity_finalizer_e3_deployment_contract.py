from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
E3_CATALOG_SHA256 = (
    "123326a4620d3dd123773819d95255e40"
    "813a5a949f406570252ff1f7031f29a"
)


def _service(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        source,
    )
    assert match is not None
    return match.group(1)


def _e3_sections() -> tuple[str, str]:
    source = GRANTS.read_text(encoding="utf-8")
    quarantine = source.split("DO $identity_finalizer_e3_quarantine$", 1)[1].split(
        "$identity_finalizer_e3_quarantine$;", 1
    )[0]
    admission = source.split("DO $identity_finalizer_e3_acl$", 1)[1].split(
        "$identity_finalizer_e3_acl$;", 1
    )[0]
    return quarantine, admission


def test_e3_grant_replay_quarantines_before_conditional_restore() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    quarantine, admission = _e3_sections()

    assert source.index("$identity_finalizer_e3_quarantine$;") < source.index(
        "DO $identity_finalizer_e3_acl$"
    )
    assert "REVOKE ALL PRIVILEGES ON TABLE %I.%I" in quarantine
    assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)" in quarantine
    assert "REFERENCES (%1$s)" in quarantine
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles" in quarantine
    assert "UNION ALL SELECT 'PUBLIC'" in quarantine
    legacy_revoke = (
        "REVOKE ALL ON FUNCTION ingest.reject_artifact_link_cycle() FROM PUBLIC"
    )
    normalized_source = " ".join(source.split())
    assert legacy_revoke in normalized_source
    assert source.index("$identity_finalizer_e3_quarantine$;") < source.index(
        "REVOKE ALL ON FUNCTION ingest.reject_artifact_link_cycle()"
    ) < source.index("DO $identity_finalizer_e3_acl$")
    assert (
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "
        "public, ingest, identity, knowledge, engagement, privacy, "
        "operations, media FROM PUBLIC"
    ) not in " ".join(quarantine.split())

    assert "IF primary_object_count = 0 THEN" in admission
    assert "current_revision = ANY (pre_e3_revisions)" in admission
    assert "IF primary_object_count = 1" in admission
    assert "identity finalizer E3 inert bootstrap contract mismatch" in admission
    assert "external_table_acl_present=%s" in admission
    assert "rows_present=%s" in admission
    assert "bootstrap_columns IS DISTINCT FROM ARRAY[" in admission
    assert "bootstrap_constraints IS DISTINCT FROM ARRAY[" in admission
    assert "bootstrap_indexes IS DISTINCT FROM ARRAY[" in admission
    assert "constraint_row.contype::text" in admission
    assert (
        "uq_reviewed_identity_finalizer_admissions_finalization__1d9a|u"
        in admission
    )
    assert (
        "uq_reviewed_identity_finalizer_admissions_finalization__1d9a"
        "|false|true"
    ) in admission
    assert (
        "uq_reviewed_identity_finalizer_admissions_finalization_commitment"
        not in admission
    )
    assert "NOT table_row.relrowsecurity" in admission
    assert "NOT table_row.relforcerowsecurity" in admission
    assert "AND table_row.relhastriggers" in admission
    assert "bootstrap_trigger_count <> 2" in admission
    assert "bootstrap_insert_ri_trigger_count <> 1" in admission
    assert "bootstrap_update_ri_trigger_count <> 1" in admission
    assert 'pg_catalog."RI_FKey_check_ins"()' in admission
    assert 'pg_catalog."RI_FKey_check_upd"()' in admission
    assert (
        "FROM operations.reviewed_identity_finalizer_admissions" in admission
    )
    assert "pg_catalog.obj_description(" in admission
    assert "primary_object_count <> 5" in admission
    assert "partial identity finalizer E3 object set" in admission
    assert "0013_identity_finalizer_e3" in admission
    reviewed_revisions = re.search(
        r"reviewed_e3_catalog_revisions constant text\[\] := ARRAY\["
        r"(.*?)\]\:\:text\[\]",
        admission,
        re.DOTALL,
    )
    assert reviewed_revisions is not None
    assert re.findall(
        r"'([0-9a-z_]+)'",
        reviewed_revisions.group(1),
        ) == [
            "0013_identity_finalizer_e3",
            "0014_identity_cutover_e4",
            "0015_current_authority_e5a",
            "0016_principal_binding_e5b",
            "0017_authenticated_binding_e5c",
        ]
    assert (
        "current_revision = ANY (reviewed_e3_catalog_revisions)"
        in admission
    )
    overlay_revisions = re.search(
        r"reviewed_e4_overlay_revisions constant text\[\] := ARRAY\["
        r"(.*?)\]\:\:text\[\]",
        admission,
        re.DOTALL,
    )
    assert overlay_revisions is not None
    assert re.findall(
        r"'([0-9a-z_]+)'", overlay_revisions.group(1)
    ) == [
        "0014_identity_cutover_e4",
        "0015_current_authority_e5a",
        "0016_principal_binding_e5b",
        "0017_authenticated_binding_e5c",
    ]
    assert (
        "login_role.rolvaliduntil =\n"
        "               timestamptz '1970-01-01 00:00:00+00'"
    ) in admission
    assert "'log_parameter_max_length_on_error=0'" in admission
    assert "pg_catalog.pg_db_role_setting" in admission
    assert "AND database_setting.setdatabase <> 0" in admission
    assert "identity finalizer E3 ownership dependency mismatch" in admission
    assert "identity finalizer E3 write-fence contract mismatch" in admission
    assert "identity finalizer E3 control policy set mismatch" in admission
    descendant_policy_relations = re.search(
        r"e4_e3_policy_relations constant text\[\] := ARRAY\["
        r"(.*?)\]\:\:text\[\]",
        admission,
        re.DOTALL,
    )
    assert descendant_policy_relations is not None
    assert re.findall(
        r"'(operations\.[a-z0-9_]+)'",
        descendant_policy_relations.group(1),
    ) == [
        "operations.reviewed_identity_migration_runs",
        "operations.reviewed_identity_migration_finalizations",
        "operations.reviewed_identity_finalizer_admissions",
        "operations.semantic_authority_cutovers",
        "operations.legacy_identity_writer_evidence",
        "operations.privacy_cutover_check_receipts",
        "operations.reviewed_identity_migration_erasure_impacts",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
    ]
    assert (
        "identity finalizer E3 reviewed descendant policy mismatch"
        in admission
    )
    assert "pg_catalog.cardinality(e4_e3_policy_relations)" in admission
    assert (
        "pg_catalog.to_regclass(\n"
        "                    'operations.enforced_legacy_identity_writer_freezes'"
        in admission
    )
    assert (
        "'operations.enforced_legacy_identity_writer_freezes'::regclass"
        not in admission
    )
    assert "policy_row.polroles <>" in admission
    assert "ARRAY[cutover_kernel_oid]::oid[]" in admission
    assert "policy_row.polqual::text <>" in admission
    assert "reference_policy.polqual::text" in admission
    assert (
        "WHEN current_revision IN (\n"
        "                '0015_current_authority_e5a',\n"
        "                '0016_principal_binding_e5b',\n"
        "                '0017_authenticated_binding_e5c'\n"
        "              ) THEN 6"
        in admission
    )
    assert "WHEN current_revision = ANY (reviewed_e4_overlay_revisions) THEN 5" in (
        admission
    )
    assert "ELSE 4" in admission
    assert "identity finalizer E3 evidence policy set mismatch" in admission
    assert "identity finalizer E3 schema ACL mismatch" in admission
    assert "identity finalizer E3 table ACL mismatch" in admission
    assert "identity finalizer E3 column ACL mismatch" in admission
    assert "identity finalizer E3 function ACL mismatch" in admission
    assert "identity finalizer E3 catalog manifest mismatch" in admission
    assert E3_CATALOG_SHA256 in admission
    assert "PENDING_E3_CATALOG_SHA256" not in admission
    assert "identity finalizer E3 effective schema ACL mismatch" in admission
    assert "identity finalizer E3 effective table ACL mismatch" in admission
    assert "identity finalizer E3 effective function ACL mismatch" in admission
    assert "identity finalizer E3 sequence/type ACL mismatch" in admission
    assert "identity finalizer E3 default ACL mismatch" in admission
    assert "identity finalizer E3 PUBLIC ACL mismatch" in admission


def test_e3_grant_replay_restores_only_the_reviewed_database_surface() -> None:
    _quarantine, admission = _e3_sections()

    for expected in (
        "GRANT USAGE ON SCHEMA operations",
        "GRANT USAGE ON SCHEMA identity, privacy",
        "operations.reviewed_identity_finalizer_admissions",
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.reviewed_identity_migration_projection_subjects",
        "identity.people",
        "identity.aliases",
        "identity.external_recognition_bindings",
        "identity.privacy_directives",
        "identity.legacy_role_labels",
        "identity.legacy_relationship_candidates",
        "privacy.auto_expiry_schedules",
        "operations.outbox",
        "privacy.identity_person_is_blocked(uuid)",
        "privacy.lock_identity_semantic_write_fence()",
        "operations.finalize_reviewed_identity_migration(bytea,uuid)",
    ):
        assert expected in admission

    assert "GRANT EXECUTE ON FUNCTION\n    operations.finalize" in admission
    assert "TO home_agent_identity_finalizer;" in admission
    assert (
        "evidence_select_policy constant text := 'identity_finalizer_e3_select'"
        in admission
    )
    assert (
        "evidence_insert_policy constant text := 'identity_finalizer_e3_insert'"
        in admission
    )
    assert (
        "migration_run_lock_policy constant text :=\n"
        "    'identity_finalizer_e3_migration_run_lock'"
    ) in admission
    assert (
        "CREATE POLICY identity_finalizer_e3_migration_run_lock\n"
        "    ON operations.reviewed_identity_migration_runs\n"
        "    FOR UPDATE TO home_agent_identity_finalizer_kernel"
    ) in admission
    assert "WITH CHECK (false)" in admission
    assert ") <> 16" in admission
    assert "lock_policy.polqual::text = select_policy.polqual::text" in admission
    assert "pg_catalog.replace(target_table, '.', '_')" not in admission
    assert "|| '_e3_kernel_select'" not in admission
    assert "|| '_e3_kernel_insert'" not in admission
    assert (
        "'home_agent_identity_finalizer|operations."
        "finalize_reviewed_identity_migration(bytea,uuid)',"
    ) in admission
    assert (
        "'home_agent_identity_finalizer_kernel|operations."
        "finalize_reviewed_identity_migration(bytea,uuid)',"
    ) not in admission
    assert "GRANT SELECT, UPDATE (consumed_at)" not in admission
    assert "GRANT UPDATE (consumed_at)" in admission
    assert (
        "GRANT UPDATE (expires_at)\n"
        "    ON TABLE operations.reviewed_identity_migration_runs"
    ) in admission
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE identity.people" not in admission
    assert (
        "GRANT SELECT, INSERT ON TABLE identity.people" in admission
    )
    assert (
        "GRANT UPDATE (\n"
        "    status, status_source_ref, status_source_version,\n"
        "    status_source_sha256, updated_at\n"
        "  ) ON TABLE identity.people"
    ) in admission
    for update_column in (
        "identity.people|status|UPDATE",
        "identity.people|status_source_ref|UPDATE",
        "identity.people|status_source_version|UPDATE",
        "identity.people|status_source_sha256|UPDATE",
        "identity.people|updated_at|UPDATE",
        "operations.reviewed_identity_migration_runs|expires_at|UPDATE",
    ):
        assert update_column in admission
    for forbidden_write in (
        "identity.principals",
        "identity.ha_user_bindings",
        "identity.confirmation_artifacts",
        "knowledge.fact_versions",
        "knowledge.memory_transactions",
        "knowledge.places",
        "engagement.initiatives",
        "operations.rollout_authorizations",
    ):
        assert forbidden_write not in admission


def test_e3_replay_pins_exact_function_and_catalog_contracts() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    _quarantine, admission = _e3_sections()

    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner\n"
        "  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) in source
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE "
        "home_agent_identity_finalizer_kernel\n"
        "  GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
    ) in source
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE "
        "home_agent_identity_finalizer_kernel\n"
        "  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) not in source

    for expected in (
        "expected_e3_catalog_sha256 constant text",
        "expected_finalizer_body_sha256 constant text",
        "expected_fence_body_sha256 constant text",
        "expected_fence_trigger_body_sha256 constant text",
        "expected_person_blocked_body_sha256 constant text",
        "function_row.prolang = (",
        "function_row.proargtypes =",
        "function_row.proallargtypes IS NULL",
        "function_row.proargmodes IS NULL",
        "function_row.proargnames =",
        "function_row.pronargdefaults = 0",
        "function_row.proretset",
        "function_row.proleakproof",
        "function_row.proparallel = 'u'",
        "pg_catalog.convert_to(function_row.prosrc, 'UTF8')",
        "'36032e050f94dd376dfffd59a25b54f5d0afa197ac13922156be46df753889ea'",
    ):
        assert expected in admission

    evidence_tables = re.search(
        r"evidence_tables constant text\[\] := ARRAY\[(.*?)\]\:\:text\[\]",
        admission,
        re.DOTALL,
    )
    assert evidence_tables is not None
    assert len(re.findall(r"'operations\.[a-z0-9_]+'", evidence_tables.group(1))) == 11
    for expected in (
        "table_row.relowner = owner_oid",
        "table_row.relkind = 'r'",
        "table_row.relpersistence = 'p'",
        "table_row.relrowsecurity",
        "table_row.relforcerowsecurity",
        "'columns'",
        "'constraints'",
        "pg_catalog.pg_get_constraintdef(",
        "'indexes'",
        "pg_catalog.pg_get_indexdef(",
        "'policies'",
        "pg_catalog.pg_get_expr(",
        "'triggers'",
        "pg_catalog.pg_get_triggerdef(",
        "actual_e3_catalog_sha256 <> expected_e3_catalog_sha256",
        "policy_row.polname = e4_select_policy",
        "policy_row.polrelid IN (",
        "e4_e3_policy_relations",
        "policy_row.polroles =",
        "ARRAY[cutover_kernel_oid]::oid[]",
        "reference_policy.polqual::text =",
        "policy_row.polqual::text",
    ):
        assert expected in admission

    policy_manifest = admission.split("'policies', (", 1)[1].split(
        "'triggers', (", 1
    )[0]
    assert "reviewed_e4_overlay_revisions" in policy_manifest
    assert "policy_row.polname = e4_select_policy" in policy_manifest
    assert "policy_row.polname = e5_select_policy" in policy_manifest
    assert "policy_row.polpermissive" in policy_manifest
    assert "policy_row.polcmd = 'r'" in policy_manifest
    assert "policy_row.polwithcheck IS NULL" in policy_manifest
    assert "e4_e3_policy_relations" in policy_manifest
    assert "identity_cutover_e4_insert" not in policy_manifest
    assert "identity_cutover_e4_update" not in policy_manifest
    assert "LIKE" not in policy_manifest
    assert "identity finalizer E3 reviewed E5 policy mismatch" in admission

    e4_admission = source.split("DO $identity_cutover_e4_acl$", 1)[1].split(
        "$identity_cutover_e4_acl$;", 1
    )[0]
    assert "policy_row.polname IN (" in e4_admission
    for e4_policy in (
        "identity_cutover_e4_select",
        "identity_cutover_e4_insert",
        "identity_cutover_e4_update",
    ):
        assert f"'{e4_policy}'" in e4_admission
    for policy_field in (
        "policy_row.polroles",
        "policy_row.polqual",
        "policy_row.polwithcheck",
    ):
        assert policy_field in e4_admission

    assert "trigger_row.tgargs = ''::bytea" in admission
    assert "trigger_row.tgqual IS NULL" in admission
    assert "trigger_row.tgoldtable IS NULL" in admission
    assert "trigger_row.tgnewtable IS NULL" in admission


def test_e3_replay_validates_effective_and_direct_acl_surfaces() -> None:
    _quarantine, admission = _e3_sections()

    for expected in (
        "pg_catalog.has_schema_privilege(",
        "pg_catalog.has_table_privilege(",
        "pg_catalog.has_function_privilege(",
        "pg_catalog.has_sequence_privilege(",
        "pg_catalog.has_type_privilege(",
        "pg_catalog.pg_default_acl",
        "default_acl.defaclrole IN (finalizer_oid, kernel_oid)",
        "default_acl.defaclrole = owner_oid",
        "default_acl.defaclnamespace = 0",
        "default_acl.defaclobjtype = 'f'",
        "default_privilege.grantee = 0",
        "schema_acl.is_grantable",
        "table_acl.is_grantable",
        "column_acl.is_grantable",
        "function_acl.is_grantable",
        "control_table.oid IN (admission_table, fence_table)",
        "function_acl.grantee = 0",
    ):
        assert expected in admission
    assert "VALUES\n             (owner_oid),\n             (kernel_oid)" not in admission

    # Ownership preserves the ability to re-grant privileges, but ordinary
    # EXECUTE is revocable. Both direct and effective manifests must therefore
    # reject kernel execution of the externally callable finalizer.
    assert (
        "role_row.oid = kernel_oid\n"
        "        AND function_row.oid = finalizer_function"
    ) not in admission
    assert (
        "home_agent_identity_finalizer_kernel|"
        "operations.finalize_reviewed_identity_migration(bytea,uuid)"
    ) not in admission


def test_e3_compose_surface_is_still_an_expired_non_callable_tombstone() -> None:
    service = _service(COMPOSE.read_text(encoding="utf-8"), "identity-finalizer")

    assert "profiles: [operator]" in service
    assert 'restart: "no"' in service
    assert 'entrypoint: ["/bin/sh", "-c"]' in service
    assert "identity finalizer kernel is dormant and not activated" in service
    assert "exit 78" in service
    assert "/run/secrets/database_url" in service
    assert "grant-runtime: {condition: service_completed_successfully}" in service
    assert "api-net" not in service
    assert "edge-net" not in service
    assert not re.search(r"(?m)^\s+ports:", service)


def test_hosted_gate_reaches_0013_without_advancing_the_production_pin() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    env = (ROOT / "stack/home-agent.env.example").read_text(encoding="utf-8")

    assert 'REVISION_0013 = "0013_identity_finalizer_e3"' in runner
    assert "def _upgrade_e3_database(" in runner
    assert "def _run_e3_phase(" in runner
    assert "Running isolated dormant revision-0013 E3 contracts" in runner
    assert "TEST_PHASE3_IDENTITY_FINALIZER_E3_FINALIZER_DATABASE_URL" in runner
    assert (
        "HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration" in env
    )
