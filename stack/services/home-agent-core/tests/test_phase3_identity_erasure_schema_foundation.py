from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.ids import uuid7


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0011_identity_erasure_schema_foundation.py"
DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_OWNER_DATABASE_URL"
LIFECYCLE_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_LIFECYCLE_DATABASE_URL"
REQUIRED_GATE_ENV = "REQUIRE_PHASE3_IDENTITY_ERASURE_E1_TESTS"
KERNEL_ROLE = "home_agent_identity_erasure_kernel"

if os.getenv(REQUIRED_GATE_ENV) == "1":
    missing_gate_urls = [
        name
        for name in (DATABASE_ENV, LIFECYCLE_DATABASE_ENV)
        if not os.getenv(name)
    ]
    if missing_gate_urls:
        raise RuntimeError(
            "required E1 PostgreSQL gate URLs are missing: "
            + ", ".join(missing_gate_urls)
        )


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _signature(label: str) -> str:
    return hashlib.sha512(label.encode("utf-8")).hexdigest()


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(error.value.orig, "sqlstate", None)


def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME_AGENT_DATABASE_URL"] = database_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_0011_is_dormant_content_free_and_exactly_source_linked() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0011_identity_erasure_e1"' in source
    assert 'down_revision: str | None = "0010_identity_erasure_source"' in source
    for table in (
        "privacy.person_erasure_scopes",
        "privacy.subject_retrieval_blocks",
        "operations.reviewed_identity_migration_erasure_receipts",
    ):
        assert table in source

    assert "identity_person_erasure" in source
    assert "fk_person_erasure_scope_request_authority" in source
    assert "fk_person_erasure_scope_principal_person" in source
    assert "fk_person_erasure_scope_confirmation" in source
    assert 'REQUEST_SCOPE_COLUMN = "identity_person_scope_commitment"' in source
    assert "identity_erasure_request_authority" in source
    assert "identity_erasure_principal_kind_authority" in source
    assert "identity_erasure_ha_binding_authority" in source
    assert "consumed_at, expires_at" in source
    assert "scope_commitment," in source
    assert "authority_kind = 'ha_user'" in source
    assert "confirmation_consumed_at >= binding_confirmed_at" in source
    assert "fk_subject_retrieval_block_request_operation" in source
    assert "fk_subject_retrieval_block_request_person" in source
    assert "fk_subject_retrieval_block_expiry_operation" not in source
    assert "fk_subject_retrieval_block_expiry_person" not in source
    assert "Auto-expiry is intentionally not an" in source
    assert "fk_identity_erasure_receipt_exact_impact" in source
    assert "fk_identity_erasure_receipt_retrieval_block" in source
    assert "identity_erasure_receipt_operation_run" in source
    assert "completion_txid" in source
    assert "removed_leaf_commitment_count BETWEEN 0 AND 30000" in source
    assert "migration_run_erased" in source
    assert "result_code = 'live_erased'" not in source

    for prohibited in (
        "display_name",
        "alias_text",
        "external_identifier",
        "source_payload",
        "before_json",
        "after_json",
        "utterance",
        "coordinate",
    ):
        assert prohibited not in source

    assert "CREATE FUNCTION" not in source
    assert "CREATE PROCEDURE" not in source
    assert "apply_identity_erasure" not in source
    assert "DELETE FROM" not in source
    assert "UPDATE identity." not in source


def test_0011_admission_acl_and_downgrade_fail_closed() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert "identity_erasure_kernel_role_invalid" in source
    assert "identity_erasure_e1_name_occupied" in source
    assert "identity_erasure_e1_constraint_occupied" in source
    assert "identity_erasure_e1_column_occupied" in source
    assert "identity_erasure_e1_predecessor_invalid" in source
    assert "identity_erasure_e1_support_boundary_invalid" in source
    assert "identity_erasure_e1_preexisting_operation" in source
    assert "identity_erasure_e1_predecessor_shape_invalid" in source
    assert "e1_expected_erasure_operation" in source
    assert "e1_expected_erasure_impact" in source
    assert "pg_catalog.pg_get_constraintdef" in source
    assert "actual_policies IS DISTINCT FROM expected_policies" in source
    assert "actual_acl IS DISTINCT FROM expected_acl" in source
    assert "identity_erasure_e1_replay_guard_invalid" in source
    assert "627bb84f83baa6183144de5d94ddcc4f0" in source
    assert "relowner = owner_oid" in source
    assert "relrowsecurity" in source
    assert "relforcerowsecurity" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS" in source
    assert "REVOKE USAGE ON TYPE %I.%I" in source
    assert "REVOKE USAGE, CREATE ON SCHEMA" in source
    assert "'privacy','operations','media'" in source
    assert "identity_erasure_e1_contract_invalid" in source
    assert "identity_erasure_e1_downgrade_blocked" in source
    assert "USING ERRCODE = '2BP01'" in source
    assert "graph_trigger.tgtype = 29" in source
    assert "graph_function.proacl IS NOT NULL" in source
    assert "trigger_function.proacl IS NOT NULL" in source
    assert "identity_person_scope_commitment ~ '^[0-9a-f]{64}$' AND" in source
    assert "scope =" in source
    assert "identity_person" in source
    assert "identity_person_scope_commitment IS NOT NULL" in source
    assert "confirmation_consumed_at <= recorded_at" in source
    assert "REVOKE SELECT (%1$s), INSERT (%1$s), UPDATE (%1$s)" in source


