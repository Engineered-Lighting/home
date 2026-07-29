from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class AuthorityOperation:
    name: Literal["finalize", "cutover"]
    contract: str
    result_contract: str
    expected_role: str
    function: str


OPERATIONS = {
    "finalize": AuthorityOperation(
        name="finalize",
        contract="identity-finalizer-execution-e5n-v1",
        result_contract="identity-finalizer-result-e5n-v1",
        expected_role="home_agent_identity_finalizer",
        function="operations.finalize_reviewed_identity_migration",
    ),
    "cutover": AuthorityOperation(
        name="cutover",
        contract="identity-cutover-execution-e5n-v1",
        result_contract="identity-cutover-result-e5n-v1",
        expected_role="home_agent_identity_cutover",
        function="operations.commit_reviewed_identity_cutover",
    ),
}


class AuthorityExecutionError(RuntimeError):
    """A content-free failure safe to report at the operator boundary."""


@dataclass(frozen=True, slots=True)
class AuthorityRequest:
    admission_id: uuid.UUID
    document: bytes


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_request(raw: bytes, operation: AuthorityOperation) -> AuthorityRequest:
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise AuthorityExecutionError("authority request is missing or oversized")
    try:
        payload = json.loads(raw, object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AuthorityExecutionError("authority request is invalid") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "admission_id", "document_b64"}
        or payload.get("contract") != operation.contract
        or not isinstance(payload.get("admission_id"), str)
        or not isinstance(payload.get("document_b64"), str)
    ):
        raise AuthorityExecutionError("authority request is invalid")
    try:
        admission_id = uuid.UUID(payload["admission_id"])
    except ValueError as error:
        raise AuthorityExecutionError("authority request is invalid") from error
    if str(admission_id) != payload["admission_id"] or admission_id.version != 7:
        raise AuthorityExecutionError("authority request is invalid")
    encoded = payload["document_b64"]
    if not encoded or len(encoded) > 5_592_408:
        raise AuthorityExecutionError("authority request is invalid")
    try:
        document = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise AuthorityExecutionError("authority request is invalid") from error
    if (
        len(document) < 2
        or len(document) > MAX_DOCUMENT_BYTES
        or base64.b64encode(document).decode("ascii") != encoded
    ):
        raise AuthorityExecutionError("authority request is invalid")
    return AuthorityRequest(admission_id=admission_id, document=document)


def read_request(operation: AuthorityOperation) -> AuthorityRequest:
    return parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1), operation)


def database_url(operation: AuthorityOperation) -> str:
    raw = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    if not raw:
        raise AuthorityExecutionError("isolated authority database URL is missing")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise AuthorityExecutionError(
            "isolated authority database URL is invalid"
        ) from error
    password = unquote(parsed.password or "")
    expected = (
        f"postgresql+psycopg://{operation.expected_role}:{password}"
        "@postgres:5432/home_agent"
    )
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.username != operation.expected_role
        or parsed.hostname != "postgres"
        or port != 5432
        or parsed.path != "/home_agent"
        or parsed.query
        or parsed.fragment
        or PASSWORD.fullmatch(password) is None
        or raw != expected
    ):
        raise AuthorityExecutionError("isolated authority database URL is invalid")
    return raw


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


async def _execute_once(
    url: str,
    operation: AuthorityOperation,
    request: AuthorityRequest,
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
                # This must remain the first statement in the transaction. Both
                # SECURITY DEFINER kernels validate the disposable login,
                # transaction isolation, one-time admission, current erasure
                # state, and the exact private-document digest themselves.
                result = (
                    await connection.execute(
                        text(
                            f"SELECT {operation.function}("
                            "CAST(:document AS bytea),"
                            "CAST(:admission_id AS uuid))"
                        ),
                        {
                            "document": request.document,
                            "admission_id": request.admission_id,
                        },
                    )
                ).scalar_one()
        if not isinstance(result, uuid.UUID):
            raise AuthorityExecutionError("authority kernel returned invalid receipt")
        return result
    finally:
        await engine.dispose()


async def execute(
    url: str,
    operation: AuthorityOperation,
    request: AuthorityRequest,
) -> uuid.UUID:
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await _execute_once(url, operation, request)
        except DBAPIError as error:
            if _sqlstate(error) not in RETRYABLE_SQLSTATES or attempt + 1 >= MAX_ATTEMPTS:
                raise AuthorityExecutionError("authority kernel rejected request") from error
            await asyncio.sleep(0.05 * (2**attempt))
    raise AssertionError("bounded authority retry loop did not terminate")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1 or arguments[0] not in OPERATIONS:
        print("identity authority executor requires one fixed operation", file=sys.stderr)
        return 64
    operation = OPERATIONS[arguments[0]]
    try:
        request = read_request(operation)
        result = asyncio.run(execute(database_url(operation), operation, request))
    except AuthorityExecutionError:
        print("identity authority execution failed closed", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "contract": operation.result_contract,
                "result_id": str(result),
                "status": "committed",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
