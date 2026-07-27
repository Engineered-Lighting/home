from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from psycopg.types.range import Range
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import schema
from app.ids import uuid7


ROOT = Path(__file__).resolve().parents[1]
OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E2_OWNER_DATABASE_URL"
LIFECYCLE_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E2_LIFECYCLE_DATABASE_URL"
LIFECYCLE_ERASURE_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_ERASURE_E2_LIFECYCLE_ERASURE_DATABASE_URL"
)
ROLE_DATABASE_ENVS = {
    "home_agent_api": "TEST_PHASE3_IDENTITY_ERASURE_E2_API_DATABASE_URL",
    "home_agent_binding_operator": (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_BINDING_OPERATOR_DATABASE_URL"
    ),
    "home_agent_ingest": "TEST_PHASE3_IDENTITY_ERASURE_E2_INGEST_DATABASE_URL",
    "home_agent_worker": "TEST_PHASE3_IDENTITY_ERASURE_E2_WORKER_DATABASE_URL",
    "home_agent_erasure": "TEST_PHASE3_IDENTITY_ERASURE_E2_ERASURE_DATABASE_URL",
    "home_agent_rollout": "TEST_PHASE3_IDENTITY_ERASURE_E2_ROLLOUT_DATABASE_URL",
    "home_agent_backup": "TEST_PHASE3_IDENTITY_ERASURE_E2_BACKUP_DATABASE_URL",
}
RUNTIME_ROLES = tuple(ROLE_DATABASE_ENVS)
MANDATORY_RESIDUALS = (
    "live_identity_rows_retained",
    "semantic_dependencies_not_evaluated",
    "generic_artifact_lineage_unresolved",
    "external_cleanup_not_evaluated",
    "legacy_cleanup_not_evaluated",
    "backup_expiry_unverified",
    "preexisting_snapshot_visibility",
)
DERIVED_TARGETS = {
    "knowledge.place_locators",
    "knowledge.fact_support",
    "engagement.presentation_attempts",
}


@dataclass(frozen=True, slots=True)
class RowProbe:
    table: str
    id_column: str
    value: uuid.UUID


@dataclass(frozen=True, slots=True)
class Scenario:
    person_id: uuid.UUID
    other_person_id: uuid.UUID
    principal_id: uuid.UUID
    other_principal_id: uuid.UUID
    ha_user_id: str
    target_rows: tuple[RowProbe, ...]
    control_rows: tuple[RowProbe, ...]
    payload: dict[str, object]
    place_id: uuid.UUID
    fact_version_id: uuid.UUID
    memory_transaction_id: uuid.UUID
    initiative_id: uuid.UUID


