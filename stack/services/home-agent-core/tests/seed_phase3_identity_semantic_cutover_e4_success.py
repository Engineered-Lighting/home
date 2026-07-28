"""Seed one disposable, synthetic E4 admitted-success fixture.

This helper is executed only by the labeled GitHub-hosted PostgreSQL gate. It
first uses the existing E3 fixture/finalizer path, then appends the post-E3
writer-freeze, privacy, candidate, and one-time E4 admission evidence. The
private cutover document is generated from PostgreSQL's own ``jsonb::text``
representation and is written to a dedicated ephemeral bind mount; it is never
printed.
"""

from __future__ import annotations

import ast
import asyncio
import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.ids import uuid7
from app.ledger import LedgerHead, ZERO_HASH
from app.worker import DurableWorker
from tests.test_phase3_identity_finalizer_e3_runtime_postgres import (
    _finalize,
    _seed_fixture,
)


OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL"
FINALIZER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CUTOVER_E4_FINALIZER_DATABASE_URL"
)
LEDGER_WORKER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CUTOVER_E4_LEDGER_WORKER_DATABASE_URL"
)
OUTPUT_DIRECTORY = Path("/run/e4-fixture")
DOCUMENT_FILE = "document.b64"
ADMISSION_FILE = "admission_id"
ROOT = Path(__file__).resolve().parents[1]
PRIVACY_CATEGORIES = (
    "ingress",
    "retrieval",
    "prompt",
    "initiative",
    "export",
    "edge_block",
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _signature(label: str) -> str:
    return hashlib.sha512(label.encode("utf-8")).hexdigest()


def _async_url(value: str):
    return make_url(value).set(drivername="postgresql+psycopg")


def _migration_literal(name: str) -> str:
    source = (
        ROOT / "alembic/versions/0014_identity_semantic_cutover_e4.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            continue
        if not isinstance(statement.value, ast.Constant) or not isinstance(
            statement.value.value, str
        ):
            break
        return statement.value.value
    raise RuntimeError(f"0014 {name} is not a string literal")


def _validate_output_directory() -> None:
    if OUTPUT_DIRECTORY.is_symlink() or not OUTPUT_DIRECTORY.is_dir():
        raise RuntimeError("E4 fixture output directory is not a real directory")
    if OUTPUT_DIRECTORY.resolve(strict=True) != OUTPUT_DIRECTORY:
        raise RuntimeError("E4 fixture output directory is not canonical")
    if any(OUTPUT_DIRECTORY.iterdir()):
        raise RuntimeError("E4 fixture output directory is not empty")


def _write_secret_file(name: str, value: str) -> None:
    path = OUTPUT_DIRECTORY / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, stat.S_IRUSR)
    try:
        payload = (value + "\n").encode("ascii")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


async def _canonical_document(connection, values: dict[str, str]) -> bytes:
    if len(values) != 27 or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in values.items()
    ):
        raise RuntimeError("synthetic E4 document shape is invalid")
    raw_json = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    document = (
        await connection.execute(
            text(
                "SELECT pg_catalog.convert_to("
                "(CAST(:document AS jsonb))::text,'UTF8')"
            ),
            {"document": raw_json},
        )
    ).scalar_one()
    if not isinstance(document, bytes) or not (2 <= len(document) <= 1048576):
        raise RuntimeError("canonical E4 document has an invalid size")
    return document


