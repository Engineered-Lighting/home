from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GRANTS = "stack/home-agent-deploy/apply-grants.sh"
MIGRATION = (
    "stack/services/home-agent-core/alembic/versions/"
    "0016_principal_binding_kernel_e5b.py"
)
REVISION = "0016_principal_binding_e5b"
FUNCTION_NAME = "identity.commit_authenticated_principal_binding_e5b("
FUNCTION_ARGUMENTS = (
    "uuid,character varying,character varying,uuid,uuid,uuid,uuid,uuid)"
)
RECEIPT = "operations.principal_binding_authority_receipts"
PINNED_E3 = (
    "123326a4620d3dd123773819d95255e40813a5a949f406570252ff1f7031f29a"
)
PINNED_E4 = (
    "a96aeb68c7c5656988088ae74539760c6a811320849f01c122e02141f87eff27"
)
PINNED_E5A = (
    "adfd664ccb07372107649fab7275d1d3040ebe498813fc69af27a4e7336cd084"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _block(source: str, marker: str) -> str:
    return source.split(f"DO ${marker}$", 1)[1].split(
        f"${marker}$;", 1
    )[0]


def _migration_function_body_sha256() -> str:
    tree = ast.parse(_read(MIGRATION))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "FUNCTION_BODY"
            for target in statement.targets
        ):
            body = ast.literal_eval(statement.value)
            assert isinstance(body, str)
            return hashlib.sha256(body.encode("utf-8")).hexdigest()
    raise AssertionError("FUNCTION_BODY assignment not found")


def _revision_array(section: str, name: str) -> list[str]:
    match = re.search(
        rf"{re.escape(name)} constant text\[\] := ARRAY\[(.*?)\]"
        r"::text\[\]",
        section,
        re.DOTALL,
    )
    assert match is not None
    return re.findall(r"'([0-9a-z_]+)'", match.group(1))


def test_e5b_receipt_never_enters_generic_operations_grants() -> None:
    source = _read(GRANTS)
    api = _block(source, "identity_api_operations_grants")
    worker = _block(source, "identity_worker_operations_grants")
    early = _block(
        source, "identity_principal_binding_e5b_receipt_early_quarantine"
    )

    assert "ALL TABLES IN SCHEMA knowledge,\n  engagement, privacy, operations" not in (
        source
    )
    assert "ALL TABLES IN SCHEMA operations TO home_agent_worker" not in source
    for section, role in (
        (api, "home_agent_api"),
        (worker, "home_agent_worker"),
    ):
        assert RECEIPT in section
        assert "relation_row.oid IS DISTINCT FROM" in section
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s" in section
        assert role in section

    assert RECEIPT in early
    assert "role_row.rolname <> 'home_agent_owner'" in early
    assert "UNION ALL SELECT 'PUBLIC'" in early
    assert "REVOKE ALL PRIVILEGES ON TABLE %s FROM %s CASCADE" in early
    assert "REFERENCES (%1$s)" in early
    assert source.index(
        "DO $identity_principal_binding_e5b_receipt_early_quarantine$"
    ) < source.index("DO $identity_erasure_e2_acl$")
    assert source.index(
        "DO $identity_principal_binding_e5b_receipt_early_quarantine$"
    ) < source.index("DO $identity_finalizer_e3_acl$")


def test_e5b_descendant_overlays_preserve_predecessor_catalog_pins() -> None:
    source = _read(GRANTS)
    e3 = _block(source, "identity_finalizer_e3_acl")
    e4 = _block(source, "identity_cutover_e4_acl")
    e5a = _block(source, "identity_current_authority_e5_acl")

    assert "0016_principal_binding_kernel_e5b" not in source
    assert _revision_array(e3, "reviewed_e3_catalog_revisions")[-1] == REVISION
    assert _revision_array(e4, "reviewed_e4_catalog_revisions")[-1] == REVISION
    assert _revision_array(e5a, "reviewed_e5_catalog_revisions")[-1] == REVISION
    assert f"'{PINNED_E3}'" in e3
    assert f"'{PINNED_E4}'" in e4
    assert f"'{PINNED_E5A}'" in e5a

    for expected in (
        "identity_projection_lineage_e5b_exact_person",
        "principal_binding_e5b_select",
        "identity finalizer E3 reviewed E5b overlay mismatch",
    ):
        assert expected in e3
    assert e3.count("identity_projection_lineage_e5b_exact_person") >= 3
    assert "index_state.indislive" in e3
    for late_bound_relation in (
        "operations.reviewed_identity_migration_projection_lineage",
        "operations.enforced_legacy_identity_writer_freezes",
    ):
        assert re.search(
            r"pg_catalog\.to_regclass\(\s*'operations\.'\s*'"
            + late_bound_relation.removeprefix("operations.")
            + r"'\s*\)",
            e3,
        )

    for expected in (
        "semantic_authority_promotions_e5b_exact_authority",
        "principal_binding_e5b_select",
        "identity cutover E4 reviewed E5b overlay mismatch",
    ):
        assert expected in e4
    assert e4.count("semantic_authority_promotions_e5b_exact_authority") >= 2
    assert "index_state.indislive" in e4

    for policy in (
        "principal_binding_e5b_select",
        "principal_binding_e5b_insert",
        "principal_binding_e5b_update",
        "principal_binding_e5b_suppression",
        "principal_binding_authority_receipts_owner_select",
        "principal_binding_authority_receipts_e5b_select",
        "principal_binding_authority_receipts_e5b_insert",
    ):
        assert policy in e5a
    assert "policy_row.polqual::text" in e5a
    assert "reference_policy.polqual::text" in e5a
    assert "current-authority E5 reviewed E5b policy overlay mismatch" in e5a