def _required_runtime_environment() -> bool:
    return bool(os.getenv(OWNER_DATABASE_ENV)) and all(
        os.getenv(name) for name in ROLE_DATABASE_ENVS.values()
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _residual_commitments(record_digest: str) -> dict[str, str]:
    return {
        code: _digest(f"identity-person-erasure-residual-v2:{record_digest}:{code}")
        for code in MANDATORY_RESIDUALS
    }


def _payload(
    *,
    person_id: uuid.UUID,
    ledger_epoch: int | None = None,
    ledger_outbox_id: uuid.UUID | None = None,
) -> dict[str, object]:
    block_id = uuid7()
    record_digest = _digest(f"record:{block_id}")
    completed_at = datetime.now(UTC) - timedelta(seconds=1)
    return {
        "version": 2,
        "subject_kind": "identity_person",
        "outbox_id": str(ledger_outbox_id or uuid7()),
        "erasure_request_id": str(uuid7()),
        "person_id": str(person_id),
        "operation_id": str(uuid7()),
        "block_id": str(block_id),
        "block_commitment": _digest(f"block:{block_id}"),
        "outcome_code": "retrieval_block_active",
        "operation_codes": ["activate_identity_person_retrieval_block"],
        "policy_digest": _digest("e2-runtime-policy"),
        "completed_at": completed_at.isoformat(),
        "source_created_at": (completed_at - timedelta(seconds=1)).isoformat(),
        "external_pending_codes": [],
        "legacy_untracked_codes": [],
        "checkpoint_affected": True,
        "backup_expiry_at": None,
        "exact_residual_codes": list(MANDATORY_RESIDUALS),
        "ledger_epoch": ledger_epoch or secrets.randbelow(2**62 - 1) + 1,
        "ledger_record_hash": _digest(f"ledger:{block_id}"),
        "ledger_record_digest": record_digest,
        "residual_commitments": _residual_commitments(record_digest),
    }


async def _seed_scenario(connection) -> Scenario:
    token = uuid.uuid4().hex
    now = datetime.now(UTC) - timedelta(minutes=2)
    staged_at = now + timedelta(seconds=1)
    consumed_at = staged_at + timedelta(seconds=1)
    expires_at = now + timedelta(minutes=30)
    person_id, other_person_id = uuid7(), uuid7()
    principal_id, other_principal_id = uuid7(), uuid7()
    request_id, proposal_id, confirmation_id, binding_id = (
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
    )
    source_binding_id = uuid7()
    alias_id, recognition_id, role_label_id, relationship_id = (
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
    )
    place_id, locator_id, visit_id, memory_id = uuid7(), uuid7(), uuid7(), uuid7()
    fact_version_id, fact_id, support_id, artifact_id = (
        uuid7(),
        uuid7(),
        uuid7(),
        uuid7(),
    )
    preference_id, initiative_id, attempt_id = uuid7(), uuid7(), uuid7()
    directive_id, edge_block_id, edge_user_block_id = uuid7(), uuid7(), uuid7()
    erasure_request_id, outbox_id = uuid7(), uuid7()
    ha_user_id = f"e2-runtime-{token}"

    await connection.execute(
        insert(schema.people),
        [
            {
                "person_id": person_id,
                "display_name": f"E2 target {token}",
                "status": "active",
                "privacy_scope": "private",
            },
            {
                "person_id": other_person_id,
                "display_name": f"E2 control {token}",
                "status": "active",
                "privacy_scope": "private",
            },
        ],
    )
    await connection.execute(
        insert(schema.principals),
        [
            {
                "principal_id": principal_id,
                "person_id": person_id,
                "kind": "ha_user",
                "display_label": f"E2 principal {token}",
                "status": "active",
            },
            {
                "principal_id": other_principal_id,
                "person_id": other_person_id,
                "kind": "ha_user",
                "display_label": f"E2 other principal {token}",
                "status": "active",
            },
        ],
    )
    await connection.execute(
        insert(schema.principal_binding_requests).values(
            request_id=request_id,
            ha_user_id=ha_user_id,
            review_code=_review_code(),
            state="consumed",
            requested_at=now,
            staged_at=staged_at,
            closed_at=consumed_at,
            expires_at=expires_at,
        )
    )
    proposal_digest = _digest(f"proposal:{token}")
    await connection.execute(
        insert(schema.confirmation_artifacts).values(
            artifact_id=confirmation_id,
            principal_id=principal_id,
            purpose="ha_user_person_binding.confirm",
            proposal_digest=proposal_digest,
            client_nonce_sha256=_digest(f"nonce:{token}"),
            issued_at=staged_at,
            expires_at=expires_at,
            consumed_at=consumed_at,
        )
    )
    await connection.execute(
        insert(schema.principal_binding_proposals).values(
            proposal_id=proposal_id,
            operator_request_id=uuid7(),
            request_id=request_id,
            ha_user_id=ha_user_id,
            person_id=person_id,
            reviewed_display_label=f"E2 reviewed {token}",
            person_snapshot_digest=_digest(f"person:{token}"),
            proposal_digest=proposal_digest,
            state="consumed",
            stage_receipt_digest=_digest(f"stage:{token}"),
            staged_at=staged_at,
            expires_at=expires_at,
            consumed_at=consumed_at,
            result_principal_id=principal_id,
            confirmation_artifact_id=confirmation_id,
        )
    )
    await connection.execute(
        insert(schema.ha_user_bindings).values(
            binding_id=binding_id,
            proposal_id=proposal_id,
            ha_user_id=ha_user_id,
            principal_id=principal_id,
            person_id=person_id,
            confirmed_by_principal_id=principal_id,
            confirmed_at=consumed_at,
            source_artifact_id=confirmation_id,
        )
    )
    await connection.execute(
        insert(schema.source_entity_bindings).values(
            binding_id=source_binding_id,
            source_system="home_assistant",
            entity_id=f"person.e2_{token}",
            principal_id=principal_id,
            person_id=person_id,
            confirmed_by_principal_id=principal_id,
            confirmation_artifact_id=confirmation_id,
            confirmed_at=consumed_at,
        )
    )
    await connection.execute(
        insert(schema.aliases).values(
            alias_id=alias_id,
            person_id=person_id,
            alias=f"E2 alias {token}",
            normalized_alias=f"e2-alias-{token}",
            alias_kind="nickname",
        )
    )
    await connection.execute(
        insert(schema.external_recognition_bindings).values(
            binding_id=recognition_id,
            person_id=person_id,
            external_system="frigate",
            external_id=f"e2-{token}",
            status="active",
            source_ref=f"e2://recognition/{token}",
            source_version=1,
            source_snapshot_sha256=_digest(f"recognition:{token}"),
        )
    )
    await connection.execute(
        insert(schema.legacy_role_labels).values(
            label_id=role_label_id,
            person_id=person_id,
            role_label="parent",
            perspective="unknown",
            source_ref=f"e2://role/{token}",
            source_version=1,
            source_snapshot_sha256=_digest(f"role:{token}"),
        )
    )
    await connection.execute(
        insert(schema.legacy_relationship_candidates).values(
            candidate_id=relationship_id,
            from_person_id=person_id,
            to_person_id=other_person_id,
            relationship_label="parent",
            relationship_status="active",
            perspective="unknown",
            authoritative=False,
            source_ref=f"e2://relationship/{token}",
            source_snapshot_sha256=_digest(f"relationship:{token}"),
        )
    )
    await connection.execute(
        insert(schema.places).values(
            place_id=place_id,
            place_type="property",
            canonical_name=f"E2 place {token}",
            owner_principal_id=principal_id,
            privacy_scope="private",
            status="active",
        )
    )
    await connection.execute(
        insert(schema.place_locators).values(
            locator_id=locator_id,
            place_id=place_id,
            ciphertext=b"e2-locator",
            nonce=b"e2-nonce-123",
            key_id="e2-test-key",
            locator_sha256=_digest(f"locator:{token}"),
            radius_m=40,
            valid_range=Range(now, None, bounds="[)"),
        )
    )
    await connection.execute(
        insert(schema.visits).values(
            visit_id=visit_id,
            principal_id=principal_id,
            place_id=place_id,
            first_root_observation_id=uuid7(),
            observed_range=Range(now, None, bounds="[)"),
            state="visiting",
            freshness="fresh",
            coverage="sufficient",
            anchor_ciphertext=b"e2-anchor",
            anchor_nonce=b"e2-anchor12",
            anchor_sha256=_digest(f"anchor:{token}"),
            anchor_kind="specific",
            anchor_radius_m=40,
        )
    )
    await connection.execute(
        insert(schema.memory_transactions).values(
            transaction_id=memory_id,
            principal_id=principal_id,
            visit_id=visit_id,
            kind="place_social_descriptor",
            state="committed",
            candidate={"kind": "e2_test"},
            preview={"kind": "e2_test"},
            verifier_results=[],
            policy_version="e2-runtime-v1",
            policy_digest=_digest("e2-runtime-memory-policy"),
            confirmation_digest=_digest(f"confirmation:{token}"),
            confirmed_at=consumed_at,
        )
    )
    await connection.execute(
        insert(schema.fact_versions).values(
            fact_version_id=fact_version_id,
            fact_id=fact_id,
            version=1,
            subject_type="person",
            subject_id=person_id,
            predicate="parent_of",
            object={"person_id": str(other_person_id)},
            perspective_principal_id=principal_id,
            valid_range=Range(now, None, bounds="[)"),
            system_range=Range(now, None, bounds="[)"),
            authority="explicit_subject",
            support="explicit_authority",
            contradiction="none",
            freshness="not_applicable",
            coverage="not_applicable",
            resolution="accepted",
            privacy_scope="private",
            memory_transaction_id=memory_id,
        )
    )
    await connection.execute(
        insert(schema.artifact_registry).values(
            artifact_id=artifact_id,
            artifact_kind="e2_test",
            store="postgresql",
            external_ref=f"e2://artifact/{token}",
            content_sha256=_digest(f"artifact:{token}"),
            owner_principal_id=principal_id,
            retention_class="e2_test",
            status="active",
        )
    )
    await connection.execute(
        insert(schema.fact_support).values(
            support_id=support_id,
            fact_version_id=fact_version_id,
            artifact_id=artifact_id,
            root_observation_id=uuid7(),
            dependency_domain="e2_test",
            support_role="authority",
        )
    )
    await connection.execute(
        insert(schema.preferences).values(
            preference_id=preference_id,
            principal_id=principal_id,
            key=f"e2.preference.{token}",
            enabled=True,
        )
    )
    await connection.execute(
        insert(schema.initiatives).values(
            initiative_id=initiative_id,
            principal_id=principal_id,
            visit_id=visit_id,
            source_fact_version_id=fact_version_id,
            purpose="travel_arrival",
            dedupe_key=f"e2:{token}",
            sensitivity="private",
            allowed_surfaces=["tauri"],
            template_key="e2_test",
            state="pending",
            expires_at=expires_at,
        )
    )
    await connection.execute(
        insert(schema.presentation_attempts).values(
            attempt_id=attempt_id,
            initiative_id=initiative_id,
            session_id=f"e2-session-{token}",
            surface="tauri",
            outcome="presented",
        )
    )

    await connection.execute(
        insert(schema.privacy_directives).values(
            directive_id=directive_id,
            person_id=person_id,
            directive="private",
            enabled=True,
        )
    )
    await connection.execute(
        insert(schema.edge_privacy_blocks).values(
            block_id=edge_block_id,
            source_system="home_assistant",
            entity_id=f"person.e2_control_{token}",
            reason_code="ignored",
            person_id=person_id,
        )
    )
    await connection.execute(
        insert(schema.edge_privacy_user_blocks).values(
            block_id=edge_user_block_id,
            ha_user_id=ha_user_id,
            reason_code="ignored",
            person_id=person_id,
        )
    )
    await connection.execute(
        insert(schema.erasure_requests).values(
            erasure_request_id=erasure_request_id,
            principal_id=principal_id,
            scope={"kind": "descriptor_fact", "fact_id": str(fact_id)},
            state="preview",
            policy_digest=_digest("e2-control-erasure-policy"),
        )
    )
    await connection.execute(
        insert(schema.outbox).values(
            outbox_id=outbox_id,
            topic="privacy.erasure.completed",
            aggregate_id=erasure_request_id,
            payload={"kind": "e2_control"},
            state="complete",
            completed_at=consumed_at,
        )
    )

    target_rows = (
        RowProbe("identity.people", "person_id", person_id),
        RowProbe("identity.principals", "principal_id", principal_id),
        RowProbe("identity.ha_user_bindings", "binding_id", binding_id),
        RowProbe("identity.principal_binding_proposals", "proposal_id", proposal_id),
        RowProbe("identity.source_entity_bindings", "binding_id", source_binding_id),
        RowProbe("identity.aliases", "alias_id", alias_id),
        RowProbe(
            "identity.external_recognition_bindings", "binding_id", recognition_id
        ),
        RowProbe("identity.legacy_role_labels", "label_id", role_label_id),
        RowProbe(
            "identity.legacy_relationship_candidates",
            "candidate_id",
            relationship_id,
        ),
        RowProbe("knowledge.places", "place_id", place_id),
        RowProbe("knowledge.place_locators", "locator_id", locator_id),
        RowProbe("knowledge.visits", "visit_id", visit_id),
        RowProbe("knowledge.memory_transactions", "transaction_id", memory_id),
        RowProbe("knowledge.fact_versions", "fact_version_id", fact_version_id),
        RowProbe("knowledge.fact_support", "support_id", support_id),
        RowProbe("engagement.preferences", "preference_id", preference_id),
        RowProbe("engagement.initiatives", "initiative_id", initiative_id),
        RowProbe("engagement.presentation_attempts", "attempt_id", attempt_id),
        RowProbe("privacy.artifact_registry", "artifact_id", artifact_id),
    )
    control_rows = (
        RowProbe("identity.principal_binding_requests", "request_id", request_id),
        RowProbe("identity.confirmation_artifacts", "artifact_id", confirmation_id),
        RowProbe("identity.privacy_directives", "directive_id", directive_id),
        RowProbe("identity.edge_privacy_blocks", "block_id", edge_block_id),
        RowProbe("identity.edge_privacy_user_blocks", "block_id", edge_user_block_id),
        RowProbe("privacy.erasure_requests", "erasure_request_id", erasure_request_id),
        RowProbe("operations.outbox", "outbox_id", outbox_id),
    )
    return Scenario(
        person_id=person_id,
        other_person_id=other_person_id,
        principal_id=principal_id,
        other_principal_id=other_principal_id,
        ha_user_id=ha_user_id,
        target_rows=target_rows,
        control_rows=control_rows,
        payload=_payload(person_id=person_id),
        place_id=place_id,
        fact_version_id=fact_version_id,
        memory_transaction_id=memory_id,
        initiative_id=initiative_id,
    )


async def _set_subject_scope(connection, scenario: Scenario) -> None:
    await connection.execute(
        text(
            "SELECT set_config('app.principal_id', :principal, true), "
            "set_config('app.ha_user_id', :ha_user, true)"
        ),
        {"principal": str(scenario.principal_id), "ha_user": scenario.ha_user_id},
    )


async def _visibility(
    engine: AsyncEngine,
    expected_role: str,
    scenario: Scenario,
    probes: tuple[RowProbe, ...],
) -> dict[str, bool | None]:
    visible: dict[str, bool | None] = {}
    async with engine.begin() as connection:
        assert (
            await connection.execute(text("SELECT session_user"))
        ).scalar_one() == expected_role
        await _set_subject_scope(connection, scenario)
        for probe in probes:
            schema_name = probe.table.split(".", 1)[0]
            schema_permitted = (
                await connection.execute(
                    text(
                        "SELECT has_schema_privilege("
                        "current_user, :schema, 'USAGE')"
                    ),
                    {"schema": schema_name},
                )
            ).scalar_one()
            if not schema_permitted:
                visible[probe.table] = None
                continue
            permitted = (
                await connection.execute(
                    text(
                        "SELECT has_column_privilege(current_user, :table, "
                        ":column, 'SELECT')"
                    ),
                    {"table": probe.table, "column": probe.id_column},
                )
            ).scalar_one()
            if not permitted:
                visible[probe.table] = None
                continue
            visible[probe.table] = bool(
                (
                    await connection.execute(
                        text(
                            f"SELECT {probe.id_column} FROM {probe.table} "
                            f"WHERE {probe.id_column} = :value"
                        ),
                        {"value": probe.value},
                    )
                ).scalar_one_or_none()
            )
    return visible


async def _replay(engine: AsyncEngine, payload: dict[str, object]) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT privacy.replay_identity_person_retrieval_block_v2("
                    "CAST(:payload AS jsonb))"
                ),
                {"payload": json.dumps(payload, separators=(",", ":"))},
            )
        ).scalar_one()


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(error.value.orig, "sqlstate", None)


