"""The People tab's read path, executed against a real PostgreSQL database.

``tests/test_people_directory_read.py`` proves the shapes: that the view models
refuse an unknown predicate, that both reads share one filter string, that
record_only refuses to serve identity content. Every one of those assertions
holds against a database that has never been opened. The single PostgreSQL test
in that file is gated on ``TEST_DATABASE_URL``, which nothing sets, so the
visibility rule itself has never run.

This file runs it. It builds a real ``CoreStore`` on the gate's owner
credential, seeds a household, and calls ``people_directory`` and
``relationships``. It cares far more about who is ABSENT than who is present.

Why the absence arms are the whole point
----------------------------------------
Row-level security suppresses erased people for every application role, so the
``status = 'active'`` arm of ``CoreStore._PERSON_VISIBLE``
(app/store.py:3333-3348; the status arm is line 3334)
is defence in depth. The other two arms are NOT covered by RLS:

  * ``identity.privacy_directives``  (app/store.py:3336-3342)
  * ``identity.edge_privacy_user_blocks``  (app/store.py:3343-3348)

Those are application-side, they exist only in this one Python string, and they
must stay literally identical to the rule the parent-relationship ceremony
applies when it picks candidates
(alembic/versions/0019_parent_relationship_stage_e5e.py:339-351). A person the
ceremony refuses to name is a person the tab must not list. Forgetting either
arm is how a roster leaks someone the ceremony deliberately refuses to name --
and no static test of the string can tell you the SQL it produces actually
selects the right rows.

Credential note, stated plainly
-------------------------------
The gate supplies an owner URL and a committer URL. ``CoreStore`` is built on
the owner credential because the committer is table-blind (asserted below) and
no API-role URL is published to this module. ``home_agent_owner`` matches the
PERMISSIVE ``identity_people_e2_acl_preservation`` policy (USING true) and is
outside the role list of the RESTRICTIVE ``identity_people_e2_identity_suppression``
policy, so under this credential RLS does not suppress anything. That is a
feature for this file: what remains is exactly the application-side filter, with
no RLS backstop able to mask a hole in it. It is also a limitation -- these
tests cannot prove the RLS erasure arm, and do not claim to.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from psycopg.types.range import Range
from pydantic import SecretStr
from sqlalchemy import delete, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import schema
from app.config import Settings
from app.db import Database
from app.ids import uuid7
from app.models import PersonCreate, ReviewedPrivacyDirectiveImport
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore


OWNER_DATABASE_ENV = "TEST_PHASE3_OWNER_ATTESTED_E5N_OWNER_DATABASE_URL"
COMMITTER_DATABASE_ENV = "TEST_PHASE3_OWNER_ATTESTED_E5N_COMMITTER_DATABASE_URL"
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
EXPECTED_REVISION = "0027_owner_person_e5n"

# Every table the People tab reads. The committer credential must reach none of
# them: the read path is an API-role surface, and a credential that exists to
# call commit kernels must not be able to enumerate the household.
PEOPLE_TAB_TABLES = (
    "identity.people",
    "identity.privacy_directives",
    "identity.edge_privacy_user_blocks",
    "knowledge.fact_versions",
)


def _configured() -> bool:
    return all(
        os.getenv(name)
        for name in (OWNER_DATABASE_ENV, COMMITTER_DATABASE_ENV)
    )


def _engine(environment_name: str) -> AsyncEngine:
    url = make_url(os.environ[environment_name]).set(
        drivername="postgresql+psycopg"
    )
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True)


def _sqlstate(error: BaseException) -> str | None:
    original = error.orig if isinstance(error, DBAPIError) else error
    return getattr(original, "sqlstate", None)


def _settings(url: str) -> Settings:
    """Settings for the API role at shadow, which is what serves the tab.

    ``semantic_people_read`` is refused below shadow (app/store.py:3366,
    app/store.py:3415); ``test_people_directory_read.py`` already covers the
    record_only refusal, so this module runs at the mode where the reads are
    permitted and exercises the filter instead.
    """

    import base64

    key = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    return Settings(  # type: ignore[arg-type]
        database_url=SecretStr(url),
        knowledge_encryption_key=SecretStr(key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="shadow",
    )


@dataclass
class Household:
    """One disposable household, plus the viewer reading it.

    ``viewer_ha_user_id`` is deliberately not derived from
    ``identity.ha_user_bindings``: ``people_directory`` takes it from the
    authenticated header precisely because the API role cannot read that table
    (app/store.py:3360-3364), and a test that derived it would be testing a
    path production does not take.
    """

    principal: dict[str, Any]
    viewer_ha_user_id: str
    control_ha_user_id: str
    block_holder_ha_user_id: str
    self_person_id: uuid.UUID
    peer_person_id: uuid.UUID
    directed_person_id: uuid.UUID
    blocked_person_id: uuid.UUID
    display_names: dict[uuid.UUID, str]
    transaction_id: uuid.UUID
    edge_peer_parent_of_self: uuid.UUID
    edge_directed_is_subject: uuid.UUID
    edge_directed_is_object: uuid.UUID
    edge_blocked_is_subject: uuid.UUID
    descriptor_fact_id: uuid.UUID
    authorities: dict[uuid.UUID, str] = field(default_factory=dict)

    @property
    def person_ids(self) -> list[uuid.UUID]:
        return [
            self.self_person_id,
            self.peer_person_id,
            self.directed_person_id,
            self.blocked_person_id,
        ]

    @property
    def parent_fact_ids(self) -> set[uuid.UUID]:
        return {
            self.edge_peer_parent_of_self,
            self.edge_directed_is_subject,
            self.edge_directed_is_object,
            self.edge_blocked_is_subject,
        }


def _fact(
    *,
    fact_id: uuid.UUID,
    subject_id: uuid.UUID,
    predicate: str,
    object_person_id: uuid.UUID,
    principal_id: uuid.UUID,
    transaction_id: uuid.UUID,
    authority: str,
    now: datetime,
) -> dict[str, Any]:
    return {
        "fact_version_id": uuid7(),
        "fact_id": fact_id,
        "version": 1,
        "subject_type": "person",
        "subject_id": subject_id,
        "predicate": predicate,
        "object": {"person_id": str(object_person_id)},
        # knowledge.fact_versions carries a PERMISSIVE policy keyed on
        # app.principal_id, so the perspective must be the viewer's principal
        # or the read transaction could not see the row at all.
        "perspective_principal_id": principal_id,
        "valid_range": Range(now, None, bounds="[)"),
        "system_range": Range(now, None, bounds="[)"),
        "authority": authority,
        "support": "explicit_authority",
        "contradiction": "none",
        "freshness": "not_applicable",
        "coverage": "not_applicable",
        "resolution": "accepted",
        "privacy_scope": "private",
        "memory_transaction_id": transaction_id,
    }


@asynccontextmanager
async def _seeded_household(store: CoreStore) -> AsyncIterator[Household]:
    """Seed four people, one principal, and five facts; remove them after.

    People are created through ``CoreStore.create_person`` rather than by raw
    insert, so the rows are exactly the rows production writes.
    """

    token = uuid.uuid4().hex[:8]
    principal_id = uuid7()
    people = {
        "self": (uuid7(), f"Directory Ada {token}"),
        "peer": (uuid7(), f"Directory Bruno {token}"),
        "directed": (uuid7(), f"Directory Cleo {token}"),
        "blocked": (uuid7(), f"Directory Dara {token}"),
    }
    household = Household(
        principal={
            "principal_id": principal_id,
            "person_id": people["self"][0],
        },
        viewer_ha_user_id=f"ha-viewer-{token}",
        control_ha_user_id=f"ha-control-{token}",
        block_holder_ha_user_id=f"ha-holder-{token}",
        self_person_id=people["self"][0],
        peer_person_id=people["peer"][0],
        directed_person_id=people["directed"][0],
        blocked_person_id=people["blocked"][0],
        display_names={
            person_id: name for person_id, name in people.values()
        },
        transaction_id=uuid7(),
        edge_peer_parent_of_self=uuid7(),
        edge_directed_is_subject=uuid7(),
        edge_directed_is_object=uuid7(),
        edge_blocked_is_subject=uuid7(),
        descriptor_fact_id=uuid7(),
    )
    try:
        # Distinct pronouns and privacy scopes so the projection cannot pass by
        # returning a constant.
        await store.create_person(
            PersonCreate(
                person_id=household.self_person_id,
                display_name=household.display_names[household.self_person_id],
                pronouns="they/them",
                privacy_scope="household",
            )
        )
        for key in ("peer", "directed", "blocked"):
            person_id, display_name = people[key]
            await store.create_person(
                PersonCreate(
                    person_id=person_id,
                    display_name=display_name,
                    privacy_scope="private",
                )
            )

        now = datetime.now(UTC)
        household.authorities = {
            household.edge_peer_parent_of_self: "explicit_related_party",
            household.edge_directed_is_subject: "authorized_administrator",
            household.edge_directed_is_object: "explicit_subject",
            household.edge_blocked_is_subject: "legacy_unverified",
        }
        async with store.database.transaction(
            principal_id=principal_id, serializable=True
        ) as connection:
            await connection.execute(
                insert(schema.principals).values(
                    principal_id=principal_id,
                    person_id=household.self_person_id,
                    kind="ha_user",
                    display_label=f"Directory viewer {token}",
                    status="active",
                )
            )
            await connection.execute(
                insert(schema.memory_transactions).values(
                    transaction_id=household.transaction_id,
                    principal_id=principal_id,
                    kind="test_fixture_people_directory_visibility",
                    state="committed",
                    candidate={"fixture": token},
                    preview={"fixture": token},
                    verifier_results=[
                        {
                            "rule": "test_fixture_only",
                            "outcome": "pass",
                            "reason_code": "not_product_authority",
                        }
                    ],
                    policy_version=store.settings.policy_version,
                    policy_digest=store.settings.policy_digest,
                )
            )
            seeded_facts = [
                _fact(
                    fact_id=household.edge_peer_parent_of_self,
                    subject_id=household.peer_person_id,
                    predicate="parent_of",
                    object_person_id=household.self_person_id,
                    principal_id=principal_id,
                    transaction_id=household.transaction_id,
                    authority=household.authorities[
                        household.edge_peer_parent_of_self
                    ],
                    now=now,
                ),
                # The directed person on the SUBJECT side.
                _fact(
                    fact_id=household.edge_directed_is_subject,
                    subject_id=household.directed_person_id,
                    predicate="parent_of",
                    object_person_id=household.peer_person_id,
                    principal_id=principal_id,
                    transaction_id=household.transaction_id,
                    authority=household.authorities[
                        household.edge_directed_is_subject
                    ],
                    now=now,
                ),
                # ...and on the OBJECT side. Suppressing only the subject
                # would still disclose that the directed person exists and
                # is related, which is the disclosure the directive exists
                # to prevent.
                _fact(
                    fact_id=household.edge_directed_is_object,
                    subject_id=household.peer_person_id,
                    predicate="parent_of",
                    object_person_id=household.directed_person_id,
                    principal_id=principal_id,
                    transaction_id=household.transaction_id,
                    authority=household.authorities[
                        household.edge_directed_is_object
                    ],
                    now=now,
                ),
                _fact(
                    fact_id=household.edge_blocked_is_subject,
                    subject_id=household.blocked_person_id,
                    predicate="parent_of",
                    object_person_id=household.self_person_id,
                    principal_id=principal_id,
                    transaction_id=household.transaction_id,
                    authority=household.authorities[
                        household.edge_blocked_is_subject
                    ],
                    now=now,
                ),
                # A non-person predicate whose object nonetheless carries a
                # resolvable person_id. That is not a realistic
                # place_social_descriptor object, and it is chosen on
                # purpose: with a place-shaped object the join to
                # identity.people would drop the row and the test would
                # pass even if _RELATIONSHIP_PREDICATES were widened. Here
                # only the predicate filter can exclude it.
                _fact(
                    fact_id=household.descriptor_fact_id,
                    subject_id=household.self_person_id,
                    predicate="place_social_descriptor",
                    object_person_id=household.peer_person_id,
                    principal_id=principal_id,
                    transaction_id=household.transaction_id,
                    authority="derived_rule",
                    now=now,
                ),
            ]
            for values in seeded_facts:
                await connection.execute(
                    insert(schema.fact_versions).values(**values)
                )
        yield household
    finally:
        async with store.database.transaction(
            principal_id=principal_id
        ) as connection:
            await connection.execute(
                delete(schema.fact_versions).where(
                    schema.fact_versions.c.memory_transaction_id
                    == household.transaction_id
                )
            )
            await connection.execute(
                delete(schema.memory_transactions).where(
                    schema.memory_transactions.c.transaction_id
                    == household.transaction_id
                )
            )
            await connection.execute(
                delete(schema.privacy_directives).where(
                    schema.privacy_directives.c.person_id.in_(
                        household.person_ids
                    )
                )
            )
            await connection.execute(
                delete(schema.edge_privacy_user_blocks).where(
                    schema.edge_privacy_user_blocks.c.person_id.in_(
                        household.person_ids
                    )
                )
            )
            await connection.execute(
                delete(schema.principals).where(
                    schema.principals.c.principal_id == principal_id
                )
            )
            await connection.execute(
                delete(schema.people).where(
                    schema.people.c.person_id.in_(household.person_ids)
                )
            )


@asynccontextmanager
async def _store() -> AsyncIterator[CoreStore]:
    settings = _settings(os.environ[OWNER_DATABASE_ENV])
    database = Database(settings.async_database_url())
    try:
        yield CoreStore(database, DisabledRuntimeSpool(), settings)
    finally:
        await database.close()


async def _block_user(
    store: CoreStore,
    *,
    ha_user_id: str,
    person_id: uuid.UUID,
) -> None:
    """Insert one edge_privacy_user_blocks row.

    There is no store method that writes an arbitrary block:
    ``import_reviewed_privacy_directive`` only derives blocks from existing
    HA user bindings (app/store.py:2243-2271, the only writer). The read path
    is what is under test, so the row is seeded directly.
    """

    async with store.database.transaction(serializable=True) as connection:
        await connection.execute(
            insert(schema.edge_privacy_user_blocks).values(
                block_id=uuid7(),
                ha_user_id=ha_user_id,
                reason_code="ignored",
                person_id=person_id,
            )
        )


def _named(view: Any) -> set[uuid.UUID]:
    """Every person id either end of any returned relationship names."""

    return {entry.subject_person_id for entry in view.relationships} | {
        entry.object_person_id for entry in view.relationships
    }


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_people_directory_hosted_gate_cannot_silently_skip() -> None:
    """Inside the gate the URLs must be present.

    Without this, deleting the environment wiring would turn every test below
    into a silent skip -- exactly the failure mode that left the existing
    PostgreSQL test dormant.
    """

    assert _configured()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_people_tab_preconditions_and_committer_blindness() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one() == EXPECTED_REVISION
            # Everything _PERSON_VISIBLE names must exist, or the arms below
            # would be silently unenforced rather than failing loudly.
            present = (
                await connection.execute(
                    text(
                        "SELECT "
                        "pg_catalog.to_regclass('identity.people') IS NOT NULL,"
                        "pg_catalog.to_regclass("
                        "'identity.privacy_directives') IS NOT NULL,"
                        "pg_catalog.to_regclass("
                        "'identity.edge_privacy_user_blocks') IS NOT NULL,"
                        "pg_catalog.to_regprocedure("
                        "'privacy.identity_person_is_blocked(uuid)') "
                        "IS NOT NULL"
                    )
                )
            ).one()
            assert present == (True, True, True, True)
    finally:
        await owner.dispose()

    committer = _engine(COMMITTER_DATABASE_ENV)
    try:
        for relation in PEOPLE_TAB_TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with committer.begin() as connection:
                    await connection.execute(
                        text(f"SELECT count(*) FROM {relation}")
                    )
            assert _sqlstate(denied.value) == "42501", relation
    finally:
        await committer.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_directory_baseline_lists_the_household_and_one_self() -> None:
    async with _store() as store, _seeded_household(store) as household:
        view = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        listed = {entry.person_id: entry for entry in view.people}
        assert set(household.person_ids) <= set(listed), (
            "a freshly created, undirected, unblocked person must be listed"
        )

        # is_self is true for exactly one entry across the WHOLE directory,
        # not merely across the seeded subset.
        selves = [entry.person_id for entry in view.people if entry.is_self]
        assert selves == [household.self_person_id]

        me = listed[household.self_person_id]
        assert me.display_name == household.display_names[
            household.self_person_id
        ]
        assert me.pronouns == "they/them"
        assert me.privacy_scope == "household"
        assert me.status == "active"

        peer = listed[household.peer_person_id]
        assert peer.is_self is False
        assert peer.pronouns is None
        assert peer.privacy_scope == "private"

        # ORDER BY display_name, person_id, checked over the seeded rows only:
        # pre-existing rows are ordered by the database collation, which is not
        # Python's.
        seeded_order = [
            entry.display_name
            for entry in view.people
            if entry.person_id in set(household.person_ids)
        ]
        assert seeded_order == sorted(seeded_order)


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_an_enabled_directive_hides_the_person_and_both_edge_ends() -> None:
    async with _store() as store, _seeded_household(store) as household:
        before = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        assert household.parent_fact_ids <= {
            entry.fact_id for entry in before.relationships
        }

        await store.import_reviewed_privacy_directive(
            household.directed_person_id,
            ReviewedPrivacyDirectiveImport(
                directive="do_not_track",
                source_ref=f"test:people-directory:{household.viewer_ha_user_id}",
                source_version=1,
                source_snapshot_sha256="b" * 64,
            ),
        )

        directory = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        listed = {entry.person_id for entry in directory.people}
        assert household.directed_person_id not in listed, (
            "a person under an enabled privacy directive must not appear in "
            "the People tab; RLS does not filter directives"
        )
        assert household.self_person_id in listed
        assert household.peer_person_id in listed
        assert household.blocked_person_id in listed

        after = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        returned = {entry.fact_id for entry in after.relationships}
        assert household.directed_person_id not in _named(after), (
            "a directed person must not be named by either end of a "
            "relationship"
        )
        # Named explicitly so a regression that filters only the subject side
        # is distinguishable from one that filters neither.
        assert household.edge_directed_is_subject not in returned
        assert household.edge_directed_is_object not in returned
        assert household.edge_peer_parent_of_self in returned
        assert household.edge_blocked_is_subject in returned

        # The filter tests `directive.enabled` alone -- no directive kind, no
        # expiry (app/store.py:3336-3342, mirroring
        # 0019_parent_relationship_stage_e5e.py:341-346). A 'private'
        # directive is not a do-not-track, and must still hide the person.
        await store.import_reviewed_privacy_directive(
            household.blocked_person_id,
            ReviewedPrivacyDirectiveImport(
                directive="private",
                source_ref=f"test:people-directory:{household.viewer_ha_user_id}",
                source_version=1,
                source_snapshot_sha256="c" * 64,
            ),
        )
        widened = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        assert household.blocked_person_id not in {
            entry.person_id for entry in widened.people
        }
        widened_edges = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        assert household.blocked_person_id not in _named(widened_edges)


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_an_edge_block_naming_the_person_hides_only_that_person() -> None:
    async with _store() as store, _seeded_household(store) as household:
        # The block is held by some other HA user, so only the person_id arm of
        # the filter can match. ha_user_id is UNIQUE, so this must not be the
        # viewer's id.
        await _block_user(
            store,
            ha_user_id=household.block_holder_ha_user_id,
            person_id=household.blocked_person_id,
        )

        directory = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        listed = {entry.person_id for entry in directory.people}
        assert household.blocked_person_id not in listed
        assert household.self_person_id in listed
        assert household.peer_person_id in listed
        assert household.directed_person_id in listed

        edges = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        assert household.blocked_person_id not in _named(edges)
        assert household.edge_blocked_is_subject not in {
            entry.fact_id for entry in edges.relationships
        }
        assert household.edge_peer_parent_of_self in {
            entry.fact_id for entry in edges.relationships
        }


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_an_edge_block_naming_the_viewer_hides_everyone() -> None:
    """The arm RLS cannot cover, and the one easiest to drop.

    ``edge_block.ha_user_id = :viewer_ha_user_id`` is not a predicate about the
    listed person at all, so it matches every candidate row. A viewer who is
    blocked at the edge sees nobody -- not a filtered roster, no roster.
    """

    async with _store() as store, _seeded_household(store) as household:
        before = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        assert before.people, "precondition: the viewer can see somebody"

        await _block_user(
            store,
            ha_user_id=household.viewer_ha_user_id,
            # NOT NULL, and deliberately a person the viewer could otherwise
            # see: the row must hide everyone because of its ha_user_id, not
            # because of who it names.
            person_id=household.peer_person_id,
        )

        after = await store.people_directory(
            household.principal, household.viewer_ha_user_id
        )
        assert after.people == [], (
            "an edge block naming the viewer must empty the roster entirely, "
            "including people the block does not name"
        )
        after_edges = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        assert after_edges.relationships == []

        # ...and the emptiness is scoped to that viewer. Same principal,
        # different HA user header: the household comes back. Without this
        # control the arm above would also pass if the block had somehow wiped
        # the table.
        #
        # The two arms of the OR are independent, so the one row still hides
        # the person it NAMES for every viewer. That is the correct outcome and
        # is asserted rather than avoided: expecting the whole household back
        # here would be expecting the person_id arm to stop working.
        control = await store.people_directory(
            household.principal, household.control_ha_user_id
        )
        control_listed = {entry.person_id for entry in control.people}
        assert {
            household.self_person_id,
            household.directed_person_id,
            household.blocked_person_id,
        } <= control_listed, "the block must not follow a different viewer"
        assert household.peer_person_id not in control_listed

        control_edges = await store.relationships(
            household.principal, household.control_ha_user_id
        )
        control_facts = {entry.fact_id for entry in control_edges.relationships}
        # Only the one seeded edge with the peer at neither end survives.
        assert household.edge_blocked_is_subject in control_facts
        assert household.edge_peer_parent_of_self not in control_facts
        assert household.edge_directed_is_subject not in control_facts
        assert household.edge_directed_is_object not in control_facts
        assert household.peer_person_id not in _named(control_edges)


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_relationships_resolve_both_ends_and_preserve_authority() -> None:
    async with _store() as store, _seeded_household(store) as household:
        view = await store.relationships(
            household.principal, household.viewer_ha_user_id
        )
        seeded = {
            entry.fact_id: entry
            for entry in view.relationships
            if entry.fact_id in household.parent_fact_ids
        }
        assert set(seeded) == household.parent_fact_ids

        for fact_id, entry in seeded.items():
            assert entry.predicate == "parent_of"
            assert entry.subject_display_name == household.display_names[
                entry.subject_person_id
            ], "the subject end must resolve to the person's display name"
            assert entry.object_display_name == household.display_names[
                entry.object_person_id
            ], "the object end must resolve to the person's display name"
            # Reported, not recomputed: an owner-attested edge is
            # authorized_administrator and a legacy one is legacy_unverified,
            # and the tab must not launder either into something else.
            assert entry.authority == household.authorities[fact_id]
            assert entry.committed_at.tzinfo is not None

        edge = seeded[household.edge_peer_parent_of_self]
        assert edge.subject_person_id == household.peer_person_id
        assert edge.object_person_id == household.self_person_id
        assert edge.subject_display_name != edge.object_display_name

        # ORDER BY subject.display_name, object.display_name, over the seeded
        # rows only for the collation reason noted above.
        ordered = [
            (entry.subject_display_name, entry.object_display_name)
            for entry in view.relationships
            if entry.fact_id in household.parent_fact_ids
        ]
        assert ordered == sorted(ordered)


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_non_person_predicates_never_reach_the_relationships_view() -> None:
    """place_social_descriptor is a ContextPredicate, so nothing downstream stops it.

    ``RelationshipEntry.predicate`` is typed ``ContextPredicate``
    (app/context.py:18), which admits ``place_social_descriptor``. The model
    would therefore validate such a row happily. The only thing keeping a
    place-shaped fact out of a person-to-person view is
    ``CoreStore._RELATIONSHIP_PREDICATES`` (app/store.py:3354), and the seeded
    descriptor fact carries a resolvable person_id in its object so the join
    cannot be what excludes it.
    """

    async with _store() as store, _seeded_household(store) as household:
        for ha_user_id in (
            household.viewer_ha_user_id,
            household.control_ha_user_id,
        ):
            view = await store.relationships(household.principal, ha_user_id)
            assert household.descriptor_fact_id not in {
                entry.fact_id for entry in view.relationships
            }
            assert all(
                entry.predicate == "parent_of" for entry in view.relationships
            ), "the relationships view is person-to-person only"

        # The row really is there and really would resolve: without this the
        # assertions above could pass because the seed silently failed.
        async with store.database.transaction(
            principal_id=household.principal["principal_id"]
        ) as connection:
            resolvable = (
                await connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM knowledge.fact_versions AS fact "
                        "JOIN identity.people AS object_person "
                        "ON object_person.person_id "
                        "= (fact.object ->> 'person_id')::uuid "
                        "WHERE fact.fact_id = :fact_id "
                        "AND fact.predicate = 'place_social_descriptor' "
                        "AND upper_inf(fact.system_range) "
                        "AND fact.resolution = 'accepted'"
                    ),
                    {"fact_id": household.descriptor_fact_id},
                )
            ).scalar_one()
        assert resolvable == 1
