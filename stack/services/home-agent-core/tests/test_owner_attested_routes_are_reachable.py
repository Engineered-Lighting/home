"""A route pinned to a revision the settings cannot hold is dead code.

Owner-attested routes now use an explicit reviewed revision matrix. Historical
labels remain descriptive; executable route coverage verifies effective gates.

Both were shipped that way. These tests exist so the next route cannot be.
"""

from __future__ import annotations

import pathlib
import re

from app.api import OWNER_PARTNER_ADAPTER_REVISION, OWNER_PERSON_ADAPTER_REVISION
from app.config import ReadinessMigration
from app.identity_capabilities import supports_identity_capability

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _members() -> tuple[str, ...]:
    return tuple(ReadinessMigration.__args__)  # type: ignore[attr-defined]


def test_every_pinned_route_revision_is_a_settable_value() -> None:
    members = _members()
    for name, revision in (
        ("OWNER_PARTNER_ADAPTER_REVISION", OWNER_PARTNER_ADAPTER_REVISION),
        ("OWNER_PERSON_ADAPTER_REVISION", OWNER_PERSON_ADAPTER_REVISION),
    ):
        assert revision in members, (
            f"{name}={revision!r} is not a ReadinessMigration member, so the "
            "route it gates can never be reached"
        )


def test_routes_that_must_be_live_together_share_a_revision() -> None:
    """readiness_migration holds ONE value.

    Two routes pinned to different revisions can never both be active, so any
    deployment satisfies at most one of them. The owner-attested surface is
    meant to be usable as a whole.
    """

    assert OWNER_PARTNER_ADAPTER_REVISION == OWNER_PERSON_ADAPTER_REVISION
    expected = {"0030_relationship_vocabulary_e5q", "0031_relationship_uniqueness_e5r"}
    for revision in _members():
        for capability in ("owner_person_creation", "owner_relationship_attestation"):
            assert supports_identity_capability(revision, capability) is (revision in expected)


def test_the_pinned_revision_has_a_migration_behind_it() -> None:
    """Pinning to a revision no migration produces would be unreachable in a
    different way: the database could never report it."""

    versions = APP.parent / "alembic" / "versions"
    revisions = {
        line.split('"')[1]
        for path in versions.glob("*.py")
        for line in path.read_text().splitlines()
        if re.match(r"^revision(: str)? = ", line)
    }
    assert OWNER_PERSON_ADAPTER_REVISION in revisions


def test_every_readiness_member_is_a_real_revision_or_the_baseline() -> None:
    """A member with no migration behind it is a value that can be configured
    but never satisfied."""

    versions = APP.parent / "alembic" / "versions"
    revisions = {
        line.split('"')[1]
        for path in versions.glob("*.py")
        for line in path.read_text().splitlines()
        if re.match(r"^revision(: str)? = ", line)
    }
    missing = [m for m in _members() if m not in revisions]
    assert not missing, f"ReadinessMigration members with no migration: {missing}"