async def _assert_catalog_boundary(connection) -> None:
    suppression_helpers = (
        "privacy.identity_person_is_blocked(uuid)",
        "privacy.identity_principal_is_blocked(uuid)",
        "privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)",
    )
    kernel_denied_functions = (
        "privacy.reject_tombstoned_identity_write()",
        "privacy.enforce_identity_person_erasure_residual_set()",
        "privacy.replay_identity_person_retrieval_block_v2(jsonb)",
        "ingest.reject_artifact_link_cycle()",
    )
    for signature in (*suppression_helpers, *kernel_denied_functions):
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT has_function_privilege("
                        "'home_agent_identity_erasure_kernel', "
                        "CAST(:signature AS regprocedure), 'EXECUTE') "
                        "AS kernel_execute, EXISTS ("
                        "SELECT 1 FROM pg_proc function_row, "
                        "LATERAL aclexplode(COALESCE("
                        "function_row.proacl, "
                        "acldefault('f', function_row.proowner))) function_acl "
                        "WHERE function_row.oid=CAST(:signature AS regprocedure) "
                        "AND function_acl.grantee=0 "
                        "AND function_acl.privilege_type='EXECUTE'"
                        ") AS public_execute"
                    ),
                    {"signature": signature},
                )
            )
            .mappings()
            .one()
        )
        assert row["kernel_execute"] is (signature in suppression_helpers)
        assert row["public_execute"] is False

    for table in (
        "identity.people",
        "identity.principals",
        "identity.ha_user_bindings",
        "identity.principal_binding_proposals",
        "identity.source_entity_bindings",
        "identity.aliases",
        "identity.external_recognition_bindings",
        "identity.legacy_role_labels",
        "identity.legacy_relationship_candidates",
        "knowledge.places",
        "knowledge.place_locators",
        "knowledge.visits",
        "knowledge.memory_transactions",
        "knowledge.fact_versions",
        "knowledge.fact_support",
        "engagement.preferences",
        "engagement.initiatives",
        "engagement.presentation_attempts",
        "privacy.artifact_registry",
    ):
        schema_name, table_name = table.split(".")
        policy_name = f"{schema_name}_{table_name}_e2_identity_suppression"
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT relation.relrowsecurity, relation.relforcerowsecurity, "
                        "policy.polpermissive, policy.polcmd, "
                        "policy.polqual IS NOT NULL AS has_using, "
                        "policy.polwithcheck IS NOT NULL AS has_check, "
                        "ARRAY(SELECT role.rolname "
                        "FROM unnest(policy.polroles) AS policy_role(role_oid) "
                        "JOIN pg_roles role ON role.oid=policy_role.role_oid "
                        "ORDER BY role.rolname) roles "
                        "FROM pg_class relation JOIN pg_policy policy "
                        "ON policy.polrelid=relation.oid "
                        "WHERE relation.oid=CAST(:table AS regclass) "
                        "AND policy.polname=:policy"
                    ),
                    {"table": table, "policy": policy_name},
                )
            )
            .mappings()
            .one()
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["polpermissive"] is False
        assert row["polcmd"] == "*"
        assert row["has_using"] is True
        assert row["has_check"] is True
        assert set(row["roles"]) == set(RUNTIME_ROLES)

        trigger_name = f"{schema_name}_{table_name}_e2_anti_resurrection"
        trigger_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid="
                    "CAST(:table AS regclass) AND tgname=:trigger "
                    "AND NOT tgisinternal AND tgenabled='O'"
                ),
                {"table": table, "trigger": trigger_name},
            )
        ).scalar_one()
        assert trigger_count == (0 if table in DERIVED_TARGETS else 1)

    kernel_policy = (
        (
            await connection.execute(
                text(
                    "SELECT NOT polpermissive AS restrictive, polcmd, "
                    "pg_get_expr(polqual, polrelid) AS expression "
                    "FROM pg_policy WHERE polrelid='identity.principals'::regclass "
                    "AND polname='principals_e2_erasure_kernel_lookup'"
                )
            )
        )
        .mappings()
        .one()
    )
    assert kernel_policy["restrictive"] is False
    assert kernel_policy["polcmd"] == "r"
    assert "home_agent_identity_erasure_kernel" in kernel_policy["expression"]


