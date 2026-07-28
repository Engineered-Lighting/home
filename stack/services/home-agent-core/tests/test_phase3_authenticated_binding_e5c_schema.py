from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "alembic/versions/0017_authenticated_binding_e5c.py"
).read_text(encoding="utf-8")


def test_e5c_revision_is_an_additive_denial_only_boundary() -> None:
    assert 'revision: str = "0017_authenticated_binding_e5c"' in MIGRATION
    assert 'down_revision: str | None = "0016_principal_binding_e5b"' in MIGRATION
    assert "adds no semantic data and grants no runtime capability" in MIGRATION
    assert "Production remains pinned to 0006a" in MIGRATION
    for forbidden in (
        "CREATE TABLE",
        "CREATE POLICY",
        "INSERT INTO",
        "UPDATE identity.",
        "DELETE FROM",
        "GRANT EXECUTE",
    ):
        assert forbidden not in MIGRATION


def test_e5c_migration_quarantines_only_the_outer_caller_surface() -> None:
    assert 'CALLER_ROLE = "home_agent_binding_committer"' in MIGRATION
    assert 'KERNEL_ROLE = "home_agent_identity_binding_kernel"' in MIGRATION
    assert "SET LOCAL ROLE {KERNEL_ROLE}" in MIGRATION
    assert "GRANT USAGE ON SCHEMA identity TO {KERNEL_ROLE}" in MIGRATION
    assert "REVOKE ALL ON FUNCTION {FUNCTION} FROM {CALLER_ROLE} CASCADE" in (
        MIGRATION
    )
    assert "REVOKE USAGE ON SCHEMA identity FROM {KERNEL_ROLE}" in MIGRATION
    assert "REVOKE USAGE, CREATE ON SCHEMA identity" in MIGRATION
    assert "authenticated_binding_e5c_quarantine_failed" in MIGRATION
    assert "pg_has_role(" in MIGRATION
    assert "'SET'" in MIGRATION
    assert "WHERE roleid = caller_oid OR member = caller_oid" in MIGRATION
    assert "function_acl.grantee IN (0, caller_oid)" in MIGRATION
    assert "function_acl.privilege_type = 'EXECUTE'" in MIGRATION
    assert "0, caller_oid, kernel_oid" in MIGRATION
    assert "namespace_acl.privilege_type IN ('USAGE', 'CREATE')" in MIGRATION


def test_e5c_upgrade_and_downgrade_are_both_fail_closed() -> None:
    tree = ast.parse(MIGRATION)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    assert set(functions) == {"_quarantine", "upgrade", "downgrade"}
    assert (
        '_quarantine(expected_revision="0016_principal_binding_e5b")'
        in MIGRATION
    )
    assert (
        '_quarantine(expected_revision="0017_authenticated_binding_e5c")'
        in MIGRATION
    )