@pytest.mark.skipif(
    not os.getenv(DATABASE_ENV),
    reason=(
        "TEST_PHASE3_IDENTITY_ERASURE_E1_OWNER_DATABASE_URL is required; "
        "it must point to a disposable PostgreSQL 17 database at revision 0011"
    ),
)
@pytest.mark.asyncio
async def test_postgresql_e1_owner_rls_and_exact_manual_scope_boundary() -> None:
    engine = create_async_engine(
        os.environ[DATABASE_ENV],
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                assert (
                    await connection.execute(text("SELECT session_user"))
                ).scalar_one() == "home_agent_owner"

                rows = (
                    await connection.execute(
                        text(
                            "SELECT n.nspname || '.' || c.relname AS target, "
                            "c.relrowsecurity, c.relforcerowsecurity, "
                            "pg_get_userbyid(c.relowner) AS owner "
                            "FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace "
                            "WHERE n.nspname || '.' || c.relname IN ("
                            "'privacy.person_erasure_scopes',"
                            "'privacy.subject_retrieval_blocks',"
                            "'operations."
                            "reviewed_identity_migration_erasure_receipts') "
                            "ORDER BY target"
                        )
                    )
                ).mappings().all()
                assert len(rows) == 3
                assert all(row["relrowsecurity"] for row in rows)
                assert all(row["relforcerowsecurity"] for row in rows)
                assert all(row["owner"] == "home_agent_owner" for row in rows)

                role = (
                    await connection.execute(
                        text(
                            "SELECT rolcanlogin, rolinherit, rolsuper, "
                            "rolcreatedb, rolcreaterole, rolreplication, "
                            "rolbypassrls, rolconnlimit "
                            "FROM pg_roles WHERE rolname = :role"
                        ),
                        {"role": KERNEL_ROLE},
                    )
                ).mappings().one()
                assert role == {
                    "rolcanlogin": False,
                    "rolinherit": False,
                    "rolsuper": False,
                    "rolcreatedb": False,
                    "rolcreaterole": False,
                    "rolreplication": False,
                    "rolbypassrls": False,
                    "rolconnlimit": 0,
                }

                for table in (
                    "privacy.person_erasure_scopes",
                    "privacy.subject_retrieval_blocks",
                    "operations.reviewed_identity_migration_erasure_receipts",
                ):
                    assert (
                        await connection.execute(
                            text(
                                "SELECT has_table_privilege(:role, :table, "
                                "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,"
                                "REFERENCES,TRIGGER')"
                            ),
                            {"role": KERNEL_ROLE, "table": table},
                        )
                    ).scalar_one() is False

                direct_acl_count = (
                    await connection.execute(
                        text(
                            "SELECT ("
                            "SELECT count(*) FROM pg_database d "
                            "CROSS JOIN LATERAL aclexplode(d.datacl) acl "
                            "JOIN pg_roles r ON r.oid = acl.grantee "
                            "WHERE d.datname = current_database() "
                            "AND r.rolname = :role) + ("
                            "SELECT count(*) FROM pg_namespace n "
                            "CROSS JOIN LATERAL aclexplode(n.nspacl) acl "
                            "JOIN pg_roles r ON r.oid = acl.grantee "
                            "WHERE n.nspname IN ("
                            "'public','ingest','identity','knowledge',"
                            "'engagement','privacy','operations','media') "
                            "AND r.rolname = :role) + ("
                            "SELECT count(*) FROM pg_proc p "
                            "JOIN pg_namespace n ON n.oid = p.pronamespace "
                            "CROSS JOIN LATERAL aclexplode(p.proacl) acl "
                            "JOIN pg_roles r ON r.oid = acl.grantee "
                            "WHERE n.nspname IN ("
                            "'public','ingest','identity','knowledge',"
                            "'engagement','privacy','operations','media') "
                            "AND r.rolname = :role) + ("
                            "SELECT count(*) FROM pg_type t "
                            "JOIN pg_namespace n ON n.oid = t.typnamespace "
                            "CROSS JOIN LATERAL aclexplode(t.typacl) acl "
                            "JOIN pg_roles r ON r.oid = acl.grantee "
                            "WHERE n.nspname IN ("
                            "'public','ingest','identity','knowledge',"
                            "'engagement','privacy','operations','media') "
                            "AND r.rolname = :role)"
                        ),
                        {"role": KERNEL_ROLE},
                    )
                ).scalar_one()
                assert direct_acl_count == 0

                assert (
                    await connection.execute(
                        text(
                            "SELECT has_column_privilege("
                            "'home_agent_api', 'privacy.erasure_requests', "
                            "'identity_person_scope_commitment', 'SELECT') "
                            "OR has_column_privilege("
                            "'home_agent_api', 'privacy.erasure_requests', "
                            "'identity_person_scope_commitment', 'INSERT') "
                            "OR has_column_privilege("
                            "'home_agent_api', 'privacy.erasure_requests', "
                            "'identity_person_scope_commitment', 'UPDATE')"
                        )
                    )
                ).scalar_one() is False

                confirmation_insert_columns = (
                    await connection.execute(
                        text(
                            "SELECT coalesce(array_agg(a.attname::text "
                            "ORDER BY a.attnum) FILTER (WHERE "
                            "has_column_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', a.attname, "
                            "'INSERT')), ARRAY[]::text[]) "
                            "FROM pg_attribute a WHERE a.attrelid = "
                            "'identity.confirmation_artifacts'::regclass "
                            "AND a.attnum > 0 AND NOT a.attisdropped"
                        )
                    )
                ).scalar_one()
                assert confirmation_insert_columns == [
                    "artifact_id",
                    "principal_id",
                    "purpose",
                    "proposal_digest",
                    "client_nonce_sha256",
                    "issued_at",
                    "expires_at",
                    "consumed_at",
                ]
                assert (
                    await connection.execute(
                        text(
                            "SELECT has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', 'INSERT') OR "
                            "has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', "
                            "'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR "
                            "has_any_column_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', "
                            "'UPDATE,REFERENCES')"
                        )
                    )
                ).scalar_one() is False
                for runtime_role in (
                    "home_agent_binding_operator",
                    "home_agent_ingest",
                    "home_agent_worker",
                    "home_agent_erasure",
                    "home_agent_rollout",
                    "home_agent_backup",
                ):
                    assert (
                        await connection.execute(
                            text(
                                "SELECT has_table_privilege(:role, "
                                "'identity.confirmation_artifacts', "
                                "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,"
                                "TRIGGER') OR has_any_column_privilege(:role, "
                                "'identity.confirmation_artifacts', "
                                "'INSERT,UPDATE,REFERENCES')"
                            ),
                            {"role": runtime_role},
                        )
                    ).scalar_one() is False
                for runtime_role in (
                    "home_agent_api",
                    "home_agent_binding_operator",
                    "home_agent_ingest",
                    "home_agent_worker",
                    "home_agent_erasure",
                    "home_agent_rollout",
                    "home_agent_backup",
                ):
                    assert (
                        await connection.execute(
                            text(
                                "SELECT has_column_privilege(:role, "
                                "'identity.ha_user_bindings', "
                                "'identity_erasure_binding_valid_until', "
                                "'SELECT,INSERT,UPDATE,REFERENCES')"
                            ),
                            {"role": runtime_role},
                        )
                    ).scalar_one() is False

                person_id = uuid7()
                principal_id = uuid7()
                second_person_id = uuid7()
                second_principal_id = uuid7()
                service_principal_id = uuid7()
                voice_principal_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO identity.people ("
                        "person_id, display_name, status, privacy_scope) VALUES "
                        "(:person_id, 'E1 primary fixture', 'active', 'private'),"
                        "(:second_person_id, 'E1 second fixture', "
                        "'active', 'private')"
                    ),
                    {
                        "person_id": person_id,
                        "second_person_id": second_person_id,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO identity.principals ("
                        "principal_id, person_id, kind, display_label, status) "
                        "VALUES "
                        "(:principal_id, :person_id, 'ha_user', "
                        "'E1 primary principal', 'active'),"
                        "(:second_principal_id, :second_person_id, 'ha_user', "
                        "'E1 second principal', 'active'),"
                        "(:service_principal_id, :second_person_id, 'service', "
                        "'E1 service principal', 'active'),"
                        "(:voice_principal_id, :second_person_id, "
                        "'voice_unknown', 'E1 voice principal', 'active')"
                    ),
                    {
                        "principal_id": principal_id,
                        "person_id": person_id,
                        "second_principal_id": second_principal_id,
                        "second_person_id": second_person_id,
                        "service_principal_id": service_principal_id,
                        "voice_principal_id": voice_principal_id,
                    },
                )
                await connection.execute(
                    text("SELECT set_config('app.principal_id', :value, true)"),
                    {"value": str(principal_id)},
                )

                binding_request_id = uuid7()
                binding_proposal_id = uuid7()
                binding_artifact_id = uuid7()
                binding_id = uuid7()
                binding_proposal_digest = _digest("e1-binding-proposal")
                await connection.execute(
                    text(
                        "INSERT INTO identity.principal_binding_requests ("
                        "request_id, ha_user_id, review_code, state, "
                        "requested_at, expires_at, staged_at, closed_at) "
                        "VALUES (:request_id, 'e1-ha-user', "
                        "'ABCDEFGHJKLMNPQR', 'consumed', "
                        "transaction_timestamp() - interval '1 minute', "
                        "transaction_timestamp() + interval '10 minutes', "
                        "transaction_timestamp(), transaction_timestamp())"
                    ),
                    {"request_id": binding_request_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO identity.confirmation_artifacts ("
                        "artifact_id, principal_id, purpose, proposal_digest, "
                        "client_nonce_sha256, issued_at, expires_at, consumed_at"
                        ") VALUES (:artifact_id, :principal_id, "
                        "'ha_user_person_binding.confirm', :proposal_digest, "
                        ":nonce, transaction_timestamp() - interval '1 minute', "
                        "transaction_timestamp() + interval '10 minutes', "
                        "transaction_timestamp())"
                    ),
                    {
                        "artifact_id": binding_artifact_id,
                        "principal_id": principal_id,
                        "proposal_digest": binding_proposal_digest,
                        "nonce": _digest("e1-binding-nonce"),
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO identity.principal_binding_proposals ("
                        "proposal_id, operator_request_id, request_id, "
                        "ha_user_id, person_id, reviewed_display_label, "
                        "person_snapshot_digest, proposal_digest, state, "
                        "stage_receipt_digest, staged_at, expires_at, "
                        "consumed_at, result_principal_id, "
                        "confirmation_artifact_id) VALUES ("
                        ":proposal_id, :operator_request_id, :request_id, "
                        "'e1-ha-user', :person_id, 'E1 principal', "
                        ":snapshot_digest, :proposal_digest, 'consumed', "
                        ":receipt_digest, transaction_timestamp(), "
                        "transaction_timestamp() + interval '10 minutes', "
                        "transaction_timestamp(), :principal_id, :artifact_id)"
                    ),
                    {
                        "proposal_id": binding_proposal_id,
                        "operator_request_id": uuid7(),
                        "request_id": binding_request_id,
                        "person_id": person_id,
                        "snapshot_digest": _digest("e1-person-snapshot"),
                        "proposal_digest": binding_proposal_digest,
                        "receipt_digest": _digest("e1-stage-receipt"),
                        "principal_id": principal_id,
                        "artifact_id": binding_artifact_id,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO identity.ha_user_bindings ("
                        "binding_id, proposal_id, ha_user_id, principal_id, "
                        "person_id, confirmed_by_principal_id, confirmed_at, "
                        "source_artifact_id) VALUES ("
                        ":binding_id, :proposal_id, 'e1-ha-user', :principal_id, "
                        ":person_id, :principal_id, transaction_timestamp(), "
                        ":artifact_id)"
                    ),
                    {
                        "binding_id": binding_id,
                        "proposal_id": binding_proposal_id,
                        "principal_id": principal_id,
                        "person_id": person_id,
                        "artifact_id": binding_artifact_id,
                    },
                )
                await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                await connection.execute(text("SET CONSTRAINTS ALL DEFERRED"))

                artifact_id = uuid7()
                descriptor_artifact_id = uuid7()
                mismatch_artifact_id = uuid7()
                wrong_person_artifact_id = uuid7()
                time_artifact_id = uuid7()
                service_artifact_id = uuid7()
                voice_artifact_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO identity.confirmation_artifacts ("
                        "artifact_id, principal_id, purpose, proposal_digest, "
                        "client_nonce_sha256, issued_at, expires_at, consumed_at"
                        ") VALUES (:artifact_id, :artifact_principal_id, "
                        "'identity_person_erasure', :proposal_digest, :nonce, "
                        "transaction_timestamp(), "
                        "transaction_timestamp() + interval '5 minutes', "
                        "transaction_timestamp())"
                    ),
                    [
                        {
                            "artifact_id": target_id,
                            "artifact_principal_id": target_principal,
                            "proposal_digest": _digest(label),
                            "nonce": _digest(f"{label}-nonce"),
                        }
                        for target_id, target_principal, label in (
                            (artifact_id, principal_id, "e1-scope"),
                            (
                                descriptor_artifact_id,
                                principal_id,
                                "descriptor-attempt",
                            ),
                            (
                                mismatch_artifact_id,
                                principal_id,
                                "e1-mismatch-proposal",
                            ),
                            (
                                wrong_person_artifact_id,
                                principal_id,
                                "wrong-person",
                            ),
                            (time_artifact_id, principal_id, "time-mismatch"),
                            (service_artifact_id, service_principal_id, "service"),
                            (voice_artifact_id, voice_principal_id, "voice"),
                        )
                    ],
                )

                request_id = uuid7()
                descriptor_request_id = uuid7()
                digest_request_id = uuid7()
                wrong_person_request_id = uuid7()
                time_request_id = uuid7()
                service_request_id = uuid7()
                voice_request_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO privacy.erasure_requests ("
                        "erasure_request_id, principal_id, scope, state, "
                        "policy_digest, identity_person_scope_commitment) VALUES "
                        "(:request_id, :principal_id, "
                        "'{\"kind\":\"identity_person\"}'::jsonb, "
                        "'preview', :policy_digest, :scope_commitment),"
                        "(:descriptor_request_id, :principal_id, "
                        "'{\"kind\":\"descriptor\"}'::jsonb, "
                        "'preview', :policy_digest, NULL)"
                    ),
                    {
                        "request_id": request_id,
                        "descriptor_request_id": descriptor_request_id,
                        "principal_id": principal_id,
                        "policy_digest": _digest("e1-policy"),
                        "scope_commitment": _digest("e1-scope"),
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO privacy.erasure_requests ("
                        "erasure_request_id, principal_id, scope, state, "
                        "policy_digest, identity_person_scope_commitment) "
                        "VALUES (:request_id, :request_principal_id, "
                        "'{\"kind\":\"identity_person\"}'::jsonb, "
                        "'preview', :policy_digest, :scope_commitment)"
                    ),
                    [
                        {
                            "request_id": target_id,
                            "request_principal_id": target_principal,
                            "policy_digest": _digest("e1-policy"),
                            "scope_commitment": _digest(label),
                        }
                        for target_id, target_principal, label in (
                            (
                                digest_request_id,
                                principal_id,
                                "different-scope-digest",
                            ),
                            (
                                wrong_person_request_id,
                                principal_id,
                                "wrong-person",
                            ),
                            (time_request_id, principal_id, "time-mismatch"),
                            (service_request_id, service_principal_id, "service"),
                            (voice_request_id, voice_principal_id, "voice"),
                        )
                    ],
                )

                with pytest.raises(DBAPIError) as descriptor_commitment:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO privacy.erasure_requests ("
                                "erasure_request_id, principal_id, scope, state, "
                                "policy_digest, identity_person_scope_commitment) "
                                "VALUES (:request_id, :principal_id, "
                                "'{\"kind\":\"descriptor\"}'::jsonb, "
                                "'preview', :policy_digest, :scope_commitment)"
                            ),
                            {
                                "request_id": uuid7(),
                                "principal_id": principal_id,
                                "policy_digest": _digest("e1-policy"),
                                "scope_commitment": _digest(
                                    "descriptor-hidden-commitment"
                                ),
                            },
                        )
                assert _sqlstate(descriptor_commitment) == "23514"

                scope_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO privacy.person_erasure_scopes ("
                        "scope_id, erasure_request_id, person_id, "
                        "authorizing_principal_id, authority_kind, "
                        "ha_binding_id, binding_confirmed_at, "
                        "binding_valid_until, confirmation_artifact_id, "
                        "confirmation_purpose, confirmation_consumed_at, "
                        "confirmation_expires_at, scope_kind, scope_commitment) "
                        "VALUES (:scope_id, :request_id, :person_id, "
                        ":principal_id, 'ha_user', :binding_id, "
                        "transaction_timestamp(), 'infinity'::timestamptz, "
                        ":artifact_id, 'identity_person_erasure', "
                        "transaction_timestamp(), "
                        "transaction_timestamp() + interval '5 minutes', "
                        "'identity_person', "
                        ":scope_commitment)"
                    ),
                    {
                        "scope_id": scope_id,
                        "request_id": request_id,
                        "person_id": person_id,
                        "principal_id": principal_id,
                        "artifact_id": artifact_id,
                        "binding_id": binding_id,
                        "scope_commitment": _digest("e1-scope"),
                    },
                )

                with pytest.raises(DBAPIError) as backdated_revocation:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "UPDATE identity.ha_user_bindings "
                                "SET revoked_at = transaction_timestamp() "
                                "- interval '1 second' "
                                "WHERE binding_id = :binding_id"
                            ),
                            {"binding_id": binding_id},
                        )
                assert _sqlstate(backdated_revocation) == "23514"
                await connection.execute(
                    text(
                        "UPDATE identity.ha_user_bindings "
                        "SET revoked_at = transaction_timestamp() "
                        "+ interval '1 minute' WHERE binding_id = :binding_id"
                    ),
                    {"binding_id": binding_id},
                )
                cascaded_valid_until = (
                    await connection.execute(
                        text(
                            "SELECT binding_valid_until < "
                            "'infinity'::timestamptz "
                            "FROM privacy.person_erasure_scopes "
                            "WHERE scope_id = :scope_id"
                        ),
                        {"scope_id": scope_id},
                    )
                ).scalar_one()
                assert cascaded_valid_until is True
                await connection.execute(
                    text(
                        "UPDATE identity.ha_user_bindings SET revoked_at = NULL "
                        "WHERE binding_id = :binding_id"
                    ),
                    {"binding_id": binding_id},
                )

                assert (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM privacy.person_erasure_scopes "
                            "WHERE erasure_request_id = :descriptor_request_id"
                        ),
                        {"descriptor_request_id": descriptor_request_id},
                    )
                ).scalar_one() == 0

                binding_snapshot = (
                    await connection.execute(
                        text(
                            "SELECT confirmed_at, "
                            "identity_erasure_binding_valid_until::text "
                            "AS valid_until "
                            "FROM identity.ha_user_bindings "
                            "WHERE binding_id = :binding_id"
                        ),
                        {"binding_id": binding_id},
                    )
                ).mappings().one()
                artifact_snapshots = {
                    row["artifact_id"]: row
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT artifact_id, consumed_at, expires_at "
                                "FROM identity.confirmation_artifacts "
                                "WHERE artifact_id = ANY(:artifact_ids)"
                            ),
                            {
                                "artifact_ids": [
                                    descriptor_artifact_id,
                                    mismatch_artifact_id,
                                    wrong_person_artifact_id,
                                    time_artifact_id,
                                    service_artifact_id,
                                    voice_artifact_id,
                                ]
                            },
                        )
                    ).mappings()
                }

                async def insert_scope_candidate(
                    *,
                    target_request_id,
                    target_person_id,
                    target_principal_id,
                    target_artifact_id,
                    commitment_label: str,
                    authority_kind: str = "ha_user",
                    consumed_override=None,
                ) -> None:
                    artifact_snapshot = artifact_snapshots[target_artifact_id]
                    await connection.execute(
                        text(
                            "INSERT INTO privacy.person_erasure_scopes ("
                            "scope_id, erasure_request_id, person_id, "
                            "authorizing_principal_id, authority_kind, "
                            "ha_binding_id, binding_confirmed_at, "
                            "binding_valid_until, confirmation_artifact_id, "
                            "confirmation_purpose, confirmation_consumed_at, "
                            "confirmation_expires_at, scope_kind, "
                            "scope_commitment, recorded_at) VALUES ("
                            ":scope_id, :request_id, :person_id, :principal_id, "
                            ":authority_kind, :binding_id, :binding_confirmed_at, "
                            ":binding_valid_until, :artifact_id, "
                            "'identity_person_erasure', :confirmation_consumed_at, "
                            ":confirmation_expires_at, 'identity_person', "
                            ":scope_commitment, :confirmation_consumed_at)"
                        ),
                        {
                            "scope_id": uuid7(),
                            "request_id": target_request_id,
                            "person_id": target_person_id,
                            "principal_id": target_principal_id,
                            "authority_kind": authority_kind,
                            "binding_id": binding_id,
                            "binding_confirmed_at": binding_snapshot[
                                "confirmed_at"
                            ],
                            "binding_valid_until": binding_snapshot["valid_until"],
                            "artifact_id": target_artifact_id,
                            "confirmation_consumed_at": consumed_override
                            or artifact_snapshot["consumed_at"],
                            "confirmation_expires_at": artifact_snapshot[
                                "expires_at"
                            ],
                            "scope_commitment": _digest(commitment_label),
                        },
                    )

                negative_cases = (
                    (
                        "descriptor",
                        descriptor_request_id,
                        person_id,
                        principal_id,
                        descriptor_artifact_id,
                        "descriptor-attempt",
                        "ha_user",
                        None,
                    ),
                    (
                        "digest",
                        digest_request_id,
                        person_id,
                        principal_id,
                        mismatch_artifact_id,
                        "different-scope-digest",
                        "ha_user",
                        None,
                    ),
                    (
                        "person",
                        wrong_person_request_id,
                        second_person_id,
                        principal_id,
                        wrong_person_artifact_id,
                        "wrong-person",
                        "ha_user",
                        None,
                    ),
                    (
                        "time",
                        time_request_id,
                        person_id,
                        principal_id,
                        time_artifact_id,
                        "time-mismatch",
                        "ha_user",
                        artifact_snapshots[time_artifact_id]["consumed_at"]
                        - timedelta(seconds=1),
                    ),
                    (
                        "service",
                        service_request_id,
                        second_person_id,
                        service_principal_id,
                        service_artifact_id,
                        "service",
                        "ha_user",
                        None,
                    ),
                    (
                        "voice",
                        voice_request_id,
                        second_person_id,
                        voice_principal_id,
                        voice_artifact_id,
                        "voice",
                        "ha_user",
                        None,
                    ),
                )
                for (
                    _case,
                    candidate_request,
                    candidate_person,
                    candidate_principal,
                    candidate_artifact,
                    candidate_commitment,
                    candidate_kind,
                    candidate_consumed,
                ) in negative_cases:
                    with pytest.raises(DBAPIError) as rejected:
                        async with connection.begin_nested():
                            await insert_scope_candidate(
                                target_request_id=candidate_request,
                                target_person_id=candidate_person,
                                target_principal_id=candidate_principal,
                                target_artifact_id=candidate_artifact,
                                commitment_label=candidate_commitment,
                                authority_kind=candidate_kind,
                                consumed_override=candidate_consumed,
                            )
                    assert _sqlstate(rejected) in {"23503", "23514"}

                operation_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_operations ("
                        "operation_id, source_kind, erasure_request_id, "
                        "operation_commitment) VALUES ("
                        ":operation_id, 'erasure_request', :request_id, "
                        ":commitment)"
                    ),
                    {
                        "operation_id": operation_id,
                        "request_id": request_id,
                        "commitment": _digest("e1-operation"),
                    },
                )

                shadow_authorization_id = (
                    await connection.execute(
                        text(
                            "SELECT authorization_id FROM "
                            "operations.rollout_authorizations "
                            "ORDER BY authorized_at LIMIT 1"
                        )
                    )
                ).scalar_one_or_none()
                if shadow_authorization_id is None:
                    shadow_authorization_id = uuid7()
                    await connection.execute(
                        text(
                            "INSERT INTO operations.rollout_authorizations ("
                            "authorization_id, operator_request_id, from_mode, "
                            "to_mode, rule_version, policy_version, policy_digest, "
                            "input_digest, worker_instance_id, "
                            "worker_success_sequence, worker_kernel_version, "
                            "worker_maintenance_succeeded_at, worker_proof_digest, "
                            "readiness_evaluated_at) VALUES ("
                            ":authorization_id, :operator_request_id, "
                            "'record_only', 'shadow', "
                            "'record-only-envelope-worker-gate-v3', "
                            "'home-agent-mvp-v1', :policy_digest, :input_digest, "
                            ":worker_instance_id, 1, "
                            "'worker-maintenance-cycle-v1', "
                            "transaction_timestamp(), :worker_proof_digest, "
                            "transaction_timestamp())"
                        ),
                        {
                            "authorization_id": shadow_authorization_id,
                            "operator_request_id": uuid7(),
                            "policy_digest": _digest("e1-rollout-policy"),
                            "input_digest": _digest("e1-rollout-input"),
                            "worker_instance_id": uuid7(),
                            "worker_proof_digest": _digest("e1-worker-proof"),
                        },
                    )

                run_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO operations.reviewed_identity_migration_runs ("
                        "run_id, operator_request_id, contract_version, "
                        "source_schema_version, "
                        "source_projection_contract_version, importer_version, "
                        "canonicalization_version, projection_version, "
                        "shadow_rule_version, commitment_algorithm, "
                        "commitment_key_fingerprint, commitment_key_epoch, "
                        "source_item_count, decision_count, "
                        "logical_source_manifest_commitment, "
                        "projection_manifest_commitment, "
                        "source_projection_contract_digest, "
                        "review_receipt_commitment, policy_version, policy_digest, "
                        "shadow_authorization_id, release_manifest_digest, "
                        "migration_tool_bundle_digest, core_oci_manifest_digest, "
                        "core_schema_digest, core_capability_digest, "
                        "signature_algorithm, signing_key_fingerprint, "
                        "review_signature, expires_at) VALUES ("
                        ":run_id, :operator_request_id, "
                        "'reviewed-identity-migration-run-v1', 1, "
                        "'legacy-identity-source-projection-v1', "
                        "'legacy-identity-importer-v1', "
                        "'identity-canonicalization-v1', "
                        "'semantic-people-projection-v1', "
                        "'record-only-envelope-worker-gate-v3', "
                        "'hmac-sha256-v1', :commitment_key_fingerprint, 1, 1, 1, "
                        ":logical_source_manifest_commitment, "
                        ":projection_manifest_commitment, "
                        ":source_projection_contract_digest, "
                        ":review_receipt_commitment, 'home-agent-mvp-v1', "
                        ":policy_digest, :shadow_authorization_id, "
                        ":release_manifest_digest, :migration_tool_bundle_digest, "
                        ":core_oci_manifest_digest, :core_schema_digest, "
                        ":core_capability_digest, 'ed25519', "
                        ":signing_key_fingerprint, :review_signature, "
                        "transaction_timestamp() + interval '5 minutes')"
                    ),
                    {
                        "run_id": run_id,
                        "operator_request_id": uuid7(),
                        "commitment_key_fingerprint": _digest("e1-run-key"),
                        "logical_source_manifest_commitment": _digest(
                            "e1-run-source"
                        ),
                        "projection_manifest_commitment": _digest(
                            "e1-run-projection"
                        ),
                        "source_projection_contract_digest": _digest(
                            "e1-run-contract"
                        ),
                        "review_receipt_commitment": _digest("e1-run-review"),
                        "policy_digest": _digest("e1-run-policy"),
                        "shadow_authorization_id": shadow_authorization_id,
                        "release_manifest_digest": _digest("e1-run-release"),
                        "migration_tool_bundle_digest": _digest("e1-run-tool"),
                        "core_oci_manifest_digest": _digest("e1-run-oci"),
                        "core_schema_digest": _digest("e1-run-schema"),
                        "core_capability_digest": _digest("e1-run-capability"),
                        "signing_key_fingerprint": _digest("e1-run-signing-key"),
                        "review_signature": _signature("e1-run-signature"),
                    },
                )
                impact_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_impacts ("
                        "impact_id, run_id, operation_id, impact_code, "
                        "readiness_suspension, removed_leaf_commitment_count, "
                        "unlinked_projection_count, impact_commitment) VALUES ("
                        ":impact_id, :run_id, :operation_id, "
                        "'leaf_commitments_removed', 'required', 2, 0, "
                        ":impact_commitment)"
                    ),
                    {
                        "impact_id": impact_id,
                        "run_id": run_id,
                        "operation_id": operation_id,
                        "impact_commitment": _digest("e1-impact"),
                    },
                )

                receipt_sql = text(
                    "INSERT INTO operations."
                    "reviewed_identity_migration_erasure_receipts ("
                    "receipt_id, impact_id, run_id, operation_id, impact_code, "
                    "removed_leaf_commitment_count, unlinked_projection_count, "
                    "result_code, completion_txid, receipt_commitment) VALUES ("
                    ":receipt_id, :impact_id, :run_id, :operation_id, "
                    ":impact_code, :removed_count, :unlinked_count, "
                    ":result_code, txid_current(), :receipt_commitment)"
                )
                receipt_parameters = {
                    "receipt_id": uuid7(),
                    "impact_id": impact_id,
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "impact_code": "leaf_commitments_removed",
                    "removed_count": 2,
                    "unlinked_count": 0,
                    "result_code": "migration_run_erased",
                    "receipt_commitment": _digest("e1-receipt"),
                }
                with pytest.raises(DBAPIError) as receipt_without_block:
                    async with connection.begin_nested():
                        await connection.execute(receipt_sql, receipt_parameters)
                assert _sqlstate(receipt_without_block) == "23503"
                block_sql = text(
                    "INSERT INTO privacy.subject_retrieval_blocks ("
                    "block_id, operation_id, person_id, source_kind, "
                    "erasure_request_id, block_code, block_commitment) "
                    "VALUES (:block_id, :operation_id, :person_id, "
                    ":source_kind, :request_id, "
                    "'identity_person_erasure', :commitment)"
                )
                with pytest.raises(DBAPIError) as wrong_block_person:
                    async with connection.begin_nested():
                        await connection.execute(
                            block_sql,
                            {
                                "block_id": uuid7(),
                                "operation_id": operation_id,
                                "person_id": second_person_id,
                                "source_kind": "erasure_request",
                                "request_id": request_id,
                                "commitment": _digest("e1-block-wrong-person"),
                            },
                        )
                assert _sqlstate(wrong_block_person) == "23503"
                with pytest.raises(DBAPIError) as unsupported_block_source:
                    async with connection.begin_nested():
                        await connection.execute(
                            block_sql,
                            {
                                "block_id": uuid7(),
                                "operation_id": operation_id,
                                "person_id": person_id,
                                "source_kind": "auto_expiry_schedule",
                                "request_id": request_id,
                                "commitment": _digest("e1-block-wrong-source"),
                            },
                        )
                assert _sqlstate(unsupported_block_source) == "23514"
                await connection.execute(
                    block_sql,
                    {
                        "block_id": uuid7(),
                        "operation_id": operation_id,
                        "person_id": person_id,
                        "source_kind": "erasure_request",
                        "request_id": request_id,
                        "commitment": _digest("e1-block"),
                    },
                )
                with pytest.raises(DBAPIError) as wrong_receipt_counts:
                    async with connection.begin_nested():
                        await connection.execute(
                            receipt_sql,
                            {
                                **receipt_parameters,
                                "receipt_id": uuid7(),
                                "removed_count": 1,
                                "receipt_commitment": _digest(
                                    "e1-receipt-wrong-count"
                                ),
                            },
                        )
                assert _sqlstate(wrong_receipt_counts) == "23503"
                for mismatch_field in ("impact_id", "run_id", "operation_id"):
                    mismatched = {
                        **receipt_parameters,
                        "receipt_id": uuid7(),
                        mismatch_field: uuid7(),
                        "receipt_commitment": _digest(
                            f"e1-receipt-wrong-{mismatch_field}"
                        ),
                    }
                    with pytest.raises(DBAPIError) as mismatched_receipt:
                        async with connection.begin_nested():
                            await connection.execute(receipt_sql, mismatched)
                    assert _sqlstate(mismatched_receipt) == "23503"
                with pytest.raises(DBAPIError) as wrong_receipt_result:
                    async with connection.begin_nested():
                        await connection.execute(
                            receipt_sql,
                            {
                                **receipt_parameters,
                                "receipt_id": uuid7(),
                                "result_code": "linkage_unavailable",
                                "receipt_commitment": _digest(
                                    "e1-receipt-wrong-result"
                                ),
                            },
                        )
                assert _sqlstate(wrong_receipt_result) == "23514"
                await connection.execute(receipt_sql, receipt_parameters)
                with pytest.raises(DBAPIError) as duplicate_receipt:
                    async with connection.begin_nested():
                        await connection.execute(
                            receipt_sql,
                            {
                                **receipt_parameters,
                                "receipt_id": uuid7(),
                                "receipt_commitment": _digest(
                                    "e1-receipt-duplicate"
                                ),
                            },
                        )
                assert _sqlstate(duplicate_receipt) == "23505"

                directive_id = uuid7()
                schedule_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO identity.privacy_directives ("
                        "directive_id, person_id, directive, enabled, expires_at) "
                        "VALUES (:directive_id, :person_id, 'auto_expire', true, "
                        "transaction_timestamp() + interval '1 minute')"
                    ),
                    {
                        "directive_id": directive_id,
                        "person_id": second_person_id,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO privacy.auto_expiry_schedules ("
                        "schedule_id, person_id, directive_id, outbox_id, due_at, "
                        "state) VALUES (:schedule_id, :person_id, :directive_id, "
                        ":outbox_id, transaction_timestamp() + interval '1 minute', "
                        "'scheduled')"
                    ),
                    {
                        "schedule_id": schedule_id,
                        "person_id": second_person_id,
                        "directive_id": directive_id,
                        "outbox_id": uuid7(),
                    },
                )
                auto_operation_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_operations ("
                        "operation_id, source_kind, auto_expiry_schedule_id, "
                        "operation_commitment) VALUES ("
                        ":operation_id, 'auto_expiry_schedule', :schedule_id, "
                        ":commitment)"
                    ),
                    {
                        "operation_id": auto_operation_id,
                        "schedule_id": schedule_id,
                        "commitment": _digest("e1-auto-operation"),
                    },
                )
                with pytest.raises(DBAPIError) as auto_block_rejected:
                    async with connection.begin_nested():
                        await connection.execute(
                            block_sql,
                            {
                                "block_id": uuid7(),
                                "operation_id": auto_operation_id,
                                "person_id": second_person_id,
                                "source_kind": "erasure_request",
                                "request_id": request_id,
                                "commitment": _digest("e1-auto-block"),
                            },
                        )
                assert _sqlstate(auto_block_rejected) == "23503"

                auto_impact_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_impacts ("
                        "impact_id, run_id, operation_id, impact_code, "
                        "readiness_suspension, removed_leaf_commitment_count, "
                        "unlinked_projection_count, impact_commitment) VALUES ("
                        ":impact_id, :run_id, :operation_id, "
                        "'linkage_unavailable', 'required', 0, 1, :commitment)"
                    ),
                    {
                        "impact_id": auto_impact_id,
                        "run_id": run_id,
                        "operation_id": auto_operation_id,
                        "commitment": _digest("e1-auto-impact"),
                    },
                )
                with pytest.raises(DBAPIError) as auto_receipt_rejected:
                    async with connection.begin_nested():
                        await connection.execute(
                            receipt_sql,
                            {
                                "receipt_id": uuid7(),
                                "impact_id": auto_impact_id,
                                "run_id": run_id,
                                "operation_id": auto_operation_id,
                                "impact_code": "linkage_unavailable",
                                "removed_count": 0,
                                "unlinked_count": 1,
                                "result_code": "linkage_unavailable",
                                "receipt_commitment": _digest("e1-auto-receipt"),
                            },
                        )
                assert _sqlstate(auto_receipt_rejected) == "23503"
            except BaseException:
                await transaction.rollback()
                raise
            else:
                await transaction.commit()

            async with connection.begin():
                await connection.execute(
                    text(
                        "UPDATE identity.ha_user_bindings "
                        "SET revoked_at = transaction_timestamp() "
                        "- interval '1 microsecond' "
                        "WHERE binding_id = :binding_id"
                    ),
                    {"binding_id": binding_id},
                )
                persisted_scope = (
                    await connection.execute(
                        text(
                            "SELECT binding_valid_until < "
                            "'infinity'::timestamptz AS revoked, "
                            "recorded_at <= binding_valid_until AS valid "
                            "FROM privacy.person_erasure_scopes "
                            "WHERE scope_id = :scope_id"
                        ),
                        {"scope_id": scope_id},
                    )
                ).mappings().one()
                assert persisted_scope == {"revoked": True, "valid": True}

                late_artifact_id = uuid7()
                late_request_id = uuid7()
                late_commitment = _digest("e1-already-revoked")
                await connection.execute(
                    text(
                        "INSERT INTO identity.confirmation_artifacts ("
                        "artifact_id, principal_id, purpose, proposal_digest, "
                        "client_nonce_sha256, issued_at, expires_at, consumed_at"
                        ") VALUES (:artifact_id, :principal_id, "
                        "'identity_person_erasure', :proposal_digest, :nonce, "
                        "transaction_timestamp(), "
                        "transaction_timestamp() + interval '5 minutes', "
                        "transaction_timestamp())"
                    ),
                    {
                        "artifact_id": late_artifact_id,
                        "principal_id": principal_id,
                        "proposal_digest": late_commitment,
                        "nonce": _digest("e1-already-revoked-nonce"),
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO privacy.erasure_requests ("
                        "erasure_request_id, principal_id, scope, state, "
                        "policy_digest, identity_person_scope_commitment) "
                        "VALUES (:request_id, :principal_id, "
                        "'{\"kind\":\"identity_person\"}'::jsonb, "
                        "'preview', :policy_digest, :scope_commitment)"
                    ),
                    {
                        "request_id": late_request_id,
                        "principal_id": principal_id,
                        "policy_digest": _digest("e1-policy"),
                        "scope_commitment": late_commitment,
                    },
                )
                late_binding = (
                    await connection.execute(
                        text(
                            "SELECT confirmed_at, "
                            "identity_erasure_binding_valid_until AS valid_until "
                            "FROM identity.ha_user_bindings "
                            "WHERE binding_id = :binding_id"
                        ),
                        {"binding_id": binding_id},
                    )
                ).mappings().one()
                late_artifact = (
                    await connection.execute(
                        text(
                            "SELECT consumed_at, expires_at FROM "
                            "identity.confirmation_artifacts "
                            "WHERE artifact_id = :artifact_id"
                        ),
                        {"artifact_id": late_artifact_id},
                    )
                ).mappings().one()
                with pytest.raises(DBAPIError) as already_revoked:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO privacy.person_erasure_scopes ("
                                "scope_id, erasure_request_id, person_id, "
                                "authorizing_principal_id, authority_kind, "
                                "ha_binding_id, binding_confirmed_at, "
                                "binding_valid_until, confirmation_artifact_id, "
                                "confirmation_purpose, confirmation_consumed_at, "
                                "confirmation_expires_at, scope_kind, "
                                "scope_commitment) VALUES ("
                                ":scope_id, :request_id, :person_id, "
                                ":principal_id, 'ha_user', :binding_id, "
                                ":binding_confirmed_at, :binding_valid_until, "
                                ":artifact_id, 'identity_person_erasure', "
                                ":confirmation_consumed_at, "
                                ":confirmation_expires_at, 'identity_person', "
                                ":scope_commitment)"
                            ),
                            {
                                "scope_id": uuid7(),
                                "request_id": late_request_id,
                                "person_id": person_id,
                                "principal_id": principal_id,
                                "binding_id": binding_id,
                                "binding_confirmed_at": late_binding["confirmed_at"],
                                "binding_valid_until": late_binding["valid_until"],
                                "artifact_id": late_artifact_id,
                                "confirmation_consumed_at": late_artifact[
                                    "consumed_at"
                                ],
                                "confirmation_expires_at": late_artifact[
                                    "expires_at"
                                ],
                                "scope_commitment": late_commitment,
                            },
                        )
                assert _sqlstate(already_revoked) == "23514"
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv(LIFECYCLE_DATABASE_ENV),
    reason=(
        "TEST_PHASE3_IDENTITY_ERASURE_E1_LIFECYCLE_DATABASE_URL is required; "
        "it must point to a disposable exact post-grants revision 0010 database"
    ),
)
def test_postgresql_e1_upgrade_round_trip_and_evidence_downgrade_guard() -> None:
    database_url = os.environ[LIFECYCLE_DATABASE_ENV]

    upgrade = _run_alembic(database_url, "upgrade", "0011_identity_erasure_e1")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    downgrade = _run_alembic(database_url, "downgrade", "0010_identity_erasure_source")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "0010_identity_erasure_source"
            assert connection.execute(
                text(
                    "SELECT to_regclass('privacy.person_erasure_scopes') "
                    "IS NULL AND NOT EXISTS (SELECT 1 FROM pg_attribute "
                    "WHERE attrelid = 'identity.ha_user_bindings'::regclass "
                    "AND attname = 'identity_erasure_binding_valid_until' "
                    "AND attnum > 0 AND NOT attisdropped)"
                )
            ).scalar_one() is True
            binding_read_columns = connection.execute(
                text(
                    "SELECT coalesce(array_agg(attname::text ORDER BY attnum) "
                    "FILTER (WHERE has_column_privilege('home_agent_api', "
                    "'identity.ha_user_bindings', attname, 'SELECT')), "
                    "ARRAY[]::text[]) FROM pg_attribute WHERE attrelid = "
                    "'identity.ha_user_bindings'::regclass AND attnum > 0 "
                    "AND NOT attisdropped"
                )
            ).scalar_one()
            assert binding_read_columns == [
                "binding_id",
                "proposal_id",
                "ha_user_id",
                "principal_id",
                "person_id",
                "confirmed_by_principal_id",
                "confirmed_at",
                "revoked_at",
                "source_artifact_id",
            ]
            assert connection.execute(
                text(
                    "SELECT has_table_privilege('home_agent_api', "
                    "'identity.ha_user_bindings', 'SELECT,INSERT,UPDATE,DELETE')"
                )
            ).scalar_one() is False
    finally:
        engine.dispose()

    reupgrade = _run_alembic(database_url, "upgrade", "0011_identity_erasure_e1")
    assert reupgrade.returncode == 0, reupgrade.stdout + reupgrade.stderr

    engine = create_engine(database_url, isolation_level="SERIALIZABLE")
    try:
        person_id = uuid7()
        principal_id = uuid7()
        request_id = uuid7()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO identity.people ("
                    "person_id, display_name, status, privacy_scope) "
                    "VALUES (:person_id, 'E1 downgrade evidence', "
                    "'active', 'private')"
                ),
                {"person_id": person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO identity.principals ("
                    "principal_id, person_id, kind, display_label, status) "
                    "VALUES (:principal_id, :person_id, 'ha_user', "
                    "'E1 downgrade principal', 'active')"
                ),
                {"principal_id": principal_id, "person_id": person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO privacy.erasure_requests ("
                    "erasure_request_id, principal_id, scope, state, "
                    "policy_digest, identity_person_scope_commitment) VALUES ("
                    ":request_id, :principal_id, "
                    "'{\"kind\":\"identity_person\"}'::jsonb, 'preview', "
                    ":policy_digest, :scope_commitment)"
                ),
                {
                    "request_id": request_id,
                    "principal_id": principal_id,
                    "policy_digest": _digest("e1-downgrade-policy"),
                    "scope_commitment": _digest("e1-downgrade-scope"),
                },
            )
    finally:
        engine.dispose()

    refused = _run_alembic(
        database_url, "downgrade", "0010_identity_erasure_source"
    )
    assert refused.returncode != 0
    refusal_output = refused.stdout + refused.stderr
    assert "identity_erasure_e1_downgrade_blocked" in refusal_output

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one() == "0011_identity_erasure_e1"
    finally:
        engine.dispose()