@pytest.mark.skipif(
    not _required_runtime_environment(),
    reason="dedicated E2 owner and all runtime-role PostgreSQL URLs are required",
)
@pytest.mark.asyncio
async def test_postgresql_e2_all_target_rls_and_control_evidence_matrix() -> None:
    owner = create_async_engine(os.environ[OWNER_DATABASE_ENV], pool_size=1)
    engines = {
        role: create_async_engine(os.environ[environment], pool_size=1)
        for role, environment in ROLE_DATABASE_ENVS.items()
    }
    try:
        async with owner.begin() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one() == "0012_identity_erasure_e2"
            scenario = await _seed_scenario(connection)
            await _assert_catalog_boundary(connection)

        before_targets = {
            role: await _visibility(engine, role, scenario, scenario.target_rows)
            for role, engine in engines.items()
        }
        before_controls = {
            role: await _visibility(engine, role, scenario, scenario.control_rows)
            for role, engine in engines.items()
        }
        for probe in scenario.target_rows:
            assert any(
                matrix[probe.table] is True for matrix in before_targets.values()
            ), f"no runtime role could observe {probe.table} before tombstoning"

        assert await _replay(
            engines["home_agent_erasure"], scenario.payload
        ) == uuid.UUID(str(scenario.payload["block_id"]))

        after_targets = {
            role: await _visibility(engine, role, scenario, scenario.target_rows)
            for role, engine in engines.items()
        }
        after_controls = {
            role: await _visibility(engine, role, scenario, scenario.control_rows)
            for role, engine in engines.items()
        }
        for role in RUNTIME_ROLES:
            for probe in scenario.target_rows:
                assert after_targets[role][probe.table] in {False, None}
            assert after_controls[role] == before_controls[role]

        async with owner.begin() as connection:
            for probe in (*scenario.target_rows, *scenario.control_rows):
                assert (
                    await connection.execute(
                        text(
                            f"SELECT count(*) FROM {probe.table} "
                            f"WHERE {probe.id_column}=:value"
                        ),
                        {"value": probe.value},
                    )
                ).scalar_one() == 1
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM privacy.subject_retrieval_blocks "
                        "WHERE person_id=:person"
                    ),
                    {"person": scenario.person_id},
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM privacy.identity_person_erasure_residuals "
                        "WHERE person_id=:person AND state='open'"
                    ),
                    {"person": scenario.person_id},
                )
            ).scalar_one() == len(MANDATORY_RESIDUALS)

        with pytest.raises(DBAPIError) as alias_reinsert:
            async with owner.begin() as connection:
                await connection.execute(
                    insert(schema.aliases).values(
                        alias_id=uuid7(),
                        person_id=scenario.person_id,
                        alias="blocked alias",
                        normalized_alias=f"blocked-{uuid.uuid4().hex}",
                        alias_kind="nickname",
                    )
                )
        assert _sqlstate(alias_reinsert) == "23514"

        with pytest.raises(DBAPIError) as place_reinsert:
            async with owner.begin() as connection:
                await connection.execute(
                    insert(schema.places).values(
                        place_id=uuid7(),
                        place_type="other",
                        owner_principal_id=scenario.principal_id,
                        privacy_scope="private",
                        status="active",
                    )
                )
        assert _sqlstate(place_reinsert) == "23514"

        derived_inserts = {
            "knowledge.place_locators": insert(schema.place_locators).values(
                locator_id=uuid7(),
                place_id=scenario.place_id,
                ciphertext=b"blocked-locator",
                nonce=b"blocked-nonce",
                key_id="e2-test-key",
                locator_sha256=_digest("blocked-locator"),
                radius_m=40,
                valid_range=Range(datetime.now(UTC), None, bounds="[)"),
            ),
            "knowledge.fact_support": insert(schema.fact_support).values(
                support_id=uuid7(),
                fact_version_id=scenario.fact_version_id,
                artifact_id=uuid7(),
                dependency_domain="e2_blocked",
                support_role="authority",
            ),
            "engagement.presentation_attempts": insert(
                schema.presentation_attempts
            ).values(
                attempt_id=uuid7(),
                initiative_id=scenario.initiative_id,
                session_id=f"blocked-{uuid.uuid4().hex}",
                surface="tauri",
                outcome="presented",
            ),
        }
        for table, statement in derived_inserts.items():
            writer = None
            for role, engine in engines.items():
                async with engine.begin() as connection:
                    can_insert = (
                        await connection.execute(
                            text(
                                "SELECT has_table_privilege(current_user, :table, "
                                "'INSERT')"
                            ),
                            {"table": table},
                        )
                    ).scalar_one()
                if can_insert:
                    writer = (role, engine)
                    break
            assert writer is not None, f"no runtime writer covers {table}"
            with pytest.raises(DBAPIError) as rejected:
                async with writer[1].begin() as connection:
                    await _set_subject_scope(connection, scenario)
                    await connection.execute(statement)
            assert _sqlstate(rejected) == "42501"
    finally:
        for engine in engines.values():
            await engine.dispose()
        await owner.dispose()


