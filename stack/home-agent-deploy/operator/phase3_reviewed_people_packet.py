#!/usr/bin/env python3
"""Stage the private, contentful People review for Phase 3 activation.

This is the first E5x boundary. It reads only the existing allowlisted,
query-only legacy Identity Store projection and writes one root-owned private
review artifact. It does not sign a migration bundle, connect to PostgreSQL,
freeze Home Assistant, create a principal binding, or create relationship
facts.

The command intentionally has no path arguments. A stopped, clean SQLite copy
must be placed at ``/srv/home-agent/private/phase3-identity/identity.db``. The
review is atomically written beside it as ``people-review-e5x.json``.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping

from migrate_legacy_identity import MigrationError, MigrationPlan, load_plan


CONTRACT = "phase3-reviewed-people-private-review-e5x-v1"
PRIVATE_ROOT = Path("/srv/home-agent/private/phase3-identity")
SOURCE_PATH = PRIVATE_ROOT / "identity.db"
REVIEW_PATH = PRIVATE_ROOT / "people-review-e5x.json"
MAX_REVIEW_BYTES = 4 * 1024 * 1024


class ReviewPacketError(RuntimeError):
    """A content-free failure at the private People review boundary."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReviewPacketError("private review could not be encoded") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ReviewPacketError("private identity snapshot is unreadable") from error
    return digest.hexdigest()


def _person_private_review(plan: MigrationPlan) -> list[dict[str, Any]]:
    aliases: dict[str, list[dict[str, str]]] = defaultdict(list)
    for alias in plan.aliases:
        aliases[alias.person_id].append(
            {"alias": alias.alias, "alias_kind": alias.alias_kind}
        )
    bindings: dict[str, list[dict[str, str]]] = defaultdict(list)
    for binding in plan.external_bindings:
        bindings[binding.person_id].append(
            {
                "external_id": binding.external_id,
                "external_system": binding.external_system,
                "status": binding.status,
            }
        )
    roles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in plan.role_candidates:
        roles[role.person_id].append(
            {
                "authoritative": False,
                "candidate_id": role.candidate_id,
                "perspective": "unknown",
                "role_label": role.role_label,
                "source_field": role.source_field,
            }
        )

    people: list[dict[str, Any]] = []
    for person in sorted(plan.people, key=lambda item: item.person_id):
        suppressed = person.is_ignored or person.do_not_identify
        directives = []
        for enabled, name, expiry in (
            (person.do_not_track, "do_not_track", None),
            (person.is_silent, "silent", None),
            (person.is_ignored or person.do_not_identify, "ignored", None),
            (person.is_private, "private", None),
            (person.auto_expire_at is not None, "auto_expire", person.auto_expire_at),
        ):
            if enabled:
                directives.append({"directive": name, "expires_at": expiry})
        people.append(
            {
                "aliases": [] if suppressed else aliases[person.person_id],
                "archived": person.is_archived,
                "display_name": (
                    "[suppressed legacy identity]"
                    if suppressed
                    else person.display_name
                ),
                "legacy_role_labels": [] if suppressed else roles[person.person_id],
                "person_id": person.person_id,
                "privacy_directives": directives,
                "pronouns": None if suppressed else person.pronouns,
                "recognition_bindings": (
                    [] if suppressed else bindings[person.person_id]
                ),
                "source_version": person.source_version,
                "suppressed": suppressed,
            }
        )
    return people


