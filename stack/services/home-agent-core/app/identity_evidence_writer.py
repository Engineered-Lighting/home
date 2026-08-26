"""Carry the signed E4 evidence documents into PostgreSQL.

The semantic cutover kernel reads four tables — the legacy writer evidence, the
enforced writer freeze, six privacy check receipts, and the authority candidate
— and until now nothing in the application wrote any of them. The whole of
``app`` held exactly two ``INSERT INTO operations.`` statements, both
admissions. The only code that filled these four was a test fixture, which is
why the gap was invisible from either end: the gate exercised the commit kernel
against a pre-populated database.

Steps 21 to 23 already sign documents that carry, key for key, the columns
those tables want. Nothing here invents or maps anything. Each arm parses one
signed packet, converts every value to the column's own type, and inserts it.

Like the admission writers, this reads one private document on stdin and makes
one bounded write. It is deliberately not a kernel: these tables carry operator
attestations, and the governed decisions are made later, by
``operations.commit_reviewed_identity_cutover`` reading what is written here.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re
import sys
from typing import Any, Callable, Literal
from urllib.parse import unquote, urlsplit
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


MAX_DOCUMENT_BYTES = 4_194_304
MAX_REQUEST_BYTES = 5_593_088
MAX_ATTEMPTS = 3
MAX_TEXT = 512
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
PASSWORD = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


class EvidenceWriterError(RuntimeError):
    """A content-free identity evidence write failure."""


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate object key")
    return dict(pairs)


def _text_value(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise EvidenceWriterError("identity evidence document is invalid")
    return value


def _uuid_value(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise EvidenceWriterError("identity evidence document is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise EvidenceWriterError("identity evidence document is invalid") from error
    if str(parsed) != value:
        raise EvidenceWriterError("identity evidence document is invalid")
    return parsed


def _integer_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceWriterError("identity evidence document is invalid")
    return value


def _boolean_value(value: Any) -> bool:
    if not isinstance(value, bool):
        raise EvidenceWriterError("identity evidence document is invalid")
    return value


def _timestamp_value(value: Any) -> datetime:
    if not isinstance(value, str) or TIMESTAMP.fullmatch(value) is None:
        raise EvidenceWriterError("identity evidence document is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        raise EvidenceWriterError("identity evidence document is invalid") from error


Converter = Callable[[Any], Any]

# Every column each table takes from its signed document, with the converter
# that proves the value is the column's own type before it reaches PostgreSQL.
# The key sets are pinned against the operator producers by
# tests/home_agent/test_phase3_e4_evidence_contract.py.
WRITER_EVIDENCE_COLUMNS: dict[str, Converter] = {
    "evidence_id": _uuid_value,
    "run_id": _uuid_value,
    "source_installation_id": _uuid_value,
    "semantic_generation": _integer_value,
    "source_projection_commitment": _text_value,
    "evidence_strength": _text_value,
    "integrity_result": _text_value,
    "checkpoint_result": _text_value,
    "journal_result": _text_value,
    "legacy_context_cutoff_status": _text_value,
    "release_manifest_digest": _text_value,
    "freeze_kernel_build_digest": _text_value,
    "evidence_commitment": _text_value,
    "signature_algorithm": _text_value,
    "signing_key_fingerprint": _text_value,
    "evidence_signature": _text_value,
    "observed_at": _timestamp_value,
}
ENFORCED_FREEZE_COLUMNS: dict[str, Converter] = {
    "freeze_id": _uuid_value,
    "run_id": _uuid_value,
    "writer_evidence_id": _uuid_value,
    "contract_version": _text_value,
    "enforcement_status": _text_value,
    "write_probe_result": _text_value,
    "semantic_write_status": _text_value,
    "legacy_context_cutoff_status": _text_value,
    "recognition_mode": _text_value,
    "source_installation_id": _uuid_value,
    "semantic_generation": _integer_value,
    "source_projection_commitment": _text_value,
    "e3_source_manifest_commitment": _text_value,
    "e3_projection_manifest_commitment": _text_value,
    "e3_commitment_key_epoch": _integer_value,
    "writer_evidence_commitment": _text_value,
    "trigger_set_commitment": _text_value,
    "blocked_probe_commitment": _text_value,
    "release_manifest_digest": _text_value,
    "freeze_kernel_build_digest": _text_value,
    "policy_digest": _text_value,
    "freeze_commitment": _text_value,
    "signature_algorithm": _text_value,
    "signing_key_fingerprint": _text_value,
    "freeze_signature": _text_value,
    "enforced_at": _timestamp_value,
    "verified_at": _timestamp_value,
}
PRIVACY_RECEIPT_COLUMNS: dict[str, Converter] = {
    "check_id": _uuid_value,
    "run_id": _uuid_value,
    "finalization_id": _uuid_value,
    "check_category": _text_value,
    "check_result": _text_value,
    "residual_code": _text_value,
    "check_commitment": _text_value,
    "receipt_commitment": _text_value,
    "policy_digest": _text_value,
    "checked_at": _timestamp_value,
}
AUTHORITY_CANDIDATE_COLUMNS: dict[str, Converter] = {
    "cutover_id": _uuid_value,
    "run_id": _uuid_value,
    "finalization_id": _uuid_value,
    "writer_evidence_id": _uuid_value,
    "contract_version": _text_value,
    "authority_status": _text_value,
    "authoritative": _boolean_value,
    "ingress_check_id": _uuid_value,
    "ingress_check_category": _text_value,
    "retrieval_check_id": _uuid_value,
    "retrieval_check_category": _text_value,
    "prompt_check_id": _uuid_value,
    "prompt_check_category": _text_value,
    "initiative_check_id": _uuid_value,
    "initiative_check_category": _text_value,
    "export_check_id": _uuid_value,
    "export_check_category": _text_value,
    "edge_block_check_id": _uuid_value,
    "edge_block_check_category": _text_value,
    "required_privacy_check_result": _text_value,
    "required_privacy_residual_code": _text_value,
    "privacy_check_set_commitment": _text_value,
    "cutover_commitment": _text_value,
    "policy_digest": _text_value,
    "signature_algorithm": _text_value,
    "signing_key_fingerprint": _text_value,
    "cutover_signature": _text_value,
    "attested_at": _timestamp_value,
}


@dataclass(frozen=True, slots=True)
class EvidenceInsert:
    table: str
    identity: str
    columns: tuple[tuple[str, Converter], ...]
    document_key: str
    many: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceOperation:
    name: Literal["freeze", "privacy", "cutover"]
    contract: str
    result_contract: str
    packet_keys: frozenset[str]
    inserts: tuple[EvidenceInsert, ...]


def _columns(mapping: dict[str, Converter]) -> tuple[tuple[str, Converter], ...]:
    # Sorted so the generated SQL is stable and reviewable.
    return tuple(sorted(mapping.items()))


OPERATIONS = {
    operation.name: operation
    for operation in (
        EvidenceOperation(
            "freeze",
            "identity-writer-freeze-evidence-e5an-v1",
            "identity-writer-freeze-evidence-result-e5an-v1",
            frozenset(
                {
                    "contract",
                    "authoritative",
                    "run_id",
                    "finalization_id",
                    "private_review_sha256",
                    "physical_observation_sha256",
                    "writer_evidence",
                    "enforced_writer_freeze",
                }
            ),
            (
                EvidenceInsert(
                    "operations.legacy_identity_writer_evidence",
                    "evidence_id",
                    _columns(WRITER_EVIDENCE_COLUMNS),
                    "writer_evidence",
                ),
                EvidenceInsert(
                    "operations.enforced_legacy_identity_writer_freezes",
                    "freeze_id",
                    _columns(ENFORCED_FREEZE_COLUMNS),
                    "enforced_writer_freeze",
                ),
            ),
        ),
        EvidenceOperation(
            "privacy",
            "identity-privacy-cutover-evidence-e5an-v1",
            "identity-privacy-cutover-evidence-result-e5an-v1",
            frozenset(
                {
                    "contract",
                    "authoritative",
                    "run_id",
                    "finalization_id",
                    "freeze_id",
                    "privacy_observation",
                    "privacy_observation_sha256",
                    "attestation_algorithm",
                    "attestation_key_fingerprint",
                    "privacy_observation_signature",
                    "receipts",
                    "privacy_check_set_commitment",
                }
            ),
            (
                EvidenceInsert(
                    "operations.privacy_cutover_check_receipts",
                    "check_id",
                    _columns(PRIVACY_RECEIPT_COLUMNS),
                    "receipts",
                    many=True,
                ),
            ),
        ),
        EvidenceOperation(
            "cutover",
            "identity-semantic-cutover-candidate-e5an-v1",
            "identity-semantic-cutover-candidate-result-e5an-v1",
            frozenset(
                {
                    "contract",
                    "authoritative",
                    "writer_freeze_evidence",
                    "privacy_cutover_evidence",
                    "erasure_current_receipt",
                    "semantic_authority_candidate",
                    "cutover_document",
                    "cutover_document_sha256",
                }
            ),
            (
                EvidenceInsert(
                    "operations.semantic_authority_cutovers",
                    "cutover_id",
                    _columns(AUTHORITY_CANDIDATE_COLUMNS),
                    "semantic_authority_candidate",
                ),
            ),
        ),
    )
}


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    operation: EvidenceOperation
    rows: tuple[tuple[EvidenceInsert, dict[str, Any]], ...]


def _statement(insert: EvidenceInsert) -> str:
    names = ",".join(name for name, _ in insert.columns)
    values = ",".join(f":{name}" for name, _ in insert.columns)
    return (
        f"INSERT INTO {insert.table} ({names}) VALUES ({values}) "
        "ON CONFLICT DO NOTHING"
    )


def _verify_statement(insert: EvidenceInsert) -> str:
    names = ",".join(name for name, _ in insert.columns)
    return (
        f"SELECT {names} FROM {insert.table} "
        f"WHERE {insert.identity} = :{insert.identity}"
    )


def _convert(insert: EvidenceInsert, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceWriterError("identity evidence document is invalid")
    expected = {name for name, _ in insert.columns}
    if set(value) != expected:
        raise EvidenceWriterError("identity evidence document is invalid")
    return {name: convert(value[name]) for name, convert in insert.columns}


def _decode(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise EvidenceWriterError("identity evidence document is invalid") from error
    if not raw or len(raw) > MAX_DOCUMENT_BYTES or b"\0" in raw:
        raise EvidenceWriterError("identity evidence document is invalid")
    return raw


def parse_request(raw: bytes, operation: EvidenceOperation) -> EvidenceRequest:
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise EvidenceWriterError("identity evidence request is invalid")
    try:
        payload = json.loads(raw, object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise EvidenceWriterError("identity evidence request is invalid") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "document_b64"}
        or payload.get("contract") != operation.contract
        or not isinstance(payload.get("document_b64"), str)
    ):
        raise EvidenceWriterError("identity evidence request is invalid")
    try:
        packet = json.loads(_decode(payload["document_b64"]), object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise EvidenceWriterError("identity evidence document is invalid") from error
    if not isinstance(packet, dict) or set(packet) != operation.packet_keys:
        raise EvidenceWriterError("identity evidence document is invalid")

    rows: list[tuple[EvidenceInsert, dict[str, Any]]] = []
    for insert in operation.inserts:
        value = packet[insert.document_key]
        if insert.many:
            if not isinstance(value, list) or not value or len(value) > 64:
                raise EvidenceWriterError("identity evidence document is invalid")
            converted = [_convert(insert, item) for item in value]
            identities = {row[insert.identity] for row in converted}
            if len(identities) != len(converted):
                raise EvidenceWriterError("identity evidence document is invalid")
            rows.extend((insert, row) for row in converted)
        else:
            rows.append((insert, _convert(insert, value)))
    return EvidenceRequest(operation, tuple(rows))


def read_request(operation: EvidenceOperation) -> EvidenceRequest:
    return parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1), operation)


def database_url() -> str:
    raw = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise EvidenceWriterError("identity evidence database URL is invalid") from error
    password = unquote(parsed.password or "")
    expected = (
        f"postgresql+psycopg://home_agent_owner:{password}@postgres:5432/home_agent"
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
        raise EvidenceWriterError("identity evidence database URL is invalid")
    return raw


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


async def _execute_once(url: str, request: EvidenceRequest) -> tuple[str, ...]:
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
        written: list[str] = []
        async with engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="SERIALIZABLE"
            )
            async with connection.begin():
                for insert, row in request.rows:
                    await connection.execute(text(_statement(insert)), row)
                    stored = (
                        await connection.execute(
                            text(_verify_statement(insert)),
                            {insert.identity: row[insert.identity]},
                        )
                    ).mappings().one_or_none()
                    # A replayed step must find exactly what it meant to write.
                    # Anything else is a different document reusing an identity.
                    if stored is None or dict(stored) != row:
                        raise EvidenceWriterError(
                            "identity evidence write was rejected"
                        )
                    written.append(str(row[insert.identity]))
        return tuple(written)
    finally:
        await engine.dispose()


async def execute(url: str, request: EvidenceRequest) -> tuple[str, ...]:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await _execute_once(url, request)
        except DBAPIError as error:
            if _sqlstate(error) not in RETRYABLE_SQLSTATES or attempt + 1 >= MAX_ATTEMPTS:
                raise EvidenceWriterError(
                    "identity evidence write was rejected"
                ) from error
            await asyncio.sleep(0.05 * (2**attempt))
    raise AssertionError("bounded evidence write retry loop did not terminate")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1 or arguments[0] not in OPERATIONS:
        print("identity evidence writer requires one fixed operation", file=sys.stderr)
        return 64
    operation = OPERATIONS[arguments[0]]
    try:
        request = read_request(operation)
        written = asyncio.run(execute(database_url(), request))
    except EvidenceWriterError:
        print("identity evidence write failed closed", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "contract": operation.result_contract,
                "operation": operation.name,
                "written": list(written),
                "status": "recorded",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