async def _seed() -> tuple[bytes, uuid.UUID]:
    owner_url = os.environ.get(OWNER_DATABASE_ENV)
    finalizer_url = os.environ.get(FINALIZER_DATABASE_ENV)
    ledger_worker_url = os.environ.get(LEDGER_WORKER_DATABASE_ENV)
    if not owner_url or not finalizer_url or not ledger_worker_url:
        raise RuntimeError("synthetic E4 fixture database URLs are missing")

    owner_engine = create_async_engine(
        _async_url(owner_url),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    finalizer_engine = create_async_engine(
        _async_url(finalizer_url),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    ledger_worker_engine = create_async_engine(
        _async_url(ledger_worker_url),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        # A fresh database has no ledger singleton until the durable worker
        # reconciles its independently governed head. Exercise that production
        # path with the worker credential instead of owner-seeding the table.
        async with ledger_worker_engine.begin() as connection:
            await DurableWorker._advance_ledger_state(
                connection,
                LedgerHead(epoch=0, head_hash=ZERO_HASH),
                datetime.now(UTC),
            )
        async with owner_engine.begin() as connection:
            fixture = await _seed_fixture(
                connection,
                label="github-hosted-e4-synthetic-success",
                parent_authority=True,
            )
        finalized_id = await _finalize(finalizer_engine, fixture)
        if finalized_id != fixture.run_id:
            raise RuntimeError("synthetic E3 finalization identity mismatch")

        evidence_id = uuid7()
        source_installation_id = uuid7()
        freeze_id = uuid7()
        candidate_cutover_id = uuid7()
        admission_id = uuid7()
        promotion_id = uuid7()
        privacy_check_ids = {
            category: uuid7() for category in PRIVACY_CATEGORIES
        }

        writer_evidence_commitment = _digest(
            "github-hosted-e4:writer-evidence"
        )
        freeze_commitment = _digest("github-hosted-e4:freeze")
        privacy_set_commitment = _digest("github-hosted-e4:privacy-set")
        promotion_commitment = _digest("github-hosted-e4:promotion")
        signing_key_fingerprint = _digest("github-hosted-e4:signing-key")
        cutover_signature = _signature("github-hosted-e4:cutover-signature")
        verifier_bundle_digest = _migration_literal("VERIFIER_BUNDLE_DIGEST")

        async with owner_engine.begin() as connection:
            evidence = (
                await connection.execute(
                    text(
                        "SELECT migration_run.run_id, "
                        "migration_run.logical_source_manifest_commitment, "
                        "migration_run.projection_manifest_commitment, "
                        "migration_run.commitment_key_epoch, "
                        "migration_run.release_manifest_digest, "
                        "migration_run.core_schema_digest, "
                        "migration_run.core_capability_digest, "
                        "migration_run.policy_digest, "
                        "finalization.finalization_id, "
                        "finalization.finalized_at, "
                        "finalizer_admission.consumed_at, "
                        "ledger.recorded_epoch, "
                        "ledger.recorded_head_hash, "
                        "ledger.updated_at, "
                        "pg_catalog.transaction_timestamp() AS fixture_time "
                        "FROM operations.reviewed_identity_migration_runs "
                        "AS migration_run "
                        "JOIN operations.reviewed_identity_migration_"
                        "finalizations AS finalization "
                        "ON finalization.run_id=migration_run.run_id "
                        "JOIN operations.reviewed_identity_finalizer_"
                        "admissions AS finalizer_admission "
                        "ON finalizer_admission.run_id=migration_run.run_id "
                        "CROSS JOIN operations.erasure_ledger_state AS ledger "
                        "WHERE migration_run.run_id=:run_id "
                        "AND ledger.state_key='current'"
                    ),
                    {"run_id": fixture.run_id},
                )
            ).mappings().one()
            if (
                evidence["finalization_id"] != fixture.run_id
                or evidence["consumed_at"] != evidence["finalized_at"]
                or evidence["recorded_epoch"] != 0
                or evidence["recorded_head_hash"] != ZERO_HASH
                or evidence["updated_at"] > evidence["fixture_time"]
            ):
                raise RuntimeError("synthetic E3/ledger prerequisite mismatch")
            fixture_time = evidence["fixture_time"]

            blocked_or_impacted = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM operations."
                        "reviewed_identity_migration_erasure_impacts "
                        "WHERE run_id=:run_id), "
                        "(SELECT count(*) FROM operations."
                        "reviewed_identity_migration_projection_subjects "
                        "AS subject "
                        "JOIN operations."
                        "reviewed_identity_migration_projection_lineage "
                        "AS lineage USING (lineage_id) "
                        "WHERE lineage.run_id=:run_id "
                        "AND privacy.identity_person_is_blocked("
                        "subject.person_id))"
                    ),
                    {"run_id": fixture.run_id},
                )
            ).one()
            if tuple(blocked_or_impacted) != (0, 0):
                raise RuntimeError(
                    "synthetic E3 run is erased or retrieval-blocked"
                )

            await connection.execute(
                text(
                    "INSERT INTO operations.legacy_identity_writer_evidence ("
                    "evidence_id,run_id,source_installation_id,"
                    "semantic_generation,source_projection_commitment,"
                    "evidence_strength,integrity_result,checkpoint_result,"
                    "journal_result,legacy_context_cutoff_status,"
                    "release_manifest_digest,freeze_kernel_build_digest,"
                    "evidence_commitment,signature_algorithm,"
                    "signing_key_fingerprint,evidence_signature,observed_at"
                    ") VALUES ("
                    ":evidence_id,:run_id,:source_installation_id,1,"
                    ":projection_manifest,'operator_attested','passed',"
                    "'complete','clean','operator_attested_cutoff',"
                    ":release_digest,:freeze_kernel_digest,"
                    ":evidence_commitment,'ed25519',:signing_key,"
                    ":evidence_signature,:fixture_time)"
                ),
                {
                    "evidence_id": evidence_id,
                    "run_id": fixture.run_id,
                    "source_installation_id": source_installation_id,
                    "projection_manifest": evidence[
                        "projection_manifest_commitment"
                    ],
                    "release_digest": evidence["release_manifest_digest"],
                    "freeze_kernel_digest": _digest(
                        "github-hosted-e4:freeze-kernel"
                    ),
                    "evidence_commitment": writer_evidence_commitment,
                    "signing_key": signing_key_fingerprint,
                    "evidence_signature": _signature(
                        "github-hosted-e4:writer-evidence-signature"
                    ),
                    "fixture_time": fixture_time,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO operations."
                    "enforced_legacy_identity_writer_freezes ("
                    "freeze_id,run_id,writer_evidence_id,contract_version,"
                    "enforcement_status,write_probe_result,"
                    "semantic_write_status,legacy_context_cutoff_status,"
                    "recognition_mode,source_installation_id,"
                    "semantic_generation,source_projection_commitment,"
                    "e3_source_manifest_commitment,"
                    "e3_projection_manifest_commitment,"
                    "e3_commitment_key_epoch,writer_evidence_commitment,"
                    "trigger_set_commitment,blocked_probe_commitment,"
                    "release_manifest_digest,freeze_kernel_build_digest,"
                    "policy_digest,freeze_commitment,signature_algorithm,"
                    "signing_key_fingerprint,freeze_signature,enforced_at,"
                    "verified_at"
                    ") VALUES ("
                    ":freeze_id,:run_id,:evidence_id,"
                    "'enforced-legacy-identity-writer-freeze-v1',"
                    "'enforced_offline','blocked','frozen',"
                    "'enforced_cutoff','nonauthoritative_only',"
                    ":source_installation_id,1,:projection_manifest,"
                    ":source_manifest,:projection_manifest,:key_epoch,"
                    ":writer_evidence_commitment,:trigger_set_commitment,"
                    ":blocked_probe_commitment,:release_digest,"
                    ":freeze_kernel_digest,:policy_digest,"
                    ":freeze_commitment,'ed25519',:signing_key,"
                    ":freeze_signature,:fixture_time,:fixture_time)"
                ),
                {
                    "freeze_id": freeze_id,
                    "run_id": fixture.run_id,
                    "evidence_id": evidence_id,
                    "source_installation_id": source_installation_id,
                    "projection_manifest": evidence[
                        "projection_manifest_commitment"
                    ],
                    "source_manifest": evidence[
                        "logical_source_manifest_commitment"
                    ],
                    "key_epoch": evidence["commitment_key_epoch"],
                    "writer_evidence_commitment": writer_evidence_commitment,
                    "trigger_set_commitment": _digest(
                        "github-hosted-e4:trigger-set"
                    ),
                    "blocked_probe_commitment": _digest(
                        "github-hosted-e4:blocked-probe"
                    ),
                    "release_digest": evidence["release_manifest_digest"],
                    "freeze_kernel_digest": _digest(
                        "github-hosted-e4:freeze-kernel"
                    ),
                    "policy_digest": evidence["policy_digest"],
                    "freeze_commitment": freeze_commitment,
                    "signing_key": signing_key_fingerprint,
                    "freeze_signature": _signature(
                        "github-hosted-e4:freeze-signature"
                    ),
                    "fixture_time": fixture_time,
                },
            )

            await connection.execute(
                text(
                    "INSERT INTO operations.privacy_cutover_check_receipts ("
                    "check_id,run_id,finalization_id,check_category,"
                    "check_result,residual_code,check_commitment,"
                    "receipt_commitment,policy_digest,checked_at"
                    ") VALUES ("
                    ":check_id,:run_id,:finalization_id,:category,"
                    "'passed','none',:check_commitment,"
                    ":receipt_commitment,:policy_digest,:fixture_time)"
                ),
                [
                    {
                        "check_id": privacy_check_ids[category],
                        "run_id": fixture.run_id,
                        "finalization_id": fixture.run_id,
                        "category": category,
                        "check_commitment": _digest(
                            f"github-hosted-e4:{category}:check"
                        ),
                        "receipt_commitment": _digest(
                            f"github-hosted-e4:{category}:receipt"
                        ),
                        "policy_digest": evidence["policy_digest"],
                        "fixture_time": fixture_time,
                    }
                    for category in PRIVACY_CATEGORIES
                ],
            )
            await connection.execute(
                text(
                    "INSERT INTO operations.semantic_authority_cutovers ("
                    "cutover_id,run_id,finalization_id,writer_evidence_id,"
                    "contract_version,authority_status,authoritative,"
                    "ingress_check_id,ingress_check_category,"
                    "retrieval_check_id,retrieval_check_category,"
                    "prompt_check_id,prompt_check_category,"
                    "initiative_check_id,initiative_check_category,"
                    "export_check_id,export_check_category,"
                    "edge_block_check_id,edge_block_check_category,"
                    "required_privacy_check_result,"
                    "required_privacy_residual_code,"
                    "privacy_check_set_commitment,cutover_commitment,"
                    "policy_digest,signature_algorithm,"
                    "signing_key_fingerprint,cutover_signature,attested_at"
                    ") VALUES ("
                    ":cutover_id,:run_id,:finalization_id,:evidence_id,"
                    "'semantic-authority-cutover-candidate-v1',"
                    "'candidate_unenforced',false,"
                    ":ingress,'ingress',:retrieval,'retrieval',"
                    ":prompt,'prompt',:initiative,'initiative',"
                    ":export,'export',:edge_block,'edge_block',"
                    "'passed','none',:privacy_set,:cutover_commitment,"
                    ":policy_digest,'ed25519',:signing_key,"
                    ":cutover_signature,:fixture_time)"
                ),
                {
                    "cutover_id": candidate_cutover_id,
                    "run_id": fixture.run_id,
                    "finalization_id": fixture.run_id,
                    "evidence_id": evidence_id,
                    **privacy_check_ids,
                    "privacy_set": privacy_set_commitment,
                    "cutover_commitment": _digest(
                        "github-hosted-e4:candidate-cutover"
                    ),
                    "policy_digest": evidence["policy_digest"],
                    "signing_key": signing_key_fingerprint,
                    "cutover_signature": _signature(
                        "github-hosted-e4:candidate-cutover-signature"
                    ),
                    "fixture_time": fixture_time,
                },
            )

            document_values = {
                "contract_version": (
                    "reviewed-identity-semantic-cutover-v1"
                ),
                "admission_id": str(admission_id),
                "promotion_id": str(promotion_id),
                "run_id": str(fixture.run_id),
                "finalization_id": str(fixture.run_id),
                "candidate_cutover_id": str(candidate_cutover_id),
                "writer_freeze_id": str(freeze_id),
                "authority_scope": "identity_semantics",
                "promotion_commitment": promotion_commitment,
                "privacy_check_set_commitment": privacy_set_commitment,
                "freeze_commitment": freeze_commitment,
                "source_installation_id": str(source_installation_id),
                "semantic_generation": "1",
                "e3_source_manifest_commitment": evidence[
                    "logical_source_manifest_commitment"
                ],
                "e3_projection_manifest_commitment": evidence[
                    "projection_manifest_commitment"
                ],
                "e3_commitment_key_epoch": str(
                    evidence["commitment_key_epoch"]
                ),
                "erasure_ledger_epoch": str(evidence["recorded_epoch"]),
                "erasure_ledger_head_hash": evidence[
                    "recorded_head_hash"
                ],
                "erasure_ledger_verification": "head_matched",
                "release_manifest_digest": evidence[
                    "release_manifest_digest"
                ],
                "core_schema_digest": evidence["core_schema_digest"],
                "core_capability_digest": evidence[
                    "core_capability_digest"
                ],
                "policy_digest": evidence["policy_digest"],
                "signing_key_fingerprint": signing_key_fingerprint,
                "verifier_bundle_digest": verifier_bundle_digest,
                "signature_algorithm": "ed25519",
                "cutover_signature": cutover_signature,
            }
            document = await _canonical_document(
                connection, document_values
            )
            document_sha256 = hashlib.sha256(document).hexdigest()

            await connection.execute(
                text(
                    "INSERT INTO operations."
                    "reviewed_identity_cutover_admissions ("
                    "admission_id,promotion_id,run_id,finalization_id,"
                    "candidate_cutover_id,writer_freeze_id,"
                    "contract_version,verification_status,"
                    "document_sha256,document_octets,promotion_commitment,"
                    "privacy_check_set_commitment,freeze_commitment,"
                    "source_installation_id,semantic_generation,"
                    "e3_source_manifest_commitment,"
                    "e3_projection_manifest_commitment,"
                    "e3_commitment_key_epoch,erasure_ledger_epoch,"
                    "erasure_ledger_head_hash,erasure_ledger_verification,"
                    "release_manifest_digest,core_schema_digest,"
                    "core_capability_digest,policy_digest,"
                    "signing_key_fingerprint,verifier_bundle_digest,"
                    "admitted_at,expires_at"
                    ") VALUES ("
                    ":admission_id,:promotion_id,:run_id,:finalization_id,"
                    ":candidate_cutover_id,:freeze_id,"
                    "'reviewed-identity-cutover-admission-v1',"
                    "'offline_verified',:document_sha256,:document_octets,"
                    ":promotion_commitment,:privacy_set,"
                    ":freeze_commitment,:source_installation_id,1,"
                    ":source_manifest,:projection_manifest,:key_epoch,"
                    ":ledger_epoch,:ledger_head,'head_matched',"
                    ":release_digest,:schema_digest,:capability_digest,"
                    ":policy_digest,:signing_key,:verifier_bundle_digest,"
                    ":fixture_time,:expires_at)"
                ),
                {
                    "admission_id": admission_id,
                    "promotion_id": promotion_id,
                    "run_id": fixture.run_id,
                    "finalization_id": fixture.run_id,
                    "candidate_cutover_id": candidate_cutover_id,
                    "freeze_id": freeze_id,
                    "document_sha256": document_sha256,
                    "document_octets": len(document),
                    "promotion_commitment": promotion_commitment,
                    "privacy_set": privacy_set_commitment,
                    "freeze_commitment": freeze_commitment,
                    "source_installation_id": source_installation_id,
                    "source_manifest": evidence[
                        "logical_source_manifest_commitment"
                    ],
                    "projection_manifest": evidence[
                        "projection_manifest_commitment"
                    ],
                    "key_epoch": evidence["commitment_key_epoch"],
                    "ledger_epoch": evidence["recorded_epoch"],
                    "ledger_head": evidence["recorded_head_hash"],
                    "release_digest": evidence["release_manifest_digest"],
                    "schema_digest": evidence["core_schema_digest"],
                    "capability_digest": evidence[
                        "core_capability_digest"
                    ],
                    "policy_digest": evidence["policy_digest"],
                    "signing_key": signing_key_fingerprint,
                    "verifier_bundle_digest": verifier_bundle_digest,
                    "fixture_time": fixture_time,
                    "expires_at": fixture_time + timedelta(minutes=10),
                },
            )

            seeded_counts = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM operations."
                        "enforced_legacy_identity_writer_freezes "
                        "WHERE run_id=:run_id), "
                        "(SELECT count(*) FROM operations."
                        "privacy_cutover_check_receipts "
                        "WHERE run_id=:run_id), "
                        "(SELECT count(*) FROM operations."
                        "semantic_authority_cutovers "
                        "WHERE run_id=:run_id), "
                        "(SELECT count(*) FROM operations."
                        "reviewed_identity_cutover_admissions "
                        "WHERE run_id=:run_id), "
                        "(SELECT count(*) FROM operations."
                        "semantic_authority_promotions)"
                    ),
                    {"run_id": fixture.run_id},
                )
            ).one()
            if tuple(seeded_counts) != (1, 6, 1, 1, 0):
                raise RuntimeError("synthetic E4 evidence cardinality mismatch")

        return document, admission_id
    finally:
        await ledger_worker_engine.dispose()
        await finalizer_engine.dispose()
        await owner_engine.dispose()


def main() -> int:
    _validate_output_directory()
    document, admission_id = asyncio.run(_seed())
    _write_secret_file(
        DOCUMENT_FILE, base64.b64encode(document).decode("ascii")
    )
    _write_secret_file(ADMISSION_FILE, str(admission_id))
    directory_descriptor = os.open(OUTPUT_DIRECTORY, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
