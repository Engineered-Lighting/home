"""Reviewed, fail-closed migration from the legacy Identity Store.

This tool deliberately reads a narrow allowlist of columns from a schema-v1
SQLite database opened read-only. It never imports directly into PostgreSQL;
all writes go through documented Core bootstrap endpoints after interactive
review and exact confirmations.

Run without arguments inside ``core-api`` so private content and credentials do
not appear in the process list. The default action is a counts-only review.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any, Callable, Iterable, Mapping, Protocol
import urllib.error
import urllib.request
import uuid


_ALLOWED_CORE_BASES = frozenset({"http://127.0.0.1:8104", "http://core-api:8104"})
SUPPORTED_SCHEMA_VERSION = 1
PERSON_ENDPOINT = "/v1/people"
ROLE_ENDPOINT = "/v1/people/legacy-role-labels"
CAPABILITIES_ENDPOINT = "/v1/operator-capabilities"
ROLLOUT_ENDPOINT = "/v1/operator-rollout"
VERIFY_PERSON_ENDPOINT = "/v1/people/verify-reviewed"
ALIAS_ENDPOINT_TEMPLATE = "/v1/people/{person_id}/aliases"
RECOGNITION_BINDING_ENDPOINT_TEMPLATE = "/v1/people/{person_id}/recognition-bindings"
PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE = "/v1/people/{person_id}/privacy-directives"
PERSON_STATUS_ENDPOINT_TEMPLATE = "/v1/people/{person_id}/status-import"
RELATIONSHIP_CANDIDATE_ENDPOINT = "/v1/people/legacy-relationship-candidates"
EXPECTED_CAPABILITY_CONTRACT: Mapping[str, Any] = {
    "contract": "legacy-identity-migration-v1",
    "audience": "operator-bootstrap",
    "person_import": {
        "method": "POST",
        "path": PERSON_ENDPOINT,
        "schema": "PersonCreate.v2",
        "source_digest_field": "legacy_source_sha256",
        "idempotency": "exact-projection-v1",
    },
    "role_import": {
        "method": "POST",
        "path": ROLE_ENDPOINT,
        "schema": "LegacyRoleImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "person_verify": {
        "method": "POST",
        "path": VERIFY_PERSON_ENDPOINT,
        "schema": "ReviewedPersonVerify.v1",
    },
    "alias_import": {
        "method": "POST",
        "path": ALIAS_ENDPOINT_TEMPLATE,
        "schema": "ReviewedAliasImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "recognition_binding_import": {
        "method": "POST",
        "path": RECOGNITION_BINDING_ENDPOINT_TEMPLATE,
        "schema": "ReviewedRecognitionBindingImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "privacy_directive_import": {
        "method": "POST",
        "path": PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE,
        "schema": "ReviewedPrivacyDirectiveImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "person_status_import": {
        "method": "POST",
        "path": PERSON_STATUS_ENDPOINT_TEMPLATE,
        "schema": "ReviewedPersonStatusImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
    "relationship_candidate_import": {
        "method": "POST",
        "path": RELATIONSHIP_CANDIDATE_ENDPOINT,
        "schema": "LegacyRelationshipCandidateImport.v1",
        "source_digest_field": "source_snapshot_sha256",
        "idempotency": "exact-projection-v1",
    },
}
SOURCE_PROJECTION_CONTRACT: Mapping[str, Any] = {
    "contract": "legacy-identity-source-projection-v1",
    "source_schema_version": SUPPORTED_SCHEMA_VERSION,
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
EXPECTED_SHADOW_ROLLOUT_CONTRACT: Mapping[str, Any] = {
    "mode": "shadow",
    "source": "deployment_policy",
    "semantic_people_writes": True,
    "persistent_memory_writes": False,
    "ingest_projection": True,
}

REQUIRED_ENDPOINTS = {
    "aliases": f"POST {ALIAS_ENDPOINT_TEMPLATE}",
    "external_bindings": f"POST {RECOGNITION_BINDING_ENDPOINT_TEMPLATE}",
    "privacy_directives": f"POST {PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE}",
    "archived_status": f"POST {PERSON_STATUS_ENDPOINT_TEMPLATE}",
    "relationship_candidates": f"POST {RELATIONSHIP_CANDIDATE_ENDPOINT}",
}

# Even if a future OpenAPI document exposes this route, legacy data can never
# call it. Parent facts require a separate, current authenticated confirmation.
PROHIBITED_MIGRATION_PATHS = frozenset({"/v1/relationships/parent-confirmations"})

_REQUIRED_TABLE_COLUMNS = {
    "schema_meta": {"key", "value"},
    "identities": {
        "uuid",
        "display_name",
        "pronouns",
        "relationship_type",
        "relationship_subrole",
        "is_private",
        "is_silent",
        "is_ignored",
        "is_archived",
        "do_not_track",
        "auto_expire_at",
        "version",
    },
    "identity_aliases": {"id", "identity_uuid", "alias", "alias_kind"},
    "enrollments": {
        "id",
        "identity_uuid",
        "frigate_person_name",
        "retired_at",
    },
    "relationships": {
        "id",
        "from_uuid",
        "to_uuid",
        "rel_type",
        "status",
    },
}

# SQLite authorizer whitelist. ORDER BY columns are included even when they are
# not returned. No prohibited table or freeform column can be read after this
# authorizer is installed.
_READABLE_COLUMNS = {
    "schema_meta": {"key", "value"},
    "identities": _REQUIRED_TABLE_COLUMNS["identities"],
    "identity_aliases": _REQUIRED_TABLE_COLUMNS["identity_aliases"],
    "enrollments": _REQUIRED_TABLE_COLUMNS["enrollments"],
    "relationships": _REQUIRED_TABLE_COLUMNS["relationships"],
}

_WRITE_ACTIONS = frozenset(
    value
    for value in (
        getattr(sqlite3, "SQLITE_INSERT", None),
        getattr(sqlite3, "SQLITE_UPDATE", None),
        getattr(sqlite3, "SQLITE_DELETE", None),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
    )
    if value is not None
)
_DISALLOWED_CONTROL_ACTIONS = frozenset(
    value
    for value in (
        getattr(sqlite3, "SQLITE_ATTACH", None),
        getattr(sqlite3, "SQLITE_DETACH", None),
        getattr(sqlite3, "SQLITE_PRAGMA", None),
    )
    if value is not None
)


class MigrationError(RuntimeError):
    """Safe operator-facing error containing no legacy row content."""


class CoreRequestError(MigrationError):
    def __init__(self, status_code: int | None, operation: str) -> None:
        self.status_code = status_code
        self.operation = operation
        code = "transport" if status_code is None else str(status_code)
        super().__init__(f"Core request failed ({code}) for {operation}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass(frozen=True, slots=True)
class PersonCandidate:
    person_id: str
    display_name: str
    pronouns: str | None
    source_version: int
    is_private: bool
    is_silent: bool
    is_ignored: bool
    is_archived: bool
    do_not_track: bool
    auto_expire_at: str | None
    do_not_identify: bool

    @property
    def blocking_requirements(self) -> tuple[str, ...]:
        requirements: list[str] = []
        if (
            self.is_private
            or self.is_silent
            or self.is_ignored
            or self.do_not_track
            or self.do_not_identify
        ):
            requirements.append("privacy_directives")
        if self.auto_expire_at is not None:
            requirements.append("privacy_directives")
        if self.is_archived:
            requirements.append("archived_status")
        return tuple(dict.fromkeys(requirements))


@dataclass(frozen=True, slots=True)
class AliasCandidate:
    person_id: str
    alias: str
    alias_kind: str


@dataclass(frozen=True, slots=True)
class ExternalBindingCandidate:
    person_id: str
    external_system: str
    external_id: str
    status: str


@dataclass(frozen=True, slots=True)
class RoleCandidate:
    person_id: str
    role_label: str
    source_field: str
    source_version: int
    snapshot_sha256: str
    candidate_id: str


@dataclass(frozen=True, slots=True)
class RelationshipCandidate:
    legacy_row_id: int
    from_person_id: str
    to_person_id: str
    relationship_label: str
    status: str
    snapshot_sha256: str
    candidate_id: str


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    schema_version: int
    people: tuple[PersonCandidate, ...]
    aliases: tuple[AliasCandidate, ...]
    external_bindings: tuple[ExternalBindingCandidate, ...]
    role_candidates: tuple[RoleCandidate, ...]
    relationship_candidates: tuple[RelationshipCandidate, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class LegacySourceInventoryRow:
    """One minimized row from the fixed legacy source projection."""

    source_table_kind: str
    row_key: Mapping[str, Any]
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ApplyOperation:
    kind: str
    candidate_id: str
    path: str
    payload: Mapping[str, Any]
    review: Mapping[str, Any]
    person_id: str
    depends_on_person: bool = False
    safety_critical: bool = False


@dataclass(frozen=True, slots=True)
class ApplyPreparation:
    operations: tuple[ApplyOperation, ...]
    blocked_people: int
    deferred_counts: Mapping[str, int]
    required_endpoints: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ApplyResult:
    attempted: int
    applied: int
    failed: int
    dependency_blocked: int


class CoreClientProtocol(Protocol):
    def require_shadow_rollout(self) -> None: ...

    def capabilities(self) -> frozenset[tuple[str, str]]: ...

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(kind: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({"kind": kind, "value": value})).hexdigest()


def _stable_uuid(value: Any, row_kind: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise MigrationError(f"invalid stable UUID in {row_kind}") from error
    return str(parsed)


def _bounded_text(
    value: Any,
    row_kind: str,
    *,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MigrationError(f"invalid text value in {row_kind}")
    normalized = value.strip()
    if not normalized and not optional:
        raise MigrationError(f"empty text value in {row_kind}")
    if len(normalized) > maximum:
        raise MigrationError(f"oversized text value in {row_kind}")
    return normalized or None


def _legacy_bool(value: Any, row_kind: str) -> bool:
    if value not in (0, 1, False, True):
        raise MigrationError(f"invalid privacy flag in {row_kind}")
    return bool(value)


def _expiry(value: Any) -> str | None:
    result = _bounded_text(value, "auto expiry", maximum=64, optional=True)
    if result is None:
        return None
    try:
        datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise MigrationError("invalid auto-expiry timestamp") from error
    return result


def _schema_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    # ``table`` comes only from the fixed module allowlist.
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _validate_schema(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        "SELECT name, type FROM sqlite_master WHERE name IN "
        "('schema_meta','identities','identity_aliases','enrollments','relationships')"
    ).fetchall()
    found = {str(row[0]): str(row[1]) for row in rows}
    if set(found) != set(_REQUIRED_TABLE_COLUMNS):
        raise MigrationError("legacy Identity Store schema is incomplete")
    if any(kind != "table" for kind in found.values()):
        raise MigrationError("legacy Identity Store objects must be tables")
    for table, required in _REQUIRED_TABLE_COLUMNS.items():
        if not required.issubset(_schema_columns(connection, table)):
            raise MigrationError(
                f"legacy Identity Store table is incompatible: {table}"
            )
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise MigrationError("legacy Identity Store schema version is missing")
    try:
        version = int(row[0])
    except (TypeError, ValueError) as error:
        raise MigrationError(
            "legacy Identity Store schema version is invalid"
        ) from error
    if version != SUPPORTED_SCHEMA_VERSION:
        raise MigrationError(
            f"unsupported legacy Identity Store schema version: {version}"
        )
    return version


def _install_authorizer(connection: sqlite3.Connection) -> None:
    def authorize(
        action: int,
        argument_one: str | None,
        argument_two: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action in _WRITE_ACTIONS or action in _DISALLOWED_CONTROL_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table = argument_one or ""
            column = argument_two or ""
            if column not in _READABLE_COLUMNS.get(table, set()):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


def _open_read_only(path: Path) -> sqlite3.Connection:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise MigrationError("legacy Identity Store path is unavailable") from error
    if not resolved.is_file():
        raise MigrationError("legacy Identity Store path is not a regular file")
    if any(Path(f"{resolved}{suffix}").exists() for suffix in ("-wal", "-journal")):
        raise MigrationError(
            "legacy Identity Store has an active journal; stop writers and checkpoint first"
        )
    try:
        connection = sqlite3.connect(
            f"{resolved.as_uri()}?mode=ro&immutable=1",
            uri=True,
            isolation_level=None,
            timeout=5,
        )
    except sqlite3.Error as error:
        raise MigrationError(
            "legacy Identity Store could not be opened read-only"
        ) from error
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def _person_candidates(connection: sqlite3.Connection) -> tuple[PersonCandidate, ...]:
    rows = connection.execute(
        "SELECT uuid, display_name, pronouns, relationship_type, "
        "relationship_subrole, is_private, is_silent, is_ignored, is_archived, "
        "do_not_track, auto_expire_at, version FROM identities ORDER BY uuid"
    ).fetchall()
    people: list[PersonCandidate] = []
    for row in rows:
        person_id = _stable_uuid(row["uuid"], "identity")
        relationship_type = _bounded_text(
            row["relationship_type"], "relationship type", maximum=64
        )
        source_version = row["version"]
        if not isinstance(source_version, int) or source_version < 0:
            raise MigrationError("invalid identity source version")
        people.append(
            PersonCandidate(
                person_id=person_id,
                display_name=str(
                    _bounded_text(row["display_name"], "identity", maximum=255)
                ),
                pronouns=_bounded_text(
                    row["pronouns"], "pronouns", maximum=64, optional=True
                ),
                source_version=source_version,
                is_private=_legacy_bool(row["is_private"], "identity"),
                is_silent=_legacy_bool(row["is_silent"], "identity"),
                is_ignored=_legacy_bool(row["is_ignored"], "identity"),
                is_archived=_legacy_bool(row["is_archived"], "identity"),
                do_not_track=_legacy_bool(row["do_not_track"], "identity"),
                auto_expire_at=_expiry(row["auto_expire_at"]),
                do_not_identify=relationship_type == "do_not_identify",
            )
        )
    return tuple(people)


def _aliases(
    connection: sqlite3.Connection,
    person_ids: set[str],
) -> tuple[AliasCandidate, ...]:
    rows = connection.execute(
        "SELECT identity_uuid, alias, alias_kind FROM identity_aliases ORDER BY id"
    ).fetchall()
    result: list[AliasCandidate] = []
    for row in rows:
        person_id = _stable_uuid(row["identity_uuid"], "alias")
        if person_id not in person_ids:
            raise MigrationError("alias references an unknown identity")
        kind = _bounded_text(row["alias_kind"], "alias kind", maximum=32)
        if kind not in {"name", "nickname", "frigate_name"}:
            raise MigrationError("unsupported legacy alias kind")
        # Recognition identifiers are imported only from reviewed enrollment
        # records into the distinct recognition-binding authority. They can
        # never become a semantic name alias by accident.
        if kind == "frigate_name":
            continue
        result.append(
            AliasCandidate(
                person_id=person_id,
                alias=str(_bounded_text(row["alias"], "alias", maximum=255)),
                alias_kind=str(kind),
            )
        )
    return tuple(result)


def _external_bindings(
    connection: sqlite3.Connection,
    person_ids: set[str],
) -> tuple[ExternalBindingCandidate, ...]:
    rows = connection.execute(
        "SELECT identity_uuid, frigate_person_name, retired_at "
        "FROM enrollments ORDER BY id"
    ).fetchall()
    result: list[ExternalBindingCandidate] = []
    for row in rows:
        person_id = _stable_uuid(row["identity_uuid"], "enrollment")
        if person_id not in person_ids:
            raise MigrationError("enrollment references an unknown identity")
        result.append(
            ExternalBindingCandidate(
                person_id=person_id,
                external_system="frigate",
                external_id=str(
                    _bounded_text(
                        row["frigate_person_name"], "Frigate binding", maximum=255
                    )
                ),
                status="retired" if row["retired_at"] is not None else "active",
            )
        )
    return tuple(result)


def _roles(
    connection: sqlite3.Connection,
    people: Mapping[str, PersonCandidate],
) -> tuple[RoleCandidate, ...]:
    rows = connection.execute(
        "SELECT uuid, relationship_type, relationship_subrole, version "
        "FROM identities ORDER BY uuid"
    ).fetchall()
    result: list[RoleCandidate] = []
    for row in rows:
        person_id = _stable_uuid(row["uuid"], "legacy role")
        if person_id not in people:
            raise MigrationError("role references an unknown identity")
        # One relationship, spread across two columns: a closed
        # `relationship_type` enum and a free-text `relationship_subrole`. It is
        # one candidate, not two -- the registration kernel accepts at most one
        # decision per kind on a source item, so emitting both makes every
        # packet containing a subrole unregistrable.
        #
        # The subrole is tried first because it is the more specific term and
        # implies its enum. Readiness detection survives that ordering: "me"
        # only ever appears as a type (and never carries a subrole), while
        # "parent" only ever appears as a subrole.
        candidates = (
            ("relationship_subrole", row["relationship_subrole"]),
            ("relationship_type", row["relationship_type"]),
        )
        for source_field, raw_label in candidates:
            label = _bounded_text(raw_label, "legacy role", maximum=64, optional=True)
            if label in (None, "unknown", "do_not_identify"):
                continue
            snapshot = {
                "person_id": person_id,
                "role_label": label,
                "source_field": source_field,
                "source_version": people[person_id].source_version,
            }
            result.append(
                RoleCandidate(
                    person_id=person_id,
                    role_label=label,
                    source_field=source_field,
                    source_version=people[person_id].source_version,
                    snapshot_sha256=hashlib.sha256(_canonical(snapshot)).hexdigest(),
                    candidate_id=_digest("legacy_role_candidate", snapshot),
                )
            )
            break
    return tuple(result)


def _relationships(
    connection: sqlite3.Connection,
    person_ids: set[str],
) -> tuple[RelationshipCandidate, ...]:
    rows = connection.execute(
        "SELECT id, from_uuid, to_uuid, rel_type, status "
        "FROM relationships ORDER BY id"
    ).fetchall()
    result: list[RelationshipCandidate] = []
    for row in rows:
        from_id = _stable_uuid(row["from_uuid"], "relationship")
        to_id = _stable_uuid(row["to_uuid"], "relationship")
        if from_id not in person_ids or to_id not in person_ids:
            raise MigrationError("relationship references an unknown identity")
        status_value = _bounded_text(row["status"], "relationship status", maximum=16)
        if status_value not in {"active", "ended", "paused"}:
            raise MigrationError("unsupported relationship status")
        label = str(_bounded_text(row["rel_type"], "relationship label", maximum=64))
        safe_snapshot = {
            "legacy_row_id": int(row["id"]),
            "from_person_id": from_id,
            "to_person_id": to_id,
            "relationship_label": label,
            "status": status_value,
        }
        result.append(
            RelationshipCandidate(
                legacy_row_id=int(row["id"]),
                from_person_id=from_id,
                to_person_id=to_id,
                relationship_label=label,
                status=str(status_value),
                snapshot_sha256=hashlib.sha256(_canonical(safe_snapshot)).hexdigest(),
                candidate_id=_digest("legacy_relationship_candidate", safe_snapshot),
            )
        )
    return tuple(result)


def _plan_material(
    schema_version: int,
    people: Iterable[PersonCandidate],
    aliases: Iterable[AliasCandidate],
    bindings: Iterable[ExternalBindingCandidate],
    roles: Iterable[RoleCandidate],
    relationships: Iterable[RelationshipCandidate],
) -> Mapping[str, Any]:
    return {
        "schema_version": schema_version,
        "people": [
            {
                "person_id": item.person_id,
                "display_name": item.display_name,
                "pronouns": item.pronouns,
                "source_version": item.source_version,
                "is_private": item.is_private,
                "is_silent": item.is_silent,
                "is_ignored": item.is_ignored,
                "is_archived": item.is_archived,
                "do_not_track": item.do_not_track,
                "auto_expire_at": item.auto_expire_at,
                "do_not_identify": item.do_not_identify,
            }
            for item in people
        ],
        "aliases": [
            {
                "person_id": item.person_id,
                "alias": item.alias,
                "alias_kind": item.alias_kind,
            }
            for item in aliases
        ],
        "external_bindings": [
            {
                "person_id": item.person_id,
                "external_system": item.external_system,
                "external_id": item.external_id,
                "status": item.status,
            }
            for item in bindings
        ],
        "role_candidates": [item.candidate_id for item in roles],
        "relationship_candidates": [item.candidate_id for item in relationships],
    }


def load_plan(path: Path) -> MigrationPlan:
    connection = _open_read_only(path)
    try:
        schema_version = _validate_schema(connection)
        _install_authorizer(connection)
        people = _person_candidates(connection)
        person_map = {item.person_id: item for item in people}
        if len(person_map) != len(people):
            raise MigrationError("duplicate stable identity UUID")
        aliases = _aliases(connection, set(person_map))
        bindings = _external_bindings(connection, set(person_map))
        roles = _roles(connection, person_map)
        relationships = _relationships(connection, set(person_map))
    except sqlite3.DatabaseError as error:
        raise MigrationError(
            "legacy Identity Store read was denied or invalid"
        ) from error
    finally:
        connection.close()
    material = _plan_material(
        schema_version, people, aliases, bindings, roles, relationships
    )
    return MigrationPlan(
        schema_version=schema_version,
        people=people,
        aliases=aliases,
        external_bindings=bindings,
        role_candidates=roles,
        relationship_candidates=relationships,
        digest=hashlib.sha256(_canonical(material)).hexdigest(),
    )


def load_source_inventory(path: Path) -> tuple[LegacySourceInventoryRow, ...]:
    """Read every selected source row through the fixed query-only allowlist.

    The returned content is private compiler input.  It includes rows that the
    semantic projection intentionally suppresses so a reviewed packet must
    record an explicit omission instead of silently losing them.
    """

    connection = _open_read_only(path)
    try:
        _validate_schema(connection)
        _install_authorizer(connection)
        queries = (
            (
                "schema_meta",
                "SELECT key, value FROM schema_meta ORDER BY key",
                lambda row: {"key": str(row["key"])},
                lambda row: {"key": str(row["key"]), "value": str(row["value"])},
            ),
            (
                "identities",
                "SELECT uuid, display_name, pronouns, relationship_type, "
                "relationship_subrole, is_private, is_silent, is_ignored, "
                "is_archived, do_not_track, auto_expire_at, version "
                "FROM identities ORDER BY uuid",
                lambda row: {"uuid": _stable_uuid(row["uuid"], "identity")},
                lambda row: {
                    "uuid": _stable_uuid(row["uuid"], "identity"),
                    "display_name": row["display_name"],
                    "pronouns": row["pronouns"],
                    "relationship_type": row["relationship_type"],
                    "relationship_subrole": row["relationship_subrole"],
                    "is_private": row["is_private"],
                    "is_silent": row["is_silent"],
                    "is_ignored": row["is_ignored"],
                    "is_archived": row["is_archived"],
                    "do_not_track": row["do_not_track"],
                    "auto_expire_at": row["auto_expire_at"],
                    "version": row["version"],
                },
            ),
            (
                "identity_aliases",
                "SELECT id, identity_uuid, alias, alias_kind "
                "FROM identity_aliases ORDER BY id",
                lambda row: {"id": int(row["id"])},
                lambda row: {
                    "id": int(row["id"]),
                    "identity_uuid": _stable_uuid(row["identity_uuid"], "alias"),
                    "alias": row["alias"],
                    "alias_kind": row["alias_kind"],
                },
            ),
            (
                "enrollments",
                "SELECT id, identity_uuid, frigate_person_name, retired_at "
                "FROM enrollments ORDER BY id",
                lambda row: {"id": int(row["id"])},
                lambda row: {
                    "id": int(row["id"]),
                    "identity_uuid": _stable_uuid(row["identity_uuid"], "enrollment"),
                    "frigate_person_name": row["frigate_person_name"],
                    "retired_at": row["retired_at"],
                },
            ),
            (
                "relationships",
                "SELECT id, from_uuid, to_uuid, rel_type, status "
                "FROM relationships ORDER BY id",
                lambda row: {"id": int(row["id"])},
                lambda row: {
                    "id": int(row["id"]),
                    "from_uuid": _stable_uuid(row["from_uuid"], "relationship"),
                    "to_uuid": _stable_uuid(row["to_uuid"], "relationship"),
                    "rel_type": row["rel_type"],
                    "status": row["status"],
                },
            ),
        )
        inventory: list[LegacySourceInventoryRow] = []
        for table, query, row_key, values in queries:
            for row in connection.execute(query).fetchall():
                inventory.append(
                    LegacySourceInventoryRow(
                        source_table_kind=table,
                        row_key=row_key(row),
                        values=values(row),
                    )
                )
    except (sqlite3.DatabaseError, TypeError, ValueError) as error:
        raise MigrationError(
            "legacy Identity Store inventory was denied or invalid"
        ) from error
    finally:
        connection.close()
    if not inventory:
        raise MigrationError("legacy Identity Store inventory is empty")
    return tuple(inventory)


def review_report(plan: MigrationPlan) -> Mapping[str, Any]:
    privacy_items = sum(
        int(item.is_private)
        + int(item.is_silent)
        + int(item.is_ignored)
        + int(item.do_not_track)
        + int(item.auto_expire_at is not None)
        + int(item.do_not_identify)
        for item in plan.people
    )
    counts = {
        "people": len(plan.people),
        "people_blocked_pending_typed_endpoint": 0,
        "aliases": len(plan.aliases),
        "privacy_suppressed_aliases": sum(
            1
            for item in plan.aliases
            if (
                next(
                    person
                    for person in plan.people
                    if person.person_id == item.person_id
                ).is_ignored
                or next(
                    person
                    for person in plan.people
                    if person.person_id == item.person_id
                ).do_not_identify
            )
        ),
        "frigate_external_bindings": len(plan.external_bindings),
        "privacy_suppressed_external_bindings": sum(
            1
            for item in plan.external_bindings
            if (
                next(
                    person
                    for person in plan.people
                    if person.person_id == item.person_id
                ).is_ignored
                or next(
                    person
                    for person in plan.people
                    if person.person_id == item.person_id
                ).do_not_identify
            )
        ),
        "privacy_directives": privacy_items,
        "archived_people": sum(item.is_archived for item in plan.people),
        "legacy_role_candidates": len(plan.role_candidates),
        "legacy_relationship_candidates": len(plan.relationship_candidates),
    }
    required: dict[str, str] = {}
    if plan.relationship_candidates:
        required["relationship_candidates"] = REQUIRED_ENDPOINTS[
            "relationship_candidates"
        ]
    return {
        "format": "home-agent-legacy-identity-review-v1",
        "schema_version": plan.schema_version,
        "plan_sha256": plan.digest,
        "counts": counts,
        "required_typed_endpoints": required,
        "content_included": False,
    }


def write_review_artifact(path: Path, report: Mapping[str, Any]) -> None:
    if os.name == "nt":
        raise MigrationError(
            "review artifacts require a POSIX host with enforceable mode 0600"
        )
    target = path.expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(target, flags, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(target, 0o600)
        if stat.S_IMODE(target.stat().st_mode) != 0o600:
            raise MigrationError("review artifact mode is not 0600")
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def _candidate_id(kind: str, value: Mapping[str, Any]) -> str:
    return _digest(kind, value)


def prepare_apply(
    plan: MigrationPlan,
    capabilities: frozenset[tuple[str, str]],
) -> ApplyPreparation:
    if ("post", PERSON_ENDPOINT) not in capabilities:
        raise MigrationError(
            f"required typed Core endpoint is absent: POST {PERSON_ENDPOINT}"
        )
    if ("field", "PersonCreate.legacy_source_sha256") not in capabilities:
        raise MigrationError(
            "Core PersonCreate lacks required exact-idempotency source digest"
        )
    if ("idempotency", "exact-projection-v1") not in capabilities:
        raise MigrationError("Core import endpoints lack exact idempotency semantics")

    required_capabilities = {
        ("post", ROLE_ENDPOINT),
        ("post", ALIAS_ENDPOINT_TEMPLATE),
        ("post", RECOGNITION_BINDING_ENDPOINT_TEMPLATE),
        ("post", PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE),
        ("post", PERSON_STATUS_ENDPOINT_TEMPLATE),
        ("post", RELATIONSHIP_CANDIDATE_ENDPOINT),
    }
    missing = sorted(required_capabilities - capabilities)
    if missing:
        raise MigrationError(f"required typed Core endpoint is absent: {missing[0]}")

    operations: list[ApplyOperation] = []
    people_by_id = {person.person_id: person for person in plan.people}
    deferred = Counter()
    required: dict[str, str] = {}

    for person in plan.people:
        suppress_identity_content = person.is_ignored or person.do_not_identify
        source_projection = {
            "person_id": person.person_id,
            "display_name": (
                "Suppressed legacy identity"
                if suppress_identity_content
                else person.display_name
            ),
            "pronouns": None if suppress_identity_content else person.pronouns,
            "source_version": person.source_version,
            "is_private": person.is_private,
            "is_silent": person.is_silent,
            "is_ignored": person.is_ignored,
            "is_archived": person.is_archived,
            "do_not_track": person.do_not_track,
            "auto_expire_at": person.auto_expire_at,
            "do_not_identify": person.do_not_identify,
        }
        payload = {
            "person_id": person.person_id,
            "display_name": source_projection["display_name"],
            "pronouns": source_projection["pronouns"],
            "privacy_scope": "private",
            "legacy_source_ref": (
                f"legacy-identity-store:v{plan.schema_version}:identity:{person.person_id}"
            ),
            "legacy_source_version": person.source_version,
            "legacy_source_sha256": hashlib.sha256(
                _canonical(source_projection)
            ).hexdigest(),
        }
        candidate_id = _candidate_id("person", payload)
        operations.append(
            ApplyOperation(
                kind="person",
                candidate_id=candidate_id,
                path=PERSON_ENDPOINT,
                payload=payload,
                review={
                    "candidate_id": candidate_id,
                    "kind": "person",
                    "person_id": person.person_id,
                    "display_name": (
                        "[suppressed]"
                        if suppress_identity_content
                        else person.display_name
                    ),
                    "pronouns": (
                        None if suppress_identity_content else person.pronouns
                    ),
                    "source_version": person.source_version,
                    "legacy_source_sha256": payload["legacy_source_sha256"],
                    "privacy_flags": {
                        "private": person.is_private,
                        "silent": person.is_silent,
                        "ignored": person.is_ignored,
                        "archived": person.is_archived,
                        "do_not_track": person.do_not_track,
                        "auto_expire_at": person.auto_expire_at,
                        "do_not_identify": person.do_not_identify,
                    },
                },
                person_id=person.person_id,
            )
        )

    # Privacy is applied before any reviewed alias or recognition binding.
    # A failed safety-critical item blocks all later operations for that person.
    for person in plan.people:
        directive_specs: list[tuple[str, str | None, list[str]]] = []
        if person.do_not_track:
            directive_specs.append(("do_not_track", None, ["do_not_track"]))
        if person.is_silent:
            directive_specs.append(("silent", None, ["is_silent"]))
        ignored_sources = []
        if person.is_ignored:
            ignored_sources.append("is_ignored")
        if person.do_not_identify:
            ignored_sources.append("relationship_type=do_not_identify")
        if ignored_sources:
            directive_specs.append(("ignored", None, ignored_sources))
        if person.is_private:
            directive_specs.append(("private", None, ["is_private"]))
        if person.auto_expire_at is not None:
            directive_specs.append(
                ("auto_expire", person.auto_expire_at, ["auto_expire_at"])
            )
        for directive, expires_at, source_flags in directive_specs:
            snapshot = {
                "person_id": person.person_id,
                "directive": directive,
                "enabled": True,
                "expires_at": expires_at,
                "source_flags": source_flags,
                "source_version": person.source_version,
            }
            snapshot_sha256 = hashlib.sha256(_canonical(snapshot)).hexdigest()
            payload = {
                "directive": directive,
                "enabled": True,
                "expires_at": expires_at,
                "source_ref": (
                    f"legacy-identity-store:v{plan.schema_version}:privacy:"
                    f"{person.person_id}:{directive}"
                ),
                "source_version": person.source_version,
                "source_snapshot_sha256": snapshot_sha256,
            }
            candidate_id = _candidate_id("privacy_directive", payload)
            operations.append(
                ApplyOperation(
                    kind="privacy_directive",
                    candidate_id=candidate_id,
                    path=PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE.format(
                        person_id=person.person_id
                    ),
                    payload=payload,
                    review={
                        "candidate_id": candidate_id,
                        "kind": "privacy_directive",
                        "person_id": person.person_id,
                        "directive": directive,
                        "expires_at": expires_at,
                        "source_flags": source_flags,
                        "source_snapshot_sha256": snapshot_sha256,
                    },
                    person_id=person.person_id,
                    depends_on_person=True,
                    safety_critical=True,
                )
            )
        if person.is_archived:
            snapshot = {
                "person_id": person.person_id,
                "status": "archived",
                "source_version": person.source_version,
            }
            snapshot_sha256 = hashlib.sha256(_canonical(snapshot)).hexdigest()
            payload = {
                "status": "archived",
                "source_ref": (
                    f"legacy-identity-store:v{plan.schema_version}:status:"
                    f"{person.person_id}"
                ),
                "source_version": person.source_version,
                "source_snapshot_sha256": snapshot_sha256,
            }
            candidate_id = _candidate_id("person_status", payload)
            operations.append(
                ApplyOperation(
                    kind="person_status",
                    candidate_id=candidate_id,
                    path=PERSON_STATUS_ENDPOINT_TEMPLATE.format(
                        person_id=person.person_id
                    ),
                    payload=payload,
                    review={
                        "candidate_id": candidate_id,
                        "kind": "person_status",
                        "person_id": person.person_id,
                        "status": "archived",
                        "source_snapshot_sha256": snapshot_sha256,
                    },
                    person_id=person.person_id,
                    depends_on_person=True,
                    safety_critical=True,
                )
            )

    for alias in plan.aliases:
        person = people_by_id[alias.person_id]
        if person.is_ignored or person.do_not_identify:
            continue
        snapshot = {
            "person_id": alias.person_id,
            "alias": alias.alias,
            "alias_kind": alias.alias_kind,
            "source_version": person.source_version,
        }
        snapshot_sha256 = hashlib.sha256(_canonical(snapshot)).hexdigest()
        payload = {
            "alias": alias.alias,
            "alias_kind": alias.alias_kind,
            "source_ref": (
                f"legacy-identity-store:v{plan.schema_version}:alias:"
                f"{hashlib.sha256(_canonical(snapshot)).hexdigest()[:32]}"
            ),
            "source_version": person.source_version,
            "source_snapshot_sha256": snapshot_sha256,
        }
        candidate_id = _candidate_id("alias", payload)
        operations.append(
            ApplyOperation(
                kind="alias",
                candidate_id=candidate_id,
                path=ALIAS_ENDPOINT_TEMPLATE.format(person_id=alias.person_id),
                payload=payload,
                review={
                    "candidate_id": candidate_id,
                    "kind": "alias",
                    "person_id": alias.person_id,
                    "alias": alias.alias,
                    "alias_kind": alias.alias_kind,
                    "source_snapshot_sha256": snapshot_sha256,
                },
                person_id=alias.person_id,
                depends_on_person=True,
            )
        )

    for binding in plan.external_bindings:
        person = people_by_id[binding.person_id]
        if person.is_ignored or person.do_not_identify:
            continue
        snapshot = {
            "person_id": binding.person_id,
            "external_system": binding.external_system,
            "external_id": binding.external_id,
            "status": binding.status,
            "source_version": person.source_version,
        }
        snapshot_sha256 = hashlib.sha256(_canonical(snapshot)).hexdigest()
        payload = {
            "external_system": binding.external_system,
            "external_id": binding.external_id,
            "status": binding.status,
            "source_ref": (
                f"legacy-identity-store:v{plan.schema_version}:enrollment:"
                f"{hashlib.sha256(_canonical(snapshot)).hexdigest()[:32]}"
            ),
            "source_version": person.source_version,
            "source_snapshot_sha256": snapshot_sha256,
        }
        candidate_id = _candidate_id("recognition_binding", payload)
        operations.append(
            ApplyOperation(
                kind="recognition_binding",
                candidate_id=candidate_id,
                path=RECOGNITION_BINDING_ENDPOINT_TEMPLATE.format(
                    person_id=binding.person_id
                ),
                payload=payload,
                review={
                    "candidate_id": candidate_id,
                    "kind": "recognition_binding",
                    "person_id": binding.person_id,
                    "external_system": binding.external_system,
                    "external_id": binding.external_id,
                    "status": binding.status,
                    "source_snapshot_sha256": snapshot_sha256,
                },
                person_id=binding.person_id,
                depends_on_person=True,
            )
        )

    for role in plan.role_candidates:
        person = people_by_id[role.person_id]
        if person.is_ignored or person.do_not_identify:
            continue
        payload = {
            "person_id": role.person_id,
            "role_label": role.role_label,
            "source_ref": (
                f"legacy-identity-store:v{plan.schema_version}:"
                f"{role.source_field}:{role.person_id}"
            ),
            "source_version": role.source_version,
            "source_snapshot_sha256": role.snapshot_sha256,
        }
        operations.append(
            ApplyOperation(
                kind="legacy_role_candidate",
                candidate_id=role.candidate_id,
                path=ROLE_ENDPOINT,
                payload=payload,
                review={
                    "candidate_id": role.candidate_id,
                    "kind": "legacy_role_candidate",
                    "person_id": role.person_id,
                    "role_label": role.role_label,
                    "perspective": "unknown",
                    "authoritative_relationship": False,
                },
                person_id=role.person_id,
                depends_on_person=True,
            )
        )

    for relationship in plan.relationship_candidates:
        from_person = people_by_id[relationship.from_person_id]
        to_person = people_by_id[relationship.to_person_id]
        if any(
            (
                from_person.is_ignored,
                from_person.do_not_identify,
                to_person.is_ignored,
                to_person.do_not_identify,
            )
        ):
            continue
        payload = {
            "from_person_id": relationship.from_person_id,
            "to_person_id": relationship.to_person_id,
            "relationship_label": relationship.relationship_label,
            "relationship_status": relationship.status,
            "source_ref": (
                f"legacy-identity-store:v{plan.schema_version}:relationship:"
                f"{relationship.legacy_row_id}"
            ),
            "source_snapshot_sha256": relationship.snapshot_sha256,
        }
        operations.append(
            ApplyOperation(
                kind="legacy_relationship_candidate",
                candidate_id=relationship.candidate_id,
                path=RELATIONSHIP_CANDIDATE_ENDPOINT,
                payload=payload,
                review={
                    "candidate_id": relationship.candidate_id,
                    "kind": "legacy_relationship_candidate",
                    "from_person_id": relationship.from_person_id,
                    "to_person_id": relationship.to_person_id,
                    "relationship_label": relationship.relationship_label,
                    "relationship_status": relationship.status,
                    "perspective": "unknown",
                    "authoritative_relationship": False,
                    "source_snapshot_sha256": relationship.snapshot_sha256,
                },
                person_id=relationship.from_person_id,
                depends_on_person=True,
            )
        )

    if any(item.path in PROHIBITED_MIGRATION_PATHS for item in operations):
        raise MigrationError(
            "a prohibited authoritative relationship operation was built"
        )
    return ApplyPreparation(
        operations=tuple(operations),
        blocked_people=0,
        deferred_counts={key: value for key, value in deferred.items() if value},
        required_endpoints=required,
    )


def collect_confirmations(
    plan: MigrationPlan,
    preparation: ApplyPreparation,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    phrase = f"APPLY LEGACY IDENTITY REVIEW {plan.digest}"
    output_fn(f"Type exactly: {phrase}")
    if input_fn("> ").strip() != phrase:
        raise MigrationError("overall confirmation did not match; no Core writes sent")
    for operation in preparation.operations:
        output_fn(json.dumps(operation.review, ensure_ascii=False, sort_keys=True))
        item_phrase = (
            f"APPLY REVIEWED {operation.kind.upper()} {operation.candidate_id}"
        )
        output_fn(f"Type exactly: {item_phrase}")
        if input_fn("> ").strip() != item_phrase:
            raise MigrationError("item confirmation did not match; no Core writes sent")


def execute_apply(
    preparation: ApplyPreparation,
    client: CoreClientProtocol,
) -> ApplyResult:
    applied = 0
    failed = 0
    dependency_blocked = 0
    successful_people: set[str] = set()
    unsafe_people: set[str] = set()
    for operation in preparation.operations:
        if operation.path in PROHIBITED_MIGRATION_PATHS:
            raise MigrationError("prohibited authoritative relationship endpoint")
        if operation.depends_on_person and (
            operation.person_id not in successful_people
            or operation.person_id in unsafe_people
        ):
            dependency_blocked += 1
            continue
        try:
            client.post(operation.path, operation.payload)
        except CoreRequestError:
            failed += 1
            if operation.safety_critical:
                unsafe_people.add(operation.person_id)
            continue
        applied += 1
        if operation.kind == "person":
            successful_people.add(operation.person_id)
    return ApplyResult(
        attempted=len(preparation.operations),
        applied=applied,
        failed=failed,
        dependency_blocked=dependency_blocked,
    )


def _read_secret(name: str) -> str:
    path = Path("/run/secrets", name)
    try:
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise MigrationError(f"secret file permissions are too broad: {name}")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise MigrationError(f"required secret file is unavailable: {name}") from error
    if not value:
        raise MigrationError(f"required secret file is empty: {name}")
    return value


class HttpCoreClient:
    def __init__(self) -> None:
        self._core_base = os.environ.get(
            "HOME_AGENT_MIGRATION_CORE_URL", "http://127.0.0.1:8104"
        ).rstrip("/")
        if self._core_base not in _ALLOWED_CORE_BASES:
            raise MigrationError(
                "migration Core URL is not an allowlisted local endpoint"
            )
        self._service_token = _read_secret("operator_token")
        self._bootstrap_token = _read_secret("bootstrap_token")
        # Never honor host/container proxy variables for credentialed bootstrap
        # traffic, even when the internal DNS name is not in NO_PROXY.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        data = None if body is None else _canonical(body)
        request = urllib.request.Request(
            self._core_base + path,
            data=data,
            method=method.upper(),
            headers={
                "Authorization": f"Bearer {self._service_token}",
                "X-Home-Agent-Bootstrap": self._bootstrap_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                payload = response.read(1_048_577)
                if len(payload) > 1_048_576:
                    raise CoreRequestError(response.status, f"{method} {path}")
                if not payload:
                    return {}
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise CoreRequestError(response.status, f"{method} {path}")
                return parsed
        except urllib.error.HTTPError as error:
            # Never echo a response body; validators may reflect submitted PII.
            raise CoreRequestError(error.code, f"{method} {path}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise CoreRequestError(None, f"{method} {path}") from error

    def capabilities(self) -> frozenset[tuple[str, str]]:
        document = self._request("GET", CAPABILITIES_ENDPOINT)
        if document != EXPECTED_CAPABILITY_CONTRACT:
            raise MigrationError("Core operator capability contract mismatch")
        return frozenset(
            {
                ("post", PERSON_ENDPOINT),
                ("post", ROLE_ENDPOINT),
                ("post", VERIFY_PERSON_ENDPOINT),
                ("post", ALIAS_ENDPOINT_TEMPLATE),
                ("post", RECOGNITION_BINDING_ENDPOINT_TEMPLATE),
                ("post", PRIVACY_DIRECTIVE_ENDPOINT_TEMPLATE),
                ("post", PERSON_STATUS_ENDPOINT_TEMPLATE),
                ("post", RELATIONSHIP_CANDIDATE_ENDPOINT),
                ("field", "PersonCreate.legacy_source_sha256"),
                ("idempotency", "exact-projection-v1"),
            }
        )

    def require_shadow_rollout(self) -> None:
        """Fail before confirmations unless Core exposes the exact apply mode."""

        document = self._request("GET", ROLLOUT_ENDPOINT)
        exact = (
            isinstance(document, dict)
            and set(document) == set(EXPECTED_SHADOW_ROLLOUT_CONTRACT)
            and type(document.get("mode")) is str
            and type(document.get("source")) is str
            and type(document.get("semantic_people_writes")) is bool
            and type(document.get("persistent_memory_writes")) is bool
            and type(document.get("ingest_projection")) is bool
            and document == EXPECTED_SHADOW_ROLLOUT_CONTRACT
        )
        if not exact:
            raise MigrationError(
                "reviewed apply requires the exact shadow rollout contract; "
                "no confirmations were collected and no Core writes were sent"
            )

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = path in {
            PERSON_ENDPOINT,
            ROLE_ENDPOINT,
            RELATIONSHIP_CANDIDATE_ENDPOINT,
        }
        parts = path.split("/")
        if not allowed and len(parts) == 6 and parts[:3] == ["", "v1", "people"]:
            try:
                uuid.UUID(parts[3])
            except (ValueError, AttributeError):
                allowed = False
            else:
                allowed = (
                    parts[4]
                    in {
                        "aliases",
                        "recognition-bindings",
                        "privacy-directives",
                        "status-import",
                    }
                    and parts[5] == ""
                )
        # Normal generated paths do not carry a trailing slash.
        if not allowed and len(parts) == 5 and parts[:3] == ["", "v1", "people"]:
            try:
                uuid.UUID(parts[3])
            except (ValueError, AttributeError):
                allowed = False
            else:
                allowed = parts[4] in {
                    "aliases",
                    "recognition-bindings",
                    "privacy-directives",
                    "status-import",
                }
        if not allowed:
            raise MigrationError("migration attempted a non-allowlisted Core path")
        return self._request("POST", path, body)


def _print_review(report: Mapping[str, Any]) -> None:
    print("Legacy Identity Store minimized review (no identity content shown)")
    print(f"schema_version: {report['schema_version']}")
    print(f"plan_sha256: {report['plan_sha256']}")
    for name, count in sorted(report["counts"].items()):
        print(f"{name}: {count}")
    for name, endpoint in sorted(report["required_typed_endpoints"].items()):
        print(f"required_endpoint[{name}]: {endpoint}")


def main() -> int:
    if len(sys.argv) != 1:
        raise MigrationError("command-line arguments are prohibited for this tool")
    print("Home Agent reviewed legacy Identity Store migration")
    print("No arguments are accepted; credentials are read from private files.")
    source_text = input("Legacy Identity Store SQLite path: ").strip()
    if not source_text:
        raise MigrationError("legacy Identity Store path is required")
    plan = load_plan(Path(source_text))
    report = review_report(plan)
    _print_review(report)

    if (
        input("Write a minimized mode-0600 review artifact? [y/N]: ").strip().lower()
        == "y"
    ):
        artifact_text = input("New review artifact path: ").strip()
        if not artifact_text:
            raise MigrationError("review artifact path is required")
        try:
            write_review_artifact(Path(artifact_text), report)
        except OSError as error:
            raise MigrationError(
                "review artifact could not be created safely"
            ) from error
        print("Minimized review artifact written")

    if input("Enter reviewed apply mode? [y/N]: ").strip().lower() != "y":
        print("Review complete; no Core writes sent")
        return 0

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise MigrationError("apply review requires a private interactive TTY")

    client = HttpCoreClient()
    client.require_shadow_rollout()
    preparation = prepare_apply(plan, client.capabilities())
    print(f"eligible_operations: {len(preparation.operations)}")
    print(f"blocked_people: {preparation.blocked_people}")
    for kind, count in sorted(preparation.deferred_counts.items()):
        print(f"deferred[{kind}]: {count}")
    for kind, endpoint in sorted(preparation.required_endpoints.items()):
        print(f"required_endpoint[{kind}]: {endpoint}")

    collect_confirmations(plan, preparation)
    result = execute_apply(preparation, client)
    print(f"attempted: {result.attempted}")
    print(f"applied: {result.applied}")
    print(f"failed: {result.failed}")
    print(f"dependency_blocked: {result.dependency_blocked}")
    if result.failed or result.dependency_blocked or preparation.deferred_counts:
        print("Migration remains incomplete and fail-closed")
        return 2
    print("Reviewed migration operations completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as error:
        print(f"migration blocked: {error}")
        raise SystemExit(2) from error