@pytest.mark.skipif(
    not os.getenv(OWNER_DATABASE_ENV)
    or not os.getenv(ROLE_DATABASE_ENVS["home_agent_erasure"]),
    reason="dedicated E2 owner and erasure PostgreSQL URLs are required",
)
@pytest.mark.asyncio
async def test_postgresql_e2_restore_before_person_and_replay_mismatches() -> None:
    owner = create_async_engine(os.environ[OWNER_DATABASE_ENV], pool_size=1)
    erasure = create_async_engine(
        os.environ[ROLE_DATABASE_ENVS["home_agent_erasure"]], pool_size=1
    )
    absent_person = uuid7()
    payload = _payload(person_id=absent_person)
    try:
        assert await _replay(erasure, payload) == uuid.UUID(str(payload["block_id"]))

        with pytest.raises(DBAPIError) as resurrected:
            async with owner.begin() as connection:
                await connection.execute(
                    insert(schema.people).values(
                        person_id=absent_person,
                        display_name="must remain blocked",
                        status="active",
                        privacy_scope="private",
                    )
                )
        assert _sqlstate(resurrected) == "23514"

        same_person = dict(payload)
        same_person["block_id"] = str(uuid7())
        same_person["block_commitment"] = _digest("different-block")
        with pytest.raises(DBAPIError) as person_conflict:
            await _replay(erasure, same_person)
        assert _sqlstate(person_conflict) == "23505"

        epoch_conflict = _payload(
            person_id=uuid7(), ledger_epoch=int(payload["ledger_epoch"])
        )
        with pytest.raises(DBAPIError) as epoch_error:
            await _replay(erasure, epoch_conflict)
        assert _sqlstate(epoch_error) == "23505"

        outbox_conflict = _payload(
            person_id=uuid7(),
            ledger_outbox_id=uuid.UUID(str(payload["outbox_id"])),
        )
        with pytest.raises(DBAPIError) as outbox_error:
            await _replay(erasure, outbox_conflict)
        assert _sqlstate(outbox_error) == "23505"

        bad_residual = _payload(person_id=uuid7())
        bad_residual["residual_commitments"] = dict(
            bad_residual["residual_commitments"]
        )
        bad_residual["residual_commitments"][MANDATORY_RESIDUALS[0]] = "f" * 64
        with pytest.raises(DBAPIError) as residual_error:
            await _replay(erasure, bad_residual)
        assert _sqlstate(residual_error) == "22023"
        async with owner.begin() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM privacy.subject_retrieval_blocks "
                        "WHERE person_id=:person"
                    ),
                    {"person": uuid.UUID(str(bad_residual["person_id"]))},
                )
            ).scalar_one() == 0
    finally:
        await erasure.dispose()
        await owner.dispose()


