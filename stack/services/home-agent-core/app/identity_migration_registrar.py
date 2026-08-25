"""Register one reviewed identity manifest through the 0008 kernel.

The reviewed packet is compiled and signed inside a networkless signing
sandbox, so nothing in that ceremony can reach PostgreSQL. This module is the
in-image half of the same stdin bridge the admission writers use: the operator
pipes one private manifest in, and exactly one `SECURITY DEFINER` kernel call
goes out.

It is deliberately separate from `identity_admission_writer`. That module pins
its connection to `home_agent_owner`; the registration kernel refuses any caller
but `home_agent_identity_migration`, whose login stays expired until a
root-controlled, two-minute ceremony activates it.

Registration is irreversible. The database allows exactly one
`record_only -> shadow` authorization and exactly one run per authorization, and
no role holds `DELETE` on the runs table, so a manifest registered against a run
that cannot then be finalized cannot be released. The kernel enforces the rules;
this module's job is to refuse anything malformed before it gets there, and to
prove the run it registered is the run the operator asked for.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import json
import os
import re
import sys
from typing import Any
from urllib.parse import unquote, urlsplit
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


CONTRACT = "identity-migration-registration-e5ak-v1"
RESULT_CONTRACT = "identity-migration-registration-result-e5ak-v1"
CALLER_ROLE = "home_agent_identity_migration"
MANIFEST_KEYS = frozenset({"run", "source_items", "decisions"})
MAX_MANIFEST_BYTES = 4_194_304
MAX_REQUEST_BYTES = 5_593_088
MAX_ATTEMPTS = 3
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
PASSWORD = re.compile(r"^[0-9a-f]{64}$")

REGISTER_SQL = (
    "SELECT operations.register_reviewed_identity_migration("
    "CAST(:manifest AS jsonb))"
)


class MigrationRegistrarError(RuntimeError):
    """A content-free reviewed-manifest registration failure."""


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    run_id: uuid.UUID
    manifest: str


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate object key")
    return dict(pairs)


def _uuid7(value: Any) -> str:
    if not isinstance(value, str):
        raise MigrationRegistrarError("identity migration registration is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise MigrationRegistrarError(
            "identity migration registration is invalid"
        ) from error
    if parsed.version != 7 or str(parsed) != value:
        raise MigrationRegistrarError("identity migration registration is invalid")
    return value


def _decode_manifest(encoded: str) -> bytes:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise MigrationRegistrarError(
            "identity migration registration is invalid"
        ) from error
    if not raw or len(raw) > MAX_MANIFEST_BYTES or b"\0" in raw:
        raise MigrationRegistrarError("identity migration registration is invalid")
    return raw


def parse_request(raw: bytes) -> RegistrationRequest:
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise MigrationRegistrarError("identity migration registration is invalid")
    try:
        payload = json.loads(raw, object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise MigrationRegistrarError(
            "identity migration registration is invalid"
        ) from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"contract", "run_id", "manifest_b64"}
        or payload.get("contract") != CONTRACT
        or not isinstance(payload.get("manifest_b64"), str)
    ):
        raise MigrationRegistrarError("identity migration registration is invalid")
    run_text = _uuid7(payload["run_id"])
    manifest_bytes = _decode_manifest(payload["manifest_b64"])
    try:
        manifest = json.loads(manifest_bytes, object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise MigrationRegistrarError(
            "identity migration registration is invalid"
        ) from error
    # The kernel enforces this too, and re-enforces far more besides. Checking
    # here keeps a malformed manifest from ever reaching the one-shot kernel
    # call, because a rejected registration still consumes an activation window.
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or not isinstance(manifest.get("run"), dict)
        or not isinstance(manifest.get("source_items"), list)
        or not isinstance(manifest.get("decisions"), list)
        or manifest["run"].get("run_id") != run_text
    ):
        raise MigrationRegistrarError("identity migration registration is invalid")
    return RegistrationRequest(uuid.UUID(run_text), manifest_bytes.decode("utf-8"))


def read_request() -> RegistrationRequest:
    return parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))


def database_url() -> str:
    """Return the migration caller's URL, refusing every other role.

    Deliberately not shared with `identity_admission_writer.database_url`, which
    pins `home_agent_owner`. The registration kernel refuses any caller but the
    migration login, and the owner must never be able to reach it.
    """

    raw = os.environ.get("HOME_AGENT_DATABASE_URL", "")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise MigrationRegistrarError(
            "identity migration database URL is invalid"
        ) from error
    password = unquote(parsed.password or "")
    expected = (
        f"postgresql+psycopg://{CALLER_ROLE}:{password}@postgres:5432/home_agent"
    )
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.username != CALLER_ROLE
        or parsed.hostname != "postgres"
        or port != 5432
        or parsed.path != "/home_agent"
        or parsed.query
        or parsed.fragment
        or PASSWORD.fullmatch(password) is None
        or raw != expected
    ):
        raise MigrationRegistrarError("identity migration database URL is invalid")
    return raw


def _sqlstate(error: DBAPIError) -> str | None:
    original = error.orig
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


async def _execute_once(url: str, request: RegistrationRequest) -> uuid.UUID:
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
                        text(REGISTER_SQL),
                        {"manifest": request.manifest},
                    )
                ).scalar_one_or_none()
        if not isinstance(result, uuid.UUID) or result != request.run_id:
            raise MigrationRegistrarError("identity migration registration was rejected")
        return result
    finally:
        await engine.dispose()


async def execute(url: str, request: RegistrationRequest) -> uuid.UUID:
    """Register once, retrying only the whole transaction.

    The kernel requires SERIALIZABLE and holds an advisory lock, so a
    serialization failure, deadlock, or lock timeout is a retryable outcome of a
    transaction that committed nothing. Never retry a suffix.
    """

    for attempt in range(MAX_ATTEMPTS):
        try:
            return await _execute_once(url, request)
        except DBAPIError as error:
            if _sqlstate(error) not in RETRYABLE_SQLSTATES or attempt + 1 >= MAX_ATTEMPTS:
                raise MigrationRegistrarError(
                    "identity migration registration was rejected"
                ) from error
            await asyncio.sleep(0.05 * (2**attempt))
    raise AssertionError("bounded registration retry loop did not terminate")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("identity migration registrar accepts no arguments", file=sys.stderr)
        return 64
    try:
        request = read_request()
        result = asyncio.run(execute(database_url(), request))
    except MigrationRegistrarError:
        print("identity migration registration failed closed", file=sys.stderr)
        return 78
    print(
        json.dumps(
            {
                "contract": RESULT_CONTRACT,
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
