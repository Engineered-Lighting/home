"""Read-only, content-free Phase 3 signing-policy inputs.

This module runs inside the exact installed Core image.  It proves that one
current record-only-to-shadow authorization exists and reports build-derived
schema/capability commitments.  It cannot create an authorization, change the
rollout mode, or read semantic People content.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from sqlalchemy import func, select, text

from . import schema
from .config import Settings
from .db import Database
from .phase2 import PHASE2_RULE_VERSION, Phase2GateInspector
from .rollout import RECORD_ONLY_TO_SHADOW, evaluate_rollout_receipt


CONTRACT = "phase3-signing-material-e5ae-v1"
REQUIRED_REVISION = "0006a_worker_lease_arbitration"
IMAGE_ROOT = Path("/app")
CORE_SCHEMA_PATHS = (
    "alembic.ini",
    "alembic/env.py",
    "alembic/script.py.mako",
    "app/schema.py",
)
EXPECTED_CAPABILITY_CONTRACT: Mapping[str, Any] = {
    "contract": "legacy-identity-migration-v1",
    "audience": "operator-bootstrap",
    "person_import": {
        "method": "POST",
        "path": "/v1/people",
        "schema": "PersonCreate.v2",
        "source_digest_field": "legacy_source_sha256",
        "idempotency": "exact-projection-v1",
    },
    "role_import": {
        "method": "POST",
        "path": "/v1/people/legacy-role-labels",
        "schema": "LegacyRoleImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "person_verify": {
        "method": "POST",
        "path": "/v1/people/verify-reviewed",
        "schema": "ReviewedPersonVerify.v1",
    },
    "alias_import": {
        "method": "POST",
        "path": "/v1/people/{person_id}/aliases",
        "schema": "ReviewedAliasImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "recognition_binding_import": {
        "method": "POST",
        "path": "/v1/people/{person_id}/recognition-bindings",
        "schema": "ReviewedRecognitionBindingImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "privacy_directive_import": {
        "method": "POST",
        "path": "/v1/people/{person_id}/privacy-directives",
        "schema": "ReviewedPrivacyDirectiveImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "person_status_import": {
        "method": "POST",
        "path": "/v1/people/{person_id}/status-import",
        "schema": "ReviewedPersonStatusImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "relationship_candidate_import": {
        "method": "POST",
        "path": "/v1/people/legacy-relationship-candidates",
        "schema": "LegacyRelationshipCandidateImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
}
SOURCE_PROJECTION_CONTRACT: Mapping[str, Any] = {
    "contract": "legacy-identity-source-projection-v1",
    "source_schema_version": 1,
    "source_tables": [
        "schema_meta",
        "identities",
        "identity_aliases",
        "enrollments",
        "relationships",
    ],
    "projection_kinds": [
        "person",
        "privacy_directive",
        "person_status",
        "alias",
        "recognition_binding",
        "legacy_role_candidate",
        "legacy_relationship_candidate",
        "explicit_omission",
    ],
    "prohibited_authority": [
        "parent_of_fact",
        "ownership",
        "residence",
        "presence",
    ],
    "idempotency": "exact-projection-v1",
}


class SigningMaterialError(RuntimeError):
    """A content-free signing-material boundary failed closed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def contract_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def core_schema_digest(root: Path) -> str:
    paths = list(CORE_SCHEMA_PATHS)
    versions = root / "alembic" / "versions"
    try:
        paths.extend(
            str(path.relative_to(root)).replace("\\", "/")
            for path in sorted(versions.glob("*.py"))
        )
        manifest = []
        for relative in paths:
            raw = (root / relative).read_bytes()
            if not raw or b"\0" in raw:
                raise SigningMaterialError("Core schema source is invalid")
            manifest.append(
                {"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}
            )
    except OSError as error:
        raise SigningMaterialError("Core schema source is unavailable") from error
    if len(paths) != len(set(paths)) or not any(
        path.startswith("alembic/versions/") for path in paths
    ):
        raise SigningMaterialError("Core schema source is incomplete")
    return contract_digest({"contract": "core-schema-bundle-v1", "files": manifest})


def evaluate_material(
    *,
    settings: Settings,
    revision: str | None,
    readiness: Any,
    receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if (
        settings.role != "api"
        or settings.rollout_mode != "record_only"
        or settings.readiness_migration != REQUIRED_REVISION
        or revision != REQUIRED_REVISION
        or readiness.ready_to_advance is not True
        or readiness.blockers != []
    ):
        raise SigningMaterialError("Phase 3 signing material is not eligible")
    status = evaluate_rollout_receipt(
        receipt,
        transition=RECORD_ONLY_TO_SHADOW,
        policy_version=settings.policy_version,
        policy_digest=settings.policy_digest,
        input_digest=readiness.input_digest,
    )
    if not status.authorized or status.authorization_id is None:
        raise SigningMaterialError("shadow authorization is unavailable")
    return {
        "contract": CONTRACT,
        "schema_revision": REQUIRED_REVISION,
        "shadow_authorization_id": str(status.authorization_id),
        "shadow_rule_version": PHASE2_RULE_VERSION,
        "policy_version": settings.policy_version,
        "policy_digest": settings.policy_digest,
        "source_projection_contract_digest": contract_digest(
            SOURCE_PROJECTION_CONTRACT
        ),
        "core_schema_digest": core_schema_digest(IMAGE_ROOT),
        "core_capability_digest": contract_digest(EXPECTED_CAPABILITY_CONTRACT),
        "authorization_status": "authorized",
        "authoritative": False,
        "read_only": True,
    }


async def execute(settings: Settings) -> dict[str, Any]:
    database = Database(settings.async_database_url())
    try:
        revision = await database.migration_revision()
        async with database.transaction(serializable=True) as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            evaluated_at = (
                await connection.execute(select(func.transaction_timestamp()))
            ).scalar_one()
            readiness = await Phase2GateInspector(
                database,
                rollout_mode=settings.rollout_mode,
                policy_version=settings.policy_version,
                policy_digest=settings.policy_digest,
            ).inspect_evidence_in_transaction(connection, now=evaluated_at)
            rows = (
                (
                    await connection.execute(
                        select(schema.rollout_authorizations).where(
                            schema.rollout_authorizations.c.from_mode == "record_only",
                            schema.rollout_authorizations.c.to_mode == "shadow",
                        )
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) != 1:
            raise SigningMaterialError("shadow authorization is unavailable")
        return evaluate_material(
            settings=settings,
            revision=revision,
            readiness=readiness,
            receipt=rows[0],
        )
    finally:
        await database.close()


def main() -> int:
    if len(sys.argv) != 1:
        print("Phase 3 signing-material probe accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = asyncio.run(execute(Settings()))
    except Exception:
        print("Phase 3 signing-material probe failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
