from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"
ROLE_DOC = ROOT / "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md"


def _normalized(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized.replace("( ", "(").replace(" )", ")")


def _grant_statements(source: str) -> list[str]:
    return [
        _normalized(statement)
        for statement in source.split(";")
        if re.search(r"\bGRANT\b", statement, flags=re.IGNORECASE)
    ]


def test_restore_login_has_an_exact_replay_allowlist() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    normalized = _normalized(source)
    grants = [
        statement
        for statement in _grant_statements(source)
        if "home_agent_erasure" in statement
    ]

    assert "DO $erasure_runtime_acl_reset$" in source
    assert "pg_catalog.pg_attribute" in source
    assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)," in source
    assert "REFERENCES (%1$s)" in source
    assert "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA" in source
    assert "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA" in source
    assert "REVOKE ALL PRIVILEGES ON TABLES FROM home_agent_erasure" in normalized
    assert "REVOKE ALL PRIVILEGES ON SEQUENCES FROM home_agent_erasure" in normalized
    assert "REVOKE ALL PRIVILEGES ON FUNCTIONS FROM home_agent_erasure" in normalized

    expected = (
        "GRANT SELECT ON TABLE ingest.artifact_links TO home_agent_erasure",
        "GRANT SELECT (person_id) ON TABLE identity.people TO home_agent_erasure",
        "GRANT SELECT (principal_id) ON TABLE identity.principals "
        "TO home_agent_erasure",
        "GRANT SELECT ON TABLE knowledge.memory_transactions, "
        "knowledge.fact_versions TO home_agent_erasure",
        "GRANT UPDATE (object, system_range, resolution) ON TABLE "
        "knowledge.fact_versions TO home_agent_erasure",
        "GRANT SELECT ON TABLE engagement.initiatives TO home_agent_erasure",
        "GRANT UPDATE (state, suppression_reason) ON TABLE "
        "engagement.initiatives TO home_agent_erasure",
        "GRANT INSERT (block_id, artifact_id, erasure_request_id) ON TABLE "
        "privacy.retrieval_blocks TO home_agent_erasure",
        "GRANT SELECT ON TABLE privacy.artifact_registry, "
        "privacy.auto_expiry_schedules TO home_agent_erasure",
        "GRANT UPDATE (state, completed_at) ON TABLE "
        "privacy.auto_expiry_schedules TO home_agent_erasure",
        "GRANT SELECT ON TABLE privacy.auto_expiry_receipts TO home_agent_erasure",
        "GRANT SELECT ON TABLE operations.erasure_replay_receipts "
        "TO home_agent_erasure",
        "GRANT SELECT ON TABLE operations.erasure_ledger_state "
        "TO home_agent_erasure",
        "GRANT SELECT ON TABLE operations.outbox TO home_agent_erasure",
        "GRANT UPDATE (state, claim_token, claimed_at, completed_at, "
        "last_error_code) ON TABLE operations.outbox TO home_agent_erasure",
    )
    for statement in expected:
        assert statement in normalized
    assert re.search(
        r"GRANT SELECT \(\s*erasure_request_id, principal_id, scope, state, "
        r"policy_digest, created_at,\s*completed_at\s*\) ON TABLE "
        r"privacy\.erasure_requests TO home_agent_erasure",
        source,
    )

    assert all("ON ALL TABLES" not in statement for statement in grants)
    assert all(" DELETE " not in f" {statement} " for statement in grants)
    for forbidden_table in (
        "identity.principal_binding_requests",
        "identity.principal_binding_proposals",
        "identity.aliases",
        "knowledge.places",
        "engagement.preferences",
        "privacy.erasure_tasks",
    ):
        assert not any(
            forbidden_table in statement and "TO home_agent_erasure" in statement
            for statement in grants
        )


def test_e2_tombstones_residuals_and_helpers_are_contract_bound() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    role_doc = ROLE_DOC.read_text(encoding="utf-8")
    quarantine = source.index("DO $identity_erasure_kernel_quarantine$")
    e2_start = source.index("DO $identity_erasure_e2_acl$")
    e2 = source[e2_start : source.index("\nSQL", e2_start)]

    assert quarantine < e2_start
    assert "privacy.subject_retrieval_blocks" in e2
    assert "privacy.identity_person_erasure_residuals" in e2
    assert "GRANT SELECT, INSERT ON TABLE %s TO home_agent_owner" in e2
    assert (
        "GRANT SELECT (principal_id, person_id) ON TABLE identity.principals "
        "TO home_agent_identity_erasure_kernel"
    ) in _normalized(e2)
    assert (
        "GRANT SELECT (person_id) ON TABLE "
        "privacy.subject_retrieval_blocks TO home_agent_identity_erasure_kernel"
    ) in _normalized(e2)
    assert "knowledge.fact_versions" not in e2
    assert "knowledge.places" not in e2
    assert "engagement.initiatives" not in e2
    assert not any(
        "identity_person_erasure_residuals" in statement
        and "home_agent_identity_erasure_kernel" in statement
        for statement in _grant_statements(e2)
    )
    assert "role_row.rolname <> 'home_agent_owner'" in e2
    assert "UNION ALL SELECT 'PUBLIC'" in e2
    assert "REVOKE ALL PRIVILEGES ON TABLE %s FROM %s" in e2
    for signature in (
        "identity_person_is_blocked(uuid)",
        "identity_principal_is_blocked(uuid)",
        "identity_fact_is_blocked(text,uuid,text,jsonb,uuid)",
        "reject_tombstoned_identity_write()",
        "enforce_identity_person_erasure_residual_set()",
        "replay_identity_person_retrieval_block_v2(jsonb)",
    ):
        assert signature in e2
    assert "partial identity erasure E2 object set" in e2
    assert "identity erasure E2 function ownership invalid" in e2
    assert "function_row.prosecdef IS DISTINCT FROM" in e2
    assert "replay_row.proowner <> owner_oid" in e2
    function_reset = e2[e2.index("FOREACH target_signature IN ARRAY all_functions") :]
    assert "SELECT role_row.rolname FROM pg_catalog.pg_roles AS role_row" in (
        function_reset
    )
    assert "rolname <> 'home_agent_identity_erasure_kernel'" not in function_reset
    assert (
        "home_agent_api, home_agent_binding_operator, home_agent_ingest, "
        "home_agent_worker, home_agent_erasure, home_agent_rollout, "
        "home_agent_backup"
    ) in _normalized(e2)
    assert "GRANT EXECUTE ON FUNCTION %s TO home_agent_erasure" in e2
    assert not any(
        " ON TABLE " in statement and re.search(r"\bhome_agent_erasure\b", statement)
        for statement in _grant_statements(e2)
    )

    assert "no direct tombstone or residual DML" in _normalized(role_doc)
    assert "exact restore-replay allowlist" in _normalized(role_doc)
