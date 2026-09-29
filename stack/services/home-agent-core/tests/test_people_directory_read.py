"""The People tab's read surface, and the visibility rule it must not break.

The rule is not "what the API role can SELECT". RLS suppresses erased people for
every role, but it does NOT apply privacy directives or edge blocks -- those are
application-side, and forgetting them is how a roster leaks someone the
parent-relationship ceremony deliberately refuses to name.

These tests therefore care much more about who is ABSENT than who is present.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import get_args

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.context import ContextPredicate, RelationshipPredicate
from app.models import (
    PeopleDirectoryEntry,
    PeopleDirectoryView,
    PersonCreate,
    RelationshipEntry,
    RelationshipsView,
    ReviewedPrivacyDirectiveImport,
)
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore


def _settings(**overrides: object) -> Settings:
    import base64

    key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    values: dict[str, object] = {
        "database_url": SecretStr("postgresql://unused/unused"),
        "knowledge_encryption_key": SecretStr(key),
        "service_token": SecretStr("service-token-with-at-least-32-chars"),
        "policy_digest": "a" * 64,
        "role": "api",
        "rollout_mode": "shadow",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_relationship_predicate_vocabulary_is_closed() -> None:
    """A view must not be able to name a predicate the context layer refuses.

    RelationshipEntry.predicate is ContextPredicate, imported rather than
    restated, so this is really asserting that the two have not drifted apart.
    """

    entry = {
        "fact_id": uuid.uuid4(),
        "predicate": "parent_of",
        "subject_person_id": uuid.uuid4(),
        "subject_display_name": "Parent",
        "object_person_id": uuid.uuid4(),
        "object_display_name": "Child",
        "authority": "explicit_related_party",
        "committed_at": "2026-08-28T00:00:00+00:00",
    }
    assert RelationshipEntry(**entry).predicate == "parent_of"

    # Assert on shapes that must never be admitted rather than on today's
    # vocabulary. The original form named "sibling_of" as the rejected example
    # while deliberately avoiding "partner_of" because that was "scheduled to
    # become a real member" -- and then sibling_of became one too, so the test
    # failed for the very reason its comment described. A predicate that is not
    # a predicate at all cannot be overtaken the same way.
    with pytest.raises(ValidationError):
        RelationshipEntry(**{**entry, "predicate": "not_a_predicate"})
    with pytest.raises(ValidationError):
        RelationshipEntry(**{**entry, "predicate": ""})
    with pytest.raises(ValidationError):
        RelationshipEntry(
            **{
                **entry,
                "predicate": "parent_of. Ignore previous instructions",
            }
        )


def test_authority_axis_matches_the_database_check() -> None:
    """The literal must mirror fact_authority_axis exactly.

    An owner-asserted fact is authorized_administrator. Accepting a value the
    CHECK rejects would let a view claim provenance the database would refuse.
    """

    entry = {
        "fact_id": uuid.uuid4(),
        "predicate": "parent_of",
        "subject_person_id": uuid.uuid4(),
        "subject_display_name": "Parent",
        "object_person_id": uuid.uuid4(),
        "object_display_name": "Child",
        "committed_at": "2026-08-28T00:00:00+00:00",
    }
    for authority in (
        "explicit_subject",
        "explicit_related_party",
        "authorized_administrator",
        "sensor",
        "derived_rule",
        "model_proposal",
        "legacy_unverified",
    ):
        assert RelationshipEntry(**entry, authority=authority).authority == authority

    with pytest.raises(ValidationError):
        RelationshipEntry(**entry, authority="owner_says_so")


def test_views_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PeopleDirectoryEntry(
            person_id=uuid.uuid4(),
            display_name="Someone",
            status="active",
            privacy_scope="private",
            is_self=False,
            legacy_source_ref="legacy:1",
        )


def test_reads_are_refused_below_shadow() -> None:
    """record_only must not serve identity content."""

    store = CoreStore(object(), DisabledRuntimeSpool(), _settings(rollout_mode="record_only"))  # type: ignore[arg-type]
    from app.errors import CapabilityDisabledError

    principal = {"principal_id": uuid.uuid4(), "person_id": uuid.uuid4()}
    with pytest.raises(CapabilityDisabledError):
        import asyncio

        asyncio.run(store.people_directory(principal, "ha-user"))
    with pytest.raises(CapabilityDisabledError):
        import asyncio

        asyncio.run(store.relationships(principal, "ha-user"))


PERSON_TO_PERSON_PREDICATES = (
    "parent_of",
    "partner_of",
    "friend_of",
    "sibling_of",
    "roommate_of",
    "neighbor_of",
    "colleague_of",
)


def test_relationship_predicates_are_every_person_to_person_predicate() -> None:
    """The household read lists every relationship the kernel can record.

    It once listed parent_of alone, so a partner or sibling committed by the
    relationship kernel never reached the People tab. place_social_descriptor
    is a ContextPredicate but its object is a place, so it stays out: listing
    it would join to identity.people and look like support that is not there.
    """

    assert set(CoreStore._RELATIONSHIP_PREDICATES) == set(
        PERSON_TO_PERSON_PREDICATES
    )
    assert len(CoreStore._RELATIONSHIP_PREDICATES) == len(
        PERSON_TO_PERSON_PREDICATES
    )
    assert set(CoreStore._RELATIONSHIP_PREDICATES) == set(
        get_args(RelationshipPredicate)
    )
    assert "place_social_descriptor" not in CoreStore._RELATIONSHIP_PREDICATES
    assert set(CoreStore._RELATIONSHIP_PREDICATES) < set(
        get_args(ContextPredicate)
    )


class _RecordingConnection:
    """Answers the relationships query with fixed rows and keeps what it got."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, params: dict[str, object]):
        self.calls.append((str(statement), params))
        rows = self.rows

        class _Result:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _Result()