def _run_alembic(
    database_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
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
        timeout=120,
    )


@pytest.mark.skipif(
    not os.getenv(LIFECYCLE_DATABASE_ENV)
    or not os.getenv(LIFECYCLE_ERASURE_DATABASE_ENV),
    reason=(
        "dedicated empty E2 lifecycle owner and erasure PostgreSQL URLs are required"
    ),
)
@pytest.mark.asyncio
async def test_postgresql_e2_clean_roundtrip_and_data_bearing_downgrade_refusal() -> (
    None
):
    database_url = os.environ[LIFECYCLE_DATABASE_ENV]
    engine = create_async_engine(database_url, pool_size=1)
    try:
        async with engine.begin() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one() == "0012_identity_erasure_e2"
            assert (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM "
                        "privacy.subject_retrieval_blocks) + "
                        "(SELECT count(*) FROM "
                        "privacy.identity_person_erasure_residuals)"
                    )
                )
            ).scalar_one() == 0
    finally:
        await engine.dispose()

    downgrade = _run_alembic(database_url, "downgrade", "0011_identity_erasure_e1")
    assert downgrade.returncode == 0
    downgraded = create_async_engine(database_url, pool_size=1)
    try:
        async with downgraded.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT version_num, "
                            "to_regclass('privacy.identity_person_erasure_residuals') "
                            "IS NULL AS residual_absent, "
                            "to_regprocedure('privacy."
                            "replay_identity_person_retrieval_block_v2(jsonb)') "
                            "IS NULL AS replay_absent, "
                            "NOT EXISTS (SELECT 1 FROM pg_proc function "
                            "JOIN pg_namespace namespace "
                            "ON namespace.oid=function.pronamespace "
                            "WHERE namespace.nspname='privacy' AND function.proname "
                            "IN ('identity_person_is_blocked', "
                            "'identity_principal_is_blocked', "
                            "'identity_fact_is_blocked', "
                            "'reject_tombstoned_identity_write', "
                            "'enforce_identity_person_erasure_residual_set')) "
                            "AS helpers_absent, "
                            "NOT EXISTS (SELECT 1 FROM pg_policy "
                            "WHERE polname LIKE '%_e2_%') "
                            "AS policies_absent, "
                            "NOT EXISTS (SELECT 1 FROM pg_trigger "
                            "WHERE tgname LIKE '%_e2_anti_resurrection' "
                            "AND NOT tgisinternal) AS triggers_absent "
                            "FROM alembic_version"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert row == {
                "version_num": "0011_identity_erasure_e1",
                "residual_absent": True,
                "replay_absent": True,
                "helpers_absent": True,
                "policies_absent": True,
                "triggers_absent": True,
            }
    finally:
        await downgraded.dispose()

    upgrade = _run_alembic(database_url, "upgrade", "head")
    assert upgrade.returncode == 0, (
        f"stdout:\n{upgrade.stdout}\n\nstderr:\n{upgrade.stderr}"
    )
    upgraded_erasure = create_async_engine(
        os.environ[LIFECYCLE_ERASURE_DATABASE_ENV], pool_size=1
    )
    payload = _payload(person_id=uuid7())
    try:
        assert await _replay(upgraded_erasure, payload) == uuid.UUID(
            str(payload["block_id"])
        )
    finally:
        await upgraded_erasure.dispose()

    refused = _run_alembic(database_url, "downgrade", "0011_identity_erasure_e1")
    assert refused.returncode != 0
    assert "identity_erasure_e2_downgrade_blocked" in (refused.stdout + refused.stderr)
    final = create_async_engine(database_url, pool_size=1)
    try:
        async with final.begin() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one() == "0013_identity_finalizer_e3"
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM privacy.subject_retrieval_blocks "
                        "WHERE person_id=:person"
                    ),
                    {"person": uuid.UUID(str(payload["person_id"]))},
                )
            ).scalar_one() == 1
    finally:
        await final.dispose()
