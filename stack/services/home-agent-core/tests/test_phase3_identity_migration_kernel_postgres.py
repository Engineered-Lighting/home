from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ids import uuid7


DATABASE_ENV = "TEST_PHASE3_IDENTITY_MIGRATION_DATABASE_URL"
OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_MIGRATION_OWNER_DATABASE_URL"
CALLER_ROLE = "home_agent_identity_migration"
# The disposable database must seed this exact reviewed shadow predecessor
# before applying 0008.  The caller intentionally has no API that can discover
# predecessor identifiers or policy data.
SHADOW_AUTHORIZATION_ID = uuid.UUID("00000000-0000-7000-8000-000000000801")
POLICY_VERSION = "home-agent-mvp-v1"
POLICY_DIGEST = "a" * 64


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _signature(label: str) -> str:
    return hashlib.sha512(label.encode("utf-8")).hexdigest()


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(error.value.orig, "sqlstate", None)


def _manifest(
    *, expires_at: datetime | None = None, label: str = "valid"
) -> dict[str, Any]:
    run_id = uuid7()
    source_item_id = uuid7()
    decision_id = uuid7()
    candidate = _digest(f"{label}:candidate")
    expiry = expires_at or datetime.now(UTC) + timedelta(minutes=5)
    return {
        "run": {
            "run_id": str(run_id),
            "operator_request_id": str(uuid7()),
            "contract_version": "reviewed-identity-migration-run-v1",
            "source_schema_version": 1,
            "source_projection_contract_version": (
                "legacy-identity-source-projection-v1"
            ),
            "importer_version": "legacy-identity-importer-v1",
            "canonicalization_version": "identity-canonicalization-v1",
            "projection_version": "semantic-people-projection-v1",
            "shadow_rule_version": "record-only-envelope-worker-gate-v3",
            "commitment_algorithm": "hmac-sha256-v1",
            "commitment_key_fingerprint": _digest(f"{label}:commitment-key"),
            "commitment_key_epoch": 1,
            "source_item_count": 1,
            "decision_count": 1,
            "logical_source_manifest_commitment": _digest(f"{label}:source"),
            "projection_manifest_commitment": _digest(f"{label}:projection"),
            "source_projection_contract_digest": _digest(f"{label}:contract"),
            "review_receipt_commitment": _digest(f"{label}:review"),
            "policy_version": POLICY_VERSION,
            "policy_digest": POLICY_DIGEST,
            "shadow_authorization_id": str(SHADOW_AUTHORIZATION_ID),
            "release_manifest_digest": _digest(f"{label}:release"),
            "migration_tool_bundle_digest": _digest(f"{label}:tool"),
            "core_oci_manifest_digest": _digest(f"{label}:oci"),
            "core_schema_digest": _digest(f"{label}:schema"),
            "core_capability_digest": _digest(f"{label}:capability"),
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": _digest(f"{label}:review-key"),
            "review_signature": _signature(f"{label}:review-signature"),
            "expires_at": expiry.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        },
        "source_items": [
            {
                "source_item_id": str(source_item_id),
                "ordinal": 0,
                "source_table_kind": "identity_aliases",
                "row_key_commitment": _digest(f"{label}:row-key"),
                "allowed_projection_commitment": candidate,
            }
        ],
        "decisions": [
            {
                "decision_id": str(decision_id),
                "source_item_id": str(source_item_id),
                "ordinal": 0,
                "decision_kind": "alias",
                "disposition": "apply",
                "candidate_commitment": candidate,
                "canonical_apply_decision_id": None,
                "canonical_apply_disposition": None,
                "decision_commitment": _digest(f"{label}:decision"),
            }
        ],
    }


def _engine(*, isolation_level: str) -> AsyncEngine:
    return create_async_engine(
        os.environ[DATABASE_ENV],
        isolation_level=isolation_level,
        pool_size=1,
        max_overflow=0,
    )


def _owner_engine() -> AsyncEngine:
    return create_async_engine(
        os.environ[OWNER_DATABASE_ENV],
        isolation_level="SERIALIZABLE",
        pool_size=1,
        max_overflow=0,
    )


async def _register(engine: AsyncEngine, manifest: dict[str, Any]) -> uuid.UUID:
    async with engine.begin() as connection:
        value = (
            await connection.execute(
                text(
                    "SELECT operations.register_reviewed_identity_migration("
                    "CAST(:manifest AS jsonb))"
                ),
                {"manifest": json.dumps(manifest, separators=(",", ":"))},
            )
        ).scalar_one()
    return value


async def _reject_registration(
    engine: AsyncEngine, manifest: dict[str, Any], sqlstate: str
) -> None:
    with pytest.raises(DBAPIError) as error:
        await _register(engine, manifest)
    assert _sqlstate(error) == sqlstate