def test_e5b_quarantine_is_before_admission_and_has_no_positive_grants() -> None:
    source = _read(GRANTS)
    overlay = _block(
        source, "identity_principal_binding_e5b_overlay_quarantine"
    )
    quarantine = _block(source, "identity_principal_binding_e5b_quarantine")
    admission = _block(source, "identity_principal_binding_e5b_acl")

    assert source.index(
        "DO $identity_principal_binding_e5b_overlay_quarantine$"
    ) < source.index("DO $identity_finalizer_e3_acl$")
    assert source.index("DO $identity_current_authority_e5_acl$") < source.index(
        "DO $identity_principal_binding_e5b_quarantine$"
    )
    assert source.index(
        "DO $identity_principal_binding_e5b_quarantine$"
    ) < source.index("DO $identity_principal_binding_e5b_acl$")

    for section in (overlay, quarantine):
        assert FUNCTION_NAME in section
        assert FUNCTION_ARGUMENTS in section
        assert RECEIPT in section
        assert "role_row.rolname <> 'home_agent_owner'" in section
        assert "UNION ALL SELECT 'PUBLIC'" in section
        assert "REFERENCES (%1$s)" in section
        assert "home_agent_identity_binding_kernel" in section
        assert "GRANT " not in section

    for expected in (
        "REVOKE ALL PRIVILEGES ON DATABASE %I",
        "REVOKE ALL PRIVILEGES ON TABLE %I.%I",
        "REVOKE USAGE, CREATE ON SCHEMA",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA",
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA",
        "REVOKE USAGE ON TYPE %I.%I",
        "REVOKE ALL PRIVILEGES ON TABLES",
        "REVOKE ALL PRIVILEGES ON SEQUENCES",
        "REVOKE ALL PRIVILEGES ON FUNCTIONS",
        "REVOKE ALL PRIVILEGES ON TYPES",
    ):
        assert expected in quarantine
    assert "GRANT " not in admission


def test_e5b_pending_manifest_pins_complete_denial_only_catalog() -> None:
    admission = _block(
        _read(GRANTS), "identity_principal_binding_e5b_acl"
    )

    assert _revision_array(admission, "pre_e5b_revisions") == [
        "0001_greenfield_core",
        "0002_people_privacy_cutover",
        "0003_resource_budgets",
        "0004_rollout_authorizations",
        "0005_principal_binding_proposals",
        "0006_worker_maintenance_health",
        "0006a_worker_lease_arbitration",
        "0007_phase3_identity_authority",
        "0008_identity_migration_kernel",
        "0009_identity_finalizer_base",
        "0010_identity_erasure_source",
        "0011_identity_erasure_e1",
        "0012_identity_erasure_e2",
        "0013_identity_finalizer_e3",
        "0014_identity_cutover_e4",
        "0015_current_authority_e5a",
    ]
    for absent_condition in (
        "receipt_table IS NULL",
        "binding_function IS NULL",
        "function_name_count = 0",
        "binding_column_count = 0",
        "policy_count = 0",
        "support_constraint_count = 0",
        "trigger_count = 0",
        "current_revision = ANY (pre_e5b_revisions)",
    ):
        assert absent_condition in admission
    assert re.search(
        r"current_revision = ANY \(pre_e5b_revisions\) THEN\s+RETURN;",
        admission,
    )

    assert f"current_revision <> '{REVISION}'" in admission
    assert FUNCTION_NAME in admission
    assert FUNCTION_ARGUMENTS in admission
    assert RECEIPT in admission
    assert "function_name_count <> 1" in admission
    assert "binding_column_count <> 1" in admission
    assert "policy_count <> 26" in admission
    assert "support_constraint_count <> 7" in admission
    assert "trigger_count <> 3" in admission
    assert "kernel_owned_object_count <> 1" in admission
    assert f"'{_migration_function_body_sha256()}'" in admission
    assert "index_state.indpred IS NULL" in admission
    assert "index_state.indpred IS NOT NULL" not in admission

    for object_name in (
        "principal_binding_proposals_e5b_exact_authority",
        "confirmation_artifacts_e5b_exact_authority",
        "semantic_authority_promotions_e5b_exact_authority",
        "identity_projection_lineage_e5b_exact_person",
        "principal_binding_receipts_e5b_binding_graph",
        "uq_ha_user_bindings_authority_receipt_id",
        "fk_ha_user_bindings_e5b_authority_receipt",
        "uq_confirmation_artifacts_binding_nonce_e5b",
        "people_principal_binding_e5b_fence",
        "privacy_directives_principal_binding_e5b_fence",
        "edge_privacy_user_blocks_principal_binding_e5b_fence",
    ):
        assert object_name in admission

    for field in (
        "'function'",
        "'body_sha256'",
        "'receipt_relation'",
        "'constraints'",
        "'indexes'",
        "'policies'",
        "'fence_triggers'",
        "'kernel_role'",
        "'owner_membership'",
        "'binding_column'",
        "'dependencies'",
        "'kernel_acl_inventory'",
        "'effective_access'",
        "'owned_object_count'",
    ):
        assert field in admission

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

    assert "'PENDING_E5B_CATALOG_SHA256'" in admission
    assert (
        "identity principal-binding E5b catalog admission is pending "
        "reviewed digest" in admission
    )
    assert "'expected=%s actual=%s'" in admission
    assert (
        "identity principal-binding E5b catalog admission digest mismatch"
        in admission
    )
    assert re.search(
        r"RAISE EXCEPTION "
        r"'identity cutover E4 activation contract is not installed'\s+"
        r"USING ERRCODE = '55000';\s+END\s*$",
        admission,
    )
