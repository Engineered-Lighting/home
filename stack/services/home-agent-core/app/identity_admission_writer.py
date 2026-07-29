from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


MAX_DOCUMENT_BYTES = 4_194_304
MAX_REQUEST_BYTES = 5_593_088
MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
PASSWORD = re.compile(r"^[0-9a-f]{64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX128 = re.compile(r"^[0-9a-f]{128}$")
FINALIZER_VERIFIER_DIGEST = (
    "be7c581a949f1b5cc3e51748066d4bae812a7b44bee431cd0f0d9c9936f2c266"
)
CUTOVER_VERIFIER_DIGEST = (
    "eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0"
)

FINALIZER_KEYS = frozenset(
    {
        "apply_decision_count",
        "authoritative",
        "auto_expiry_effect_set_commitment",
        "auto_expiry_effects",
        "contract_version",
        "decision_count",
        "decision_manifest_commitment",
        "finalization_attestation",
        "finalization_commitment",
        "finalization_id",
        "finalization_signing",
        "lineage_set_commitment",
        "privacy_closure_set_commitment",
        "privacy_closures",
        "projections",
        "projection_manifest_commitment",
        "receipt_count",
        "receipt_set_commitment",
        "review_attestation",
        "review_expires_at",
        "review_receipt_commitment",
        "run_id",
        "source_item_count",
        "source_manifest_commitment",
        "verification_status",
    }
)
CUTOVER_KEYS = frozenset(
    {
        "admission_id",
        "authority_scope",
        "candidate_cutover_id",
        "contract_version",
        "core_capability_digest",
        "core_schema_digest",
        "cutover_signature",
        "e3_commitment_key_epoch",
        "e3_projection_manifest_commitment",
        "e3_source_manifest_commitment",
        "erasure_ledger_epoch",
        "erasure_ledger_head_hash",
        "erasure_ledger_verification",
        "finalization_id",
        "freeze_commitment",
        "policy_digest",
        "privacy_check_set_commitment",
        "promotion_commitment",
        "promotion_id",
        "release_manifest_digest",
        "run_id",
        "semantic_generation",
        "signature_algorithm",
        "signing_key_fingerprint",
        "source_installation_id",
        "verifier_bundle_digest",
        "writer_freeze_id",
    }
)

FINALIZER_SQL = """
WITH migration_run AS (
  SELECT *
    FROM operations.reviewed_identity_migration_runs
   WHERE run_id = CAST(:run_id AS uuid)
     AND logical_source_manifest_commitment = :source_manifest_commitment
     AND projection_manifest_commitment = :projection_manifest_commitment
     AND review_receipt_commitment = :review_receipt_commitment
     AND signing_key_fingerprint = :review_signing_key_fingerprint
     AND source_item_count = :source_item_count
     AND decision_count = :decision_count
), attempted AS (
  INSERT INTO operations.reviewed_identity_finalizer_admissions (
    admission_id,run_id,finalization_id,contract_version,
    verification_status,document_sha256,document_octets,
    finalization_commitment,decision_manifest_commitment,
    receipt_set_commitment,lineage_set_commitment,
    privacy_closure_set_commitment,auto_expiry_effect_set_commitment,
    review_receipt_commitment,release_manifest_digest,
    migration_tool_bundle_digest,core_oci_manifest_digest,
    core_schema_digest,core_capability_digest,policy_digest,
    review_signing_key_fingerprint,finalization_signing_key_fingerprint,
    verifier_bundle_digest,expires_at
  )
  SELECT CAST(:admission_id AS uuid),run_id,CAST(:finalization_id AS uuid),
         'reviewed-identity-finalizer-admission-v1','offline_verified',
         :document_sha256,:document_octets,:finalization_commitment,
         :decision_manifest_commitment,:receipt_set_commitment,
         :lineage_set_commitment,:privacy_closure_set_commitment,
         :auto_expiry_effect_set_commitment,:review_receipt_commitment,
         release_manifest_digest,migration_tool_bundle_digest,
         core_oci_manifest_digest,core_schema_digest,core_capability_digest,
         policy_digest,signing_key_fingerprint,
         :finalization_signing_key_fingerprint,:verifier_bundle_digest,
         transaction_timestamp() + interval '15 minutes'
    FROM migration_run
  ON CONFLICT DO NOTHING
  RETURNING admission_id
), exact_existing AS (
  SELECT admission.admission_id
    FROM operations.reviewed_identity_finalizer_admissions AS admission
    JOIN migration_run
      ON migration_run.run_id = admission.run_id
     AND migration_run.release_manifest_digest =
         admission.release_manifest_digest
     AND migration_run.migration_tool_bundle_digest =
         admission.migration_tool_bundle_digest
     AND migration_run.core_oci_manifest_digest =
         admission.core_oci_manifest_digest
     AND migration_run.core_schema_digest = admission.core_schema_digest
     AND migration_run.core_capability_digest =
         admission.core_capability_digest
     AND migration_run.policy_digest = admission.policy_digest
     AND migration_run.signing_key_fingerprint =
         admission.review_signing_key_fingerprint
   WHERE admission.admission_id = CAST(:admission_id AS uuid)
     AND admission.run_id = CAST(:run_id AS uuid)
     AND admission.finalization_id = CAST(:finalization_id AS uuid)
     AND admission.document_sha256 = :document_sha256
     AND admission.document_octets = :document_octets
     AND admission.finalization_commitment = :finalization_commitment
     AND admission.decision_manifest_commitment =
         :decision_manifest_commitment
     AND admission.receipt_set_commitment = :receipt_set_commitment
     AND admission.lineage_set_commitment = :lineage_set_commitment
     AND admission.privacy_closure_set_commitment =
         :privacy_closure_set_commitment
     AND admission.auto_expiry_effect_set_commitment =
         :auto_expiry_effect_set_commitment
     AND admission.review_receipt_commitment = :review_receipt_commitment
     AND admission.finalization_signing_key_fingerprint =
         :finalization_signing_key_fingerprint
     AND admission.verifier_bundle_digest = :verifier_bundle_digest
)
SELECT admission_id FROM attempted
UNION ALL
SELECT admission_id FROM exact_existing
LIMIT 1
"""

CUTOVER_SQL = """
WITH attempted AS (
  INSERT INTO operations.reviewed_identity_cutover_admissions (
    admission_id,promotion_id,run_id,finalization_id,candidate_cutover_id,
    writer_freeze_id,contract_version,verification_status,document_sha256,
    document_octets,promotion_commitment,privacy_check_set_commitment,
    freeze_commitment,source_installation_id,semantic_generation,
    e3_source_manifest_commitment,e3_projection_manifest_commitment,
    e3_commitment_key_epoch,erasure_ledger_epoch,erasure_ledger_head_hash,
    erasure_ledger_verification,release_manifest_digest,core_schema_digest,
    core_capability_digest,policy_digest,signing_key_fingerprint,
    verifier_bundle_digest,expires_at
  ) VALUES (
    CAST(:admission_id AS uuid),CAST(:promotion_id AS uuid),
    CAST(:run_id AS uuid),CAST(:finalization_id AS uuid),
    CAST(:candidate_cutover_id AS uuid),CAST(:writer_freeze_id AS uuid),
    'reviewed-identity-cutover-admission-v1','offline_verified',
    :document_sha256,:document_octets,:promotion_commitment,
    :privacy_check_set_commitment,:freeze_commitment,
    CAST(:source_installation_id AS uuid),:semantic_generation,
    :e3_source_manifest_commitment,:e3_projection_manifest_commitment,
    :e3_commitment_key_epoch,:erasure_ledger_epoch,
    :erasure_ledger_head_hash,:erasure_ledger_verification,
    :release_manifest_digest,:core_schema_digest,:core_capability_digest,
    :policy_digest,:signing_key_fingerprint,:verifier_bundle_digest,
    transaction_timestamp() + interval '15 minutes'
  )
  ON CONFLICT DO NOTHING
  RETURNING admission_id
), exact_existing AS (
  SELECT admission_id
    FROM operations.reviewed_identity_cutover_admissions
   WHERE admission_id = CAST(:admission_id AS uuid)
     AND promotion_id = CAST(:promotion_id AS uuid)
     AND run_id = CAST(:run_id AS uuid)
     AND finalization_id = CAST(:finalization_id AS uuid)
     AND candidate_cutover_id = CAST(:candidate_cutover_id AS uuid)
     AND writer_freeze_id = CAST(:writer_freeze_id AS uuid)
     AND document_sha256 = :document_sha256
     AND document_octets = :document_octets
     AND promotion_commitment = :promotion_commitment
     AND privacy_check_set_commitment = :privacy_check_set_commitment
     AND freeze_commitment = :freeze_commitment
     AND source_installation_id = CAST(:source_installation_id AS uuid)
     AND semantic_generation = :semantic_generation
     AND e3_source_manifest_commitment = :e3_source_manifest_commitment
     AND e3_projection_manifest_commitment =
         :e3_projection_manifest_commitment
     AND e3_commitment_key_epoch = :e3_commitment_key_epoch
     AND erasure_ledger_epoch = :erasure_ledger_epoch
     AND erasure_ledger_head_hash = :erasure_ledger_head_hash
     AND erasure_ledger_verification = :erasure_ledger_verification
     AND release_manifest_digest = :release_manifest_digest
     AND core_schema_digest = :core_schema_digest
     AND core_capability_digest = :core_capability_digest
     AND policy_digest = :policy_digest
     AND signing_key_fingerprint = :signing_key_fingerprint
     AND verifier_bundle_digest = :verifier_bundle_digest
)
SELECT admission_id FROM attempted
UNION ALL
SELECT admission_id FROM exact_existing
LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class AdmissionOperation:
    name: Literal["finalizer", "cutover"]
    contract: str
    result_contract: str
    sql: str


OPERATIONS = {
    "finalizer": AdmissionOperation(
        "finalizer",
        "identity-finalizer-admission-e5u-v1",
        "identity-finalizer-admission-result-e5u-v1",
        FINALIZER_SQL,
    ),
    "cutover": AdmissionOperation(
        "cutover",
        "identity-cutover-admission-e5u-v1",
        "identity-cutover-admission-result-e5u-v1",
        CUTOVER_SQL,
    ),
}


class AdmissionWriterError(RuntimeError):
    """A content-free authority-admission failure."""


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    admission_id: uuid.UUID
    document: bytes
    parameters: dict[str, Any]


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _uuid7(value: Any) -> str:
    if not isinstance(value, str):
        raise AdmissionWriterError("authority admission document is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise AdmissionWriterError("authority admission document is invalid") from error
    if str(parsed) != value or parsed.version != 7:
        raise AdmissionWriterError("authority admission document is invalid")
    return value


def _hex64(value: Any) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise AdmissionWriterError("authority admission document is invalid")
    return value


def _integer(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AdmissionWriterError("authority admission document is invalid")
    return value


def _decimal(value: Any, *, minimum: int) -> int:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise AdmissionWriterError("authority admission document is invalid")
    return _integer(int(value), minimum=minimum)


def _decode_document(encoded: str) -> bytes:
    if not encoded or len(encoded) > 5_592_408:
        raise AdmissionWriterError("authority admission request is invalid")
    try:
        document = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise AdmissionWriterError("authority admission request is invalid") from error
    if (
        not 2 <= len(document) <= MAX_DOCUMENT_BYTES
        or base64.b64encode(document).decode("ascii") != encoded
        or b"\0" in document
    ):
        raise AdmissionWriterError("authority admission request is invalid")
    return document


def _parse_document(document: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_exact_object,
            parse_float=lambda _value: (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AdmissionWriterError("authority admission document is invalid") from error
    if not isinstance(value, dict):
        raise AdmissionWriterError("authority admission document is invalid")
    return value


def _finalizer_parameters(
    admission_id: uuid.UUID,
    document: bytes,
    value: dict[str, Any],
) -> dict[str, Any]:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if set(value) != FINALIZER_KEYS or canonical != document:
        raise AdmissionWriterError("authority admission document is invalid")
    if (
        value.get("contract_version")
        != "reviewed-identity-migration-finalization-v1"
        or value.get("verification_status") != "candidate_unverified"
        or value.get("authoritative") is not False
        or not isinstance(value.get("review_attestation"), dict)
        or not isinstance(value.get("finalization_signing"), dict)
        or not isinstance(value.get("finalization_attestation"), dict)
    ):
        raise AdmissionWriterError("authority admission document is invalid")
    run_id = _uuid7(value["run_id"])
    finalization_id = _uuid7(value["finalization_id"])
    if run_id != finalization_id:
        raise AdmissionWriterError("authority admission document is invalid")
    review = value["review_attestation"]
    signing = value["finalization_signing"]
    attestation = value["finalization_attestation"]
    review_fingerprint = _hex64(review.get("signing_key_fingerprint"))
    finalization_fingerprint = _hex64(signing.get("signing_key_fingerprint"))
    if (
        attestation.get("signing_key_fingerprint") != finalization_fingerprint
        or signing.get("signature_algorithm") != "ed25519"
        or attestation.get("signature_algorithm") != "ed25519"
        or not isinstance(attestation.get("finalization_signature"), str)
        or HEX128.fullmatch(attestation["finalization_signature"]) is None
    ):
        raise AdmissionWriterError("authority admission document is invalid")
    parameters: dict[str, Any] = {
        "admission_id": str(admission_id),
        "run_id": run_id,
        "finalization_id": finalization_id,
        "document_sha256": hashlib.sha256(document).hexdigest(),
        "document_octets": len(document),
        "source_item_count": _integer(value["source_item_count"], minimum=1),
        "decision_count": _integer(value["decision_count"], minimum=1),
        "review_signing_key_fingerprint": review_fingerprint,
        "finalization_signing_key_fingerprint": finalization_fingerprint,
        "verifier_bundle_digest": FINALIZER_VERIFIER_DIGEST,
    }
    for key in (
        "source_manifest_commitment",
        "projection_manifest_commitment",
        "finalization_commitment",
        "decision_manifest_commitment",
        "receipt_set_commitment",
        "lineage_set_commitment",
        "privacy_closure_set_commitment",
        "auto_expiry_effect_set_commitment",
        "review_receipt_commitment",
    ):
        parameters[key] = _hex64(value[key])
    return parameters


def _cutover_parameters(
    admission_id: uuid.UUID,
    document: bytes,
    value: dict[str, Any],
) -> dict[str, Any]:
    if (
        set(value) != CUTOVER_KEYS
        or not all(isinstance(item, str) for item in value.values())
        or value.get("contract_version")
        != "reviewed-identity-semantic-cutover-v1"
        or value.get("authority_scope") != "identity_semantics"
        or value.get("admission_id") != str(admission_id)
        or value.get("signature_algorithm") != "ed25519"
        or value.get("erasure_ledger_verification") != "head_matched"
        or value.get("verifier_bundle_digest") != CUTOVER_VERIFIER_DIGEST
        or HEX128.fullmatch(value.get("cutover_signature", "")) is None
    ):
        raise AdmissionWriterError("authority admission document is invalid")
    parameters: dict[str, Any] = {
        "admission_id": str(admission_id),
        "document_sha256": hashlib.sha256(document).hexdigest(),
        "document_octets": len(document),
        "semantic_generation": _decimal(value["semantic_generation"], minimum=1),
        "e3_commitment_key_epoch": _decimal(
            value["e3_commitment_key_epoch"], minimum=1
        ),
        "erasure_ledger_epoch": _decimal(
            value["erasure_ledger_epoch"], minimum=0
        ),
    }
    for key in (
        "promotion_id",
        "run_id",
        "finalization_id",
        "candidate_cutover_id",
        "writer_freeze_id",
        "source_installation_id",
    ):
        parameters[key] = _uuid7(value[key])
    for key in (
        "promotion_commitment",
        "privacy_check_set_commitment",
        "freeze_commitment",
        "e3_source_manifest_commitment",
        "e3_projection_manifest_commitment",
        "erasure_ledger_head_hash",
        "release_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "policy_digest",
        "signing_key_fingerprint",
        "verifier_bundle_digest",
    ):
        parameters[key] = _hex64(value[key])
    parameters["erasure_ledger_verification"] = value[
        "erasure_ledger_verification"
    ]
    return parameters


def parse_request(raw: bytes, operation: AdmissionOperation) -> AdmissionRequest:
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise AdmissionWriterError("authority admission request is invalid")
    try:
        payload = json.loads(raw, object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AdmissionWriterError("authority admission request is invalid") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "admission_id", "document_b64"}
        or payload.get("contract") != operation.contract
        or not isinstance(payload.get("admission_id"), str)
        or not isinstance(payload.get("document_b64"), str)
    ):
        raise AdmissionWriterError("authority admission request is invalid")
    admission_text = _uuid7(payload["admission_id"])
    admission_id = uuid.UUID(admission_text)
    document = _decode_document(payload["document_b64"])
    value = _parse_document(document)
    if operation.name == "finalizer":
        parameters = _finalizer_parameters(admission_id, document, value)
    else:
        parameters = _cutover_parameters(admission_id, document, value)
    return AdmissionRequest(admission_id, document, parameters)


def read_request(operation: AdmissionOperation) -> AdmissionRequest:
    return parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1), operation)


def database_url() -> str:
    raw = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise AdmissionWriterError("authority admission database URL is invalid") from error
    password = unquote(parsed.password or "")
    expected = (
        f"postgresql+psycopg://home_agent_owner:{password}"
        "@postgres:5432/home_agent"
    )
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.username != "home_agent_owner"
        or parsed.hostname != "postgres"
        or port != 5432
        or parsed.path != "/home_agent"
        or parsed.query
        or parsed.fragment
        or PASSWORD.fullmatch(password) is None
        or raw != expected
    ):
        raise AdmissionWriterError("authority admission database URL is invalid")
    return raw


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


async def _execute_once(
    url: str,
    operation: AdmissionOperation,
    request: AdmissionRequest,
) -> uuid.UUID:
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "options": (
                "-c log_parameter_max_length_on_error=0 "
                "-c statement_timeout=30000 "
                "-c lock_timeout=5000"
            )
        },
    )
    try:
        async with engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                result = (
                    await connection.execute(
                        text(operation.sql),
                        request.parameters,
                    )
                ).scalar_one_or_none()
        if not isinstance(result, uuid.UUID) or result != request.admission_id:
            raise AdmissionWriterError("authority admission was rejected")
        return result
    finally:
        await engine.dispose()


async def execute(
    url: str,
    operation: AdmissionOperation,
    request: AdmissionRequest,
) -> uuid.UUID:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await _execute_once(url, operation, request)
        except DBAPIError as error:
            if _sqlstate(error) not in RETRYABLE_SQLSTATES or attempt + 1 >= MAX_ATTEMPTS:
                raise AdmissionWriterError("authority admission was rejected") from error
            await asyncio.sleep(0.05 * (2**attempt))
    raise AssertionError("bounded authority-admission retry loop did not terminate")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1 or arguments[0] not in OPERATIONS:
        print("identity admission writer requires one fixed operation", file=sys.stderr)
        return 64
    operation = OPERATIONS[arguments[0]]
    try:
        request = read_request(operation)
        result = asyncio.run(execute(database_url(), operation, request))
    except AdmissionWriterError:
        print("identity authority admission failed closed", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "admission_id": str(result),
                "contract": operation.result_contract,
                "status": "admitted",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