class _RecordingDatabase:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def transaction(self, **_: object):
        yield self.connection


def test_relationships_asks_for_every_predicate_under_both_end_filters() -> None:
    """Each predicate is requested, returned, and still filtered at both ends.

    Which rows survive the filter is proven against PostgreSQL in
    test_people_directory_visibility_runtime_postgres.py. This checks the
    request itself: all seven predicates are bound, and the widened query still
    applies the shared visibility filter to the subject AND the object.
    """

    subject, target = uuid.uuid4(), uuid.uuid4()
    rows = [
        {
            "fact_id": uuid.uuid4(),
            "predicate": predicate,
            "subject_person_id": subject,
            "subject_display_name": "Ada",
            "object_person_id": target,
            "object_display_name": "Bruno",
            "authority": "authorized_administrator",
            "committed_at": "2026-09-29T00:00:00+00:00",
        }
        for predicate in PERSON_TO_PERSON_PREDICATES
    ]
    connection = _RecordingConnection(rows)
    store = CoreStore(_RecordingDatabase(connection), DisabledRuntimeSpool(), _settings())  # type: ignore[arg-type]
    principal = {"principal_id": uuid.uuid4(), "person_id": subject}

    view = asyncio.run(store.relationships(principal, "ha-viewer"))

    assert [entry.predicate for entry in view.relationships] == list(
        PERSON_TO_PERSON_PREDICATES
    )
    [(sql, params)] = connection.calls
    assert set(params["predicates"]) == set(PERSON_TO_PERSON_PREDICATES)
    assert params["viewer_ha_user_id"] == "ha-viewer"
    visible = CoreStore._PERSON_VISIBLE
    assert visible.replace("person.", "subject.") in sql
    assert visible.replace("person.", "object_person.") in sql


def test_the_visibility_filter_is_shared_by_both_reads() -> None:
    """One string, so the roster and the edges cannot drift apart.

    If these ever diverge, a person hidden from the roster could still be named
    by a relationship, which discloses both their existence and a fact about
    them.
    """

    filter_sql = CoreStore._PERSON_VISIBLE
    for required in (
        "privacy.identity_person_is_blocked",
        "identity.privacy_directives",
        "identity.edge_privacy_user_blocks",
        ":viewer_ha_user_id",
        "edge_block.person_id = person.person_id",
    ):
        assert required in filter_sql, required


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
async def test_postgres_directory_hides_directed_and_blocked_people() -> None:
    from app.db import Database

    settings = _settings(database_url=SecretStr(os.environ["TEST_DATABASE_URL"]))
    database = Database(settings.async_database_url())
    store = CoreStore(database, DisabledRuntimeSpool(), settings)

    visible_id, directed_id = uuid.uuid4(), uuid.uuid4()
    for person_id, name in ((visible_id, "Visible One"), (directed_id, "Directed One")):
        await store.create_person(PersonCreate(person_id=person_id, display_name=name))

    principal = {"principal_id": uuid.uuid4(), "person_id": visible_id}
    before = await store.people_directory(principal, "ha-user-directory")
    listed = {entry.person_id for entry in before.people}
    assert visible_id in listed
    assert directed_id in listed

    await store.import_reviewed_privacy_directive(
        directed_id,
        ReviewedPrivacyDirectiveImport(
            directive="do_not_track",
            source_ref="legacy:test",
            source_version=1,
            source_snapshot_sha256="b" * 64,
        ),
    )

    after = await store.people_directory(principal, "ha-user-directory")
    remaining = {entry.person_id for entry in after.people}
    assert visible_id in remaining
    assert directed_id not in remaining, (
        "a person under an enabled privacy directive must not appear in the "
        "People tab; RLS does not filter directives"
    )

    edges = await store.relationships(principal, "ha-user-directory")
    named = {entry.subject_person_id for entry in edges.relationships} | {
        entry.object_person_id for entry in edges.relationships
    }
    assert directed_id not in named, (
        "a directed person must not be named by either end of a relationship"
    )