@pytest.mark.skipif(
    not os.getenv(DATABASE_ENV) or not os.getenv(OWNER_DATABASE_ENV),
    reason=(
        "TEST_PHASE3_IDENTITY_MIGRATION_DATABASE_URL and "
        "TEST_PHASE3_IDENTITY_MIGRATION_OWNER_DATABASE_URL are required; "
        "they must address the exact caller and owner in one disposable "
        "0008 database with the documented shadow predecessor"
    ),
)
@pytest.mark.asyncio
async def test_manifest_kernel_postgresql_boundary_and_replay() -> None:
    read_committed = _engine(isolation_level="READ COMMITTED")
    try:
        with pytest.raises(DBAPIError) as wrong_isolation:
            async with read_committed.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT operations."
                        "reviewed_identity_migration_capabilities()"
                    )
                )
        assert _sqlstate(wrong_isolation) == "25001"
    finally:
        await read_committed.dispose()

    engine = _engine(isolation_level="SERIALIZABLE")
    try:
        async with engine.begin() as connection:
            assert (
                await connection.execute(text("SELECT session_user"))
            ).scalar_one() == (CALLER_ROLE)
            capabilities = (
                await connection.execute(
                    text(
                        "SELECT operations."
                        "reviewed_identity_migration_capabilities()"
                    )
                )
            ).scalar_one()
            assert capabilities == {
                "contract_version": "reviewed-identity-migration-kernel-v1",
                "manifest_registration_enabled": True,
                "finalization_enabled": False,
                "finalization_block_reason": (
                    "external_payload_verifier_not_implemented"
                ),
                "direct_table_access": False,
                "required_isolation": "serializable",
                "manifest_canonical_bytes_max": 4_194_304,
                "run_header_canonical_bytes_max": 32_768,
                "source_items_max": 10_000,
                "decisions_max": 10_000,
                "activation_window_seconds": 900,
            }
            assert (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.to_regprocedure("
                        "'operations.finalize_reviewed_identity_migration(jsonb)')"
                    )
                )
            ).scalar_one_or_none() is None

        for table in (
            "operations.reviewed_identity_migration_runs",
            "operations.legacy_identity_writer_evidence",
            "identity.people",
            "privacy.auto_expiry_schedules",
            "knowledge.fact_versions",
            "operations.outbox",
        ):
            with pytest.raises(DBAPIError) as direct_access:
                async with engine.begin() as connection:
                    await connection.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            assert _sqlstate(direct_access) == "42501"

        sparse = _manifest(label="sparse")
        sparse["source_items"][0]["ordinal"] = 1
        await _reject_registration(engine, sparse, "22023")

        oversized = _manifest(label="oversized")
        oversized["padding"] = "x" * 4_194_304
        await _reject_registration(engine, oversized, "22023")

        noncanonical_expiry = _manifest(label="noncanonical-expiry")
        noncanonical_expiry["run"]["expires_at"] = (
            noncanonical_expiry["run"]["expires_at"].removesuffix("Z") + "+00:00"
        )
        await _reject_registration(engine, noncanonical_expiry, "22023")

        duplicate = _manifest(label="duplicate")
        duplicate_decision = copy.deepcopy(duplicate["decisions"][0])
        duplicate_decision["decision_id"] = str(uuid7())
        duplicate_decision["ordinal"] = 1
        duplicate_decision["candidate_commitment"] = _digest("duplicate:other")
        duplicate_decision["decision_commitment"] = _digest("duplicate:decision:2")
        duplicate["decisions"].append(duplicate_decision)
        duplicate["run"]["decision_count"] = 2
        await _reject_registration(engine, duplicate, "22023")

        omission = _manifest(label="omission")
        omission_decision = {
            "decision_id": str(uuid7()),
            "source_item_id": omission["source_items"][0]["source_item_id"],
            "ordinal": 1,
            "decision_kind": "explicit_omission",
            "disposition": "out_of_scope_by_rule",
            "candidate_commitment": None,
            "canonical_apply_decision_id": None,
            "canonical_apply_disposition": None,
            "decision_commitment": _digest("omission:decision:2"),
        }
        omission["decisions"].append(omission_decision)
        omission["run"]["decision_count"] = 2
        await _reject_registration(engine, omission, "22023")

        expires_at = datetime.now(UTC) + timedelta(seconds=5)
        valid = _manifest(expires_at=expires_at)
        valid["source_items"][0]["source_table_kind"] = "identities"
        valid["decisions"][0]["decision_kind"] = "privacy_directive"
        second_privacy = copy.deepcopy(valid["decisions"][0])
        second_privacy["decision_id"] = str(uuid7())
        second_privacy["ordinal"] = 1
        second_privacy["candidate_commitment"] = _digest("valid:privacy:second")
        second_privacy["decision_commitment"] = _digest("valid:privacy:second:decision")
        valid["decisions"].append(second_privacy)
        valid["run"]["decision_count"] = 2
        run_id = await _register(engine, valid)
        assert str(run_id) == valid["run"]["run_id"]
        assert await _register(engine, valid) == run_id

        changed = copy.deepcopy(valid)
        changed["run"]["review_signature"] = _signature("changed-signature")
        await _reject_registration(engine, changed, "23505")

        await asyncio.sleep(5.2)
        assert await _register(engine, valid) == run_id

        collision = _manifest(label="shadow-collision")
        await _reject_registration(engine, collision, "23505")

        owner_engine = _owner_engine()
        try:
            async with owner_engine.begin() as connection:
                assert (
                    await connection.execute(text("SELECT session_user"))
                ).scalar_one() == "home_agent_owner"
                principal_id = (
                    await connection.execute(
                        text(
                            "SELECT principal_id FROM identity.principals "
                            "ORDER BY created_at LIMIT 1"
                        )
                    )
                ).scalar_one_or_none()
                if principal_id is None:
                    person_id = uuid7()
                    principal_id = uuid7()
                    await connection.execute(
                        text(
                            "INSERT INTO identity.people ("
                            "person_id, display_name, status, privacy_scope"
                            ") VALUES ("
                            ":person_id, 'Phase 3 erasure fixture', "
                            "'active', 'private')"
                        ),
                        {"person_id": person_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO identity.principals ("
                            "principal_id, person_id, kind, display_label, status"
                            ") VALUES ("
                            ":principal_id, :person_id, 'service', "
                            "'Phase 3 erasure fixture', 'active')"
                        ),
                        {"principal_id": principal_id, "person_id": person_id},
                    )
                erasure_request_id = uuid7()
                await connection.execute(
                    text(
                        "INSERT INTO privacy.erasure_requests ("
                        "erasure_request_id, principal_id, scope, state, "
                        "policy_digest) VALUES ("
                        ":erasure_request_id, :principal_id, "
                        "CAST(:scope AS jsonb), 'running', :policy_digest)"
                    ),
                    {
                        "erasure_request_id": erasure_request_id,
                        "principal_id": principal_id,
                        "scope": json.dumps(
                            {
                                "kind": "reviewed_identity_migration_run",
                                "run_id": str(run_id),
                            },
                            separators=(",", ":"),
                        ),
                        "policy_digest": POLICY_DIGEST,
                    },
                )
            owner_connection = await owner_engine.connect()
            owner_transaction = await owner_connection.begin()
            try:
                # The impact points at an erasure *operation*, which is what
                # names the request; `exact_source` requires the request arm to
                # carry the request id and leave the auto-expiry arm null.
                operation_id = uuid7()
                await owner_connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_operations ("
                        "operation_id, source_kind, erasure_request_id, "
                        "auto_expiry_schedule_id, operation_commitment"
                        ") VALUES ("
                        ":operation_id, 'erasure_request', "
                        ":erasure_request_id, NULL, :operation_commitment)"
                    ),
                    {
                        "operation_id": operation_id,
                        "erasure_request_id": erasure_request_id,
                        "operation_commitment": _digest(f"operation:{run_id}"),
                    },
                )
                await owner_connection.execute(
                    text(
                        "INSERT INTO operations."
                        "reviewed_identity_migration_erasure_impacts ("
                        "impact_id, run_id, operation_id, impact_code, "
                        "readiness_suspension, removed_leaf_commitment_count, "
                        "unlinked_projection_count, impact_commitment) VALUES ("
                        ":impact_id, :run_id, :operation_id, "
                        "'linkage_unavailable', 'required', 0, 1, "
                        ":impact_commitment)"
                    ),
                    {
                        "impact_id": uuid7(),
                        "run_id": run_id,
                        "operation_id": operation_id,
                        "impact_commitment": _digest(f"erasure:{run_id}"),
                    },
                )
                waiting_replay = asyncio.create_task(_register(engine, valid))
                await asyncio.sleep(0.2)
                await owner_transaction.commit()
                with pytest.raises(DBAPIError) as stale_waiter:
                    await waiting_replay
                assert _sqlstate(stale_waiter) == "40001"
            finally:
                if owner_transaction.is_active:
                    await owner_transaction.rollback()
                await owner_connection.close()
        finally:
            await owner_engine.dispose()

        with pytest.raises(DBAPIError) as erased_replay:
            await _register(engine, valid)
        assert _sqlstate(erased_replay) == "55000"
        assert "identity_migration_erasure_affected" in str(erased_replay.value.orig)
    finally:
        await engine.dispose()
