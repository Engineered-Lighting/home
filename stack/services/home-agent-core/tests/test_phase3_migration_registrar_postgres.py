"""Drive `app.identity_migration_registrar` against the real 0008 kernel.

Step 17 registers the reviewed run through this module, and until now nothing
ran it against a database. `test_identity_migration_registrar_e5ak.py` drives
it with a fake backend, and `test_phase3_identity_migration_kernel_postgres.py`
drives the kernel with hand-built SQL. The seam between them -- the module's
own URL pin, its request contract, its manifest pre-check and its retry
loop, all against the kernel that actually refuses things -- was exercised
nowhere.

This needs a cluster of its own. The module refuses any database but one
literally named `home_agent`, so it cannot use the renamed disposable database
the kernel contracts run in, and `rollout_transition_once` admits exactly one
`record_only -> shadow` authorization per database, so it cannot share the E3
phase's either. The gate gives it a fresh cluster and seeds the one reviewed
shadow predecessor the kernel demands.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import identity_migration_registrar
from app.ids import uuid7

DATABASE_ENV = "TEST_PHASE3_REGISTRAR_MIGRATION_DATABASE_URL"
OWNER_DATABASE_ENV = "TEST_PHASE3_REGISTRAR_OWNER_DATABASE_URL"
# The gate seeds this exact predecessor. The kernel matches it on all four of
# these at once and the caller has no API that can discover any of them.
SHADOW_AUTHORIZATION_ID = uuid.UUID("00000000-0000-7000-8000-000000000801")
SHADOW_RULE_VERSION = "record-only-envelope-worker-gate-v3"
POLICY_VERSION = "home-agent-mvp-v1"
POLICY_DIGEST = "a" * 64


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _signature(label: str) -> str:
    return hashlib.sha512(label.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _manifest(run_id: uuid.UUID, *, label: str = "registrar-seam") -> dict[str, Any]:
    source_item_id = uuid7()
    decision_id = uuid7()
    candidate = _digest(f"{label}:candidate")
    expiry = datetime.now(UTC) + timedelta(minutes=10)
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
            "shadow_rule_version": SHADOW_RULE_VERSION,
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
            "expires_at": expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
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


def _request(run_id: uuid.UUID, manifest: dict[str, Any]) -> bytes:
    return _canonical(
        {
            "contract": identity_migration_registrar.CONTRACT,
            "run_id": str(run_id),
            "manifest_b64": base64.b64encode(_canonical(manifest)).decode("ascii"),
        }
    )


def _owner_engine() -> AsyncEngine:
    return create_async_engine(os.environ[OWNER_DATABASE_ENV], pool_size=1)


@pytest.fixture
def pinned_environment() -> Any:
    """Expose the caller URL the way the entrypoint does, then restore it."""

    previous = os.environ.get("HOME_AGENT_DATABASE_URL")
    os.environ["HOME_AGENT_DATABASE_URL"] = os.environ[DATABASE_ENV]
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HOME_AGENT_DATABASE_URL", None)
        else:
            os.environ["HOME_AGENT_DATABASE_URL"] = previous


@pytest.mark.skipif(
    not os.getenv(DATABASE_ENV) or not os.getenv(OWNER_DATABASE_ENV),
    reason=(
        "TEST_PHASE3_REGISTRAR_MIGRATION_DATABASE_URL and "
        "TEST_PHASE3_REGISTRAR_OWNER_DATABASE_URL are required; they must "
        "address the migration caller and the owner in one cluster whose "
        "database is named home_agent and carries the seeded shadow "
        "predecessor"
    ),
)
@pytest.mark.asyncio
async def test_production_registrar_registers_through_the_real_kernel(
    pinned_environment: None,
) -> None:
    """The module resolves its own URL and the kernel accepts what it sends."""

    # The module refuses any role, host, port or database but the pinned ones.
    # Resolving it here is the point: the ceremony gets this URL from the
    # environment, not from a caller.
    url = identity_migration_registrar.database_url()
    assert url == os.environ[DATABASE_ENV]

    run_id = uuid7()
    manifest = _manifest(run_id)
    request = identity_migration_registrar.parse_request(_request(run_id, manifest))
    assert request.run_id == run_id

    registered = await identity_migration_registrar.execute(url, request)
    assert registered == run_id

    owner = _owner_engine()
    try:
        async with owner.connect() as connection:
            stored = (
                await connection.execute(
                    text(
                        "SELECT shadow_authorization_id, review_signature, "
                        "source_item_count, decision_count "
                        "FROM operations.reviewed_identity_migration_runs "
                        "WHERE run_id = CAST(:run AS uuid)"
                    ),
                    {"run": run_id},
                )
            ).one()
            # The run the finalizer later copies its provenance from, written
            # by the module rather than by hand.
            assert stored[0] == SHADOW_AUTHORIZATION_ID
            assert stored[1] == manifest["run"]["review_signature"]
            assert stored[2] == 1
            assert stored[3] == 1

            items, decisions = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM operations."
                        "reviewed_identity_migration_source_items "
                        "WHERE run_id = CAST(:run AS uuid)), "
                        "(SELECT count(*) FROM operations."
                        "reviewed_identity_migration_decisions "
                        "WHERE run_id = CAST(:run AS uuid))"
                    ),
                    {"run": run_id},
                )
            ).one()
            assert items == 1
            assert decisions == 1
    finally:
        await owner.dispose()

    # The grant is one-shot -- one reviewed run per authorization, with no
    # DELETE anywhere to reclaim it. That property is what the whole ceremony
    # is arranged around, and it has never been observed through the module
    # that spends it. Asserting it here rather than in a second test keeps the
    # two from depending on execution order.
    second_id = uuid7()
    second = identity_migration_registrar.parse_request(
        _request(second_id, _manifest(second_id, label="registrar-second"))
    )
    with pytest.raises(identity_migration_registrar.MigrationRegistrarError):
        await identity_migration_registrar.execute(url, second)