def compile_private_review(
    plan: MigrationPlan,
    *,
    sqlite_snapshot_sha256: str,
) -> Mapping[str, Any]:
    if len(sqlite_snapshot_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sqlite_snapshot_sha256
    ):
        raise ReviewPacketError("private identity snapshot digest is invalid")
    people = _person_private_review(plan)
    visible_people = {
        person["person_id"]: person for person in people if not person["suppressed"]
    }
    me_candidates = sorted(
        person_id
        for person_id, person in visible_people.items()
        if any(
            label["role_label"].strip().casefold() == "me"
            for label in person["legacy_role_labels"]
        )
    )
    parent_candidates = sorted(
        person_id
        for person_id, person in visible_people.items()
        if any(
            label["role_label"].strip().casefold() == "parent"
            for label in person["legacy_role_labels"]
        )
    )
    relationship_candidates = []
    for relationship in sorted(
        plan.relationship_candidates,
        key=lambda item: (item.from_person_id, item.to_person_id, item.legacy_row_id),
    ):
        if (
            relationship.from_person_id not in visible_people
            or relationship.to_person_id not in visible_people
        ):
            continue
        relationship_candidates.append(
            {
                "authoritative": False,
                "candidate_id": relationship.candidate_id,
                "from_person_id": relationship.from_person_id,
                "perspective": "unknown",
                "relationship_label": relationship.relationship_label,
                "relationship_status": relationship.status,
                "to_person_id": relationship.to_person_id,
            }
        )

    blockers = []
    if len(me_candidates) != 1:
        blockers.append("unique_legacy_me_candidate_not_established")
    if len(parent_candidates) != 2:
        blockers.append("exactly_two_legacy_parent_candidates_not_established")
    body = {
        "contract": CONTRACT,
        "migration_semantics": {
            "authoritative_parent_facts_created": 0,
            "legacy_labels_are_perspective_unknown": True,
            "ownership_residence_presence_implied": False,
            "principal_binding_created": False,
            "semantic_people_authority_promoted": False,
        },
        "people": people,
        "relationship_candidates": relationship_candidates,
        "schema_version": plan.schema_version,
        "slice_readiness": {
            "blockers": blockers,
            "me_candidate_person_ids": me_candidates,
            "parent_candidate_person_ids": parent_candidates,
            "ready_for_private_content_review": not blockers,
        },
        "source_plan_sha256": plan.digest,
        "sqlite_snapshot_sha256": sqlite_snapshot_sha256,
    }
    private_digest = hashlib.sha256(_canonical(body)).hexdigest()
    return {
        "contract": CONTRACT,
        "private_review": body,
        "private_review_sha256": private_digest,
    }


def _safe_private_root() -> None:
    try:
        details = PRIVATE_ROOT.lstat()
    except FileNotFoundError as error:
        raise ReviewPacketError("private review directory is missing") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ReviewPacketError("private review directory is unsafe")


def _validate_source() -> None:
    _safe_private_root()
    try:
        details = SOURCE_PATH.lstat()
    except FileNotFoundError as error:
        raise ReviewPacketError("private identity snapshot is missing") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise ReviewPacketError("private identity snapshot is unsafe")


def _atomic_write_review(value: Mapping[str, Any]) -> None:
    _safe_private_root()
    try:
        REVIEW_PATH.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ReviewPacketError("private review already exists")
    raw = _canonical(value) + b"\n"
    if len(raw) > MAX_REVIEW_BYTES or b"\0" in raw:
        raise ReviewPacketError("private review is oversized")
    temporary = REVIEW_PATH.with_name(
        f".{REVIEW_PATH.name}.new.{secrets.token_hex(12)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, REVIEW_PATH)
        directory = os.open(PRIVATE_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def stage() -> Mapping[str, Any]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ReviewPacketError("private People review requires root on Linux")
    _validate_source()
    try:
        plan = load_plan(SOURCE_PATH)
    except MigrationError as error:
        raise ReviewPacketError("private identity snapshot is invalid") from error
    artifact = compile_private_review(
        plan,
        sqlite_snapshot_sha256=_sha256_file(SOURCE_PATH),
    )
    _atomic_write_review(artifact)
    review = artifact["private_review"]
    return {
        "contract": CONTRACT,
        "legacy_parent_candidate_count": len(
            review["slice_readiness"]["parent_candidate_person_ids"]
        ),
        "people_count": len(review["people"]),
        "private_review_sha256": artifact["private_review_sha256"],
        "ready_for_private_content_review": review["slice_readiness"][
            "ready_for_private_content_review"
        ],
        "status": "staged",
    }


def main() -> int:
    if len(sys.argv) != 1:
        print("private People review accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = stage()
    except ReviewPacketError:
        print("private People review failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
