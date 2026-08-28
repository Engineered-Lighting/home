"""Run identity.commit_owner_partner_relationship_e5k against PostgreSQL.

Every test here calls the kernel and asserts on the rows it wrote or the
error it raised. Nothing in this file reads migration source text: a static
assertion that a guard clause is still spelled correctly cannot catch a SQL
error, a constraint violation, a plpgsql logic bug, or a function that was
created under the wrong owner. The kernel has never executed in production,
so the failures those tests cannot see are exactly the ones that matter.

The final signature is 0026's seventeen parameters -- 0024's fifteen plus
``target_subject_person_id`` and ``target_predicate``. 0026 added them with
CREATE OR REPLACE, which for a changed signature is a plain CREATE, so a
fifteen-argument call is now ambiguous rather than merely stale; every call
below passes all seventeen with explicit casts so exactly one candidate can
match.

Gated on the two URLs the hosted PostgreSQL gate supplies, and skipped
entirely without them, so this never touches a production database.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ids import uuid7


OWNER_DATABASE_ENV = "TEST_PHASE3_OWNER_ATTESTED_E5N_OWNER_DATABASE_URL"
COMMITTER_DATABASE_ENV = (
    "TEST_PHASE3_OWNER_ATTESTED_E5N_COMMITTER_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"

CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_partner_relationship_kernel"
# 0026 SIGNATURE, spelled the way pg_catalog.to_regprocedure parses it.
COMMIT_FUNCTION = (
    "identity.commit_owner_partner_relationship_e5k("
    "uuid,text,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "uuid,uuid,uuid,uuid,text)"
)
# 0024's original signature. It must not survive 0026: see the surface
# assertion in test_e5k_is_reachable_by_the_committer_as_the_kernel.
SUPERSEDED_FUNCTION = (
    "identity.commit_owner_partner_relationship_e5k("
    "uuid,text,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "uuid,uuid,uuid)"
)
CONTRACT_VERSION = "owner-partner-attestation-v1"
# The central claim of the whole design. An owner assertion is recorded on
# the authority axis as an administrator's, never as a second party's: the
# second party never confirmed anything, and most household members have no
# account with which they could.
ATTESTED_AUTHORITY = "authorized_administrator"
CONFIRMED_AUTHORITY = "explicit_related_party"

# Seventeen positional arguments, every one cast, so the call resolves to
# 0026's function and not to 0024's fifteen-argument leftover.
COMMIT_SQL = text(
    "SELECT identity.commit_owner_partner_relationship_e5k("
    "CAST(:ceremony_id AS uuid),"
    "CAST(:ha_user_id AS text),"
    "CAST(:partner_person_id AS uuid),"
    "CAST(:document_digest AS text),"
    "CAST(:memory_transaction_id AS uuid),"
    "CAST(:fact_id_self AS uuid),"
    "CAST(:fact_id_partner AS uuid),"
    "CAST(:fact_version_id_self AS uuid),"
    "CAST(:fact_version_id_partner AS uuid),"
    "CAST(:support_id_self AS uuid),"
    "CAST(:support_id_partner AS uuid),"
    "CAST(:receipt_id AS uuid),"
    "CAST(:receipt_edge_id_0 AS uuid),"
    "CAST(:receipt_edge_id_1 AS uuid),"
    "CAST(:attestation_artifact_id AS uuid),"
    "CAST(:subject_person_id AS uuid),"
    "CAST(:predicate AS text)) AS receipt_id"
)

TOTALS_SQL = text(
    "SELECT "
    "(SELECT count(*) FROM knowledge.fact_versions),"
    "(SELECT count(*) FROM knowledge.fact_support),"
    "(SELECT count(*) FROM knowledge.memory_transactions),"
    "(SELECT count(*) FROM privacy.artifact_registry),"
    "(SELECT count(*) FROM operations."
    "partner_relationship_authority_receipts),"
    "(SELECT count(*) FROM operations."
    "partner_relationship_authority_receipt_edges)"
)

FACTS_SQL = text(
    "SELECT fact.fact_version_id, fact.fact_id, fact.subject_type, "
    "fact.subject_id, fact.predicate, "
    "CAST(fact.object ->> 'person_id' AS uuid) AS object_person_id, "
    "fact.perspective_principal_id, fact.authority, fact.support, "
    "fact.resolution, fact.privacy_scope, "
    "upper_inf(fact.system_range) AS system_open, "
    "upper_inf(fact.valid_range) AS valid_open "
    "FROM knowledge.fact_versions AS fact "
    "WHERE fact.memory_transaction_id = CAST(:memory_id AS uuid) "
    "ORDER BY fact.subject_id"
)

RECEIPT_SQL = text(
    "SELECT receipt.receipt_id, receipt.ceremony_id, receipt.principal_id, "
    "receipt.subject_person_id, receipt.partner_person_id, "
    "receipt.contract_version, receipt.edge_count, "
    "receipt.authority_result, receipt.document_digest, "
    "receipt.memory_transaction_id, receipt.assertion_scope, "
    "receipt.predicate, receipt.attested_at "
    "FROM operations.partner_relationship_authority_receipts AS receipt "
    "WHERE receipt.receipt_id = CAST(:receipt_id AS uuid)"
)

EDGES_SQL = text(
    "SELECT edge.ordinal, edge.fact_version_id, edge.receipt_edge_id "
    "FROM operations.partner_relationship_authority_receipt_edges AS edge "
    "WHERE edge.receipt_id = CAST(:receipt_id AS uuid) "
    "ORDER BY edge.ordinal"
)

SUPPORT_SQL = text(
    "SELECT support.support_id, support.fact_version_id, "
    "support.artifact_id, support.root_observation_id, "
    "support.dependency_domain, support.support_role "
    "FROM knowledge.fact_support AS support "
    "WHERE support.fact_version_id IN ("
    "SELECT fact.fact_version_id FROM knowledge.fact_versions AS fact "
    "WHERE fact.memory_transaction_id = CAST(:memory_id AS uuid)) "
    "ORDER BY support.fact_version_id"
)

ARTIFACT_SQL = text(
    "SELECT artifact.artifact_id, artifact.artifact_kind, artifact.store, "
    "artifact.content_sha256, artifact.owner_principal_id, "
    "artifact.retention_class, artifact.status "
    "FROM privacy.artifact_registry AS artifact "
    "WHERE artifact.artifact_id = CAST(:artifact_id AS uuid)"
)


def _configured() -> bool:
    return all(
        os.getenv(name)
        for name in (OWNER_DATABASE_ENV, COMMITTER_DATABASE_ENV)
    )


def _engine(
    environment_name: str, *, serializable: bool = False
) -> AsyncEngine:
    url = make_url(os.environ[environment_name]).set(
        drivername="postgresql+psycopg"
    )
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "hide_parameters": True,
    }
    if serializable:
        options["isolation_level"] = "SERIALIZABLE"
    return create_async_engine(url, **options)


def _sqlstate(error: BaseException) -> str | None:
    original = error.orig if isinstance(error, DBAPIError) else error
    return getattr(original, "sqlstate", None)


def _message(error: BaseException) -> str:
    return str(error.orig if isinstance(error, DBAPIError) else error)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _attestation(
    ha_user_id: str,
    partner_person_id: uuid.UUID,
    *,
    label: str,
    ceremony_id: uuid.UUID | None = None,
    document_digest: str | None = None,
    subject_person_id: uuid.UUID | None = None,
    predicate: str = "partner_of",
) -> dict[str, object]:
    """Build one complete call. Every identifier is caller-supplied."""

    return {
        "ceremony_id": uuid7() if ceremony_id is None else ceremony_id,
        "ha_user_id": ha_user_id,
        "partner_person_id": partner_person_id,
        "document_digest": (
            _digest(label) if document_digest is None else document_digest
        ),
        "memory_transaction_id": uuid7(),
        "fact_id_self": uuid7(),
        "fact_id_partner": uuid7(),
        "fact_version_id_self": uuid7(),
        "fact_version_id_partner": uuid7(),
        "support_id_self": uuid7(),
        "support_id_partner": uuid7(),
        "receipt_id": uuid7(),
        "receipt_edge_id_0": uuid7(),
        "receipt_edge_id_1": uuid7(),
        "attestation_artifact_id": uuid7(),
        "subject_person_id": subject_person_id,
        "predicate": predicate,
    }


async def _commit(engine: AsyncEngine, values: dict[str, object]) -> uuid.UUID:
    async with engine.begin() as connection:
        return (await connection.execute(COMMIT_SQL, values)).scalar_one()


async def _bound_account(
    owner: AsyncEngine,
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """The one live account holder the kernel derives its perspective from."""

    async with owner.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT binding.ha_user_id, binding.person_id, "
                    "binding.principal_id "
                    "FROM identity.ha_user_bindings AS binding "
                    "WHERE binding.revoked_at IS NULL"
                )
            )
        ).all()
    assert len(rows) == 1
    return rows[0].ha_user_id, rows[0].person_id, rows[0].principal_id


async def _new_person(owner: AsyncEngine, label: str) -> uuid.UUID:
    """An account-less household member: the ordinary case for E5k."""

    person_id = uuid7()
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO identity.people("
                "person_id,display_name,status,privacy_scope,"
                "created_at,updated_at) VALUES("
                "CAST(:person_id AS uuid),CAST(:display_name AS varchar),"
                "'active','private',transaction_timestamp(),"
                "transaction_timestamp())"
            ),
            {"person_id": person_id, "display_name": f"E5k {label} fixture"},
        )
    return person_id


async def _totals(owner: AsyncEngine) -> tuple[int, ...]:
    async with owner.connect() as connection:
        return tuple((await connection.execute(TOTALS_SQL)).one())


async def _facts(owner: AsyncEngine, memory_id: object) -> list:
    async with owner.connect() as connection:
        return (
            await connection.execute(FACTS_SQL, {"memory_id": memory_id})
        ).all()


async def _receipt(owner: AsyncEngine, receipt_id: object):
    async with owner.connect() as connection:
        return (
            await connection.execute(RECEIPT_SQL, {"receipt_id": receipt_id})
        ).one()


async def _edges(owner: AsyncEngine, receipt_id: object) -> list:
    async with owner.connect() as connection:
        return (
            await connection.execute(EDGES_SQL, {"receipt_id": receipt_id})
        ).all()


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5k_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    """Inside the gate, an unset URL is a gate defect, not a skip.

    Without this, forgetting to export either URL turns every test below
    into a green skip, which is precisely how a kernel reaches production
    having never run.
    """

    assert _configured()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_is_reachable_by_the_committer_as_the_kernel() -> None:
    """The executable preconditions every test below depends on.

    Read from the live catalog rather than from migration text, because
    what a migration says it created and what the cluster actually holds
    are different things: 0026 declares the kernel role in a constant and
    still creates the function under whoever ran alembic.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            surface = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM pg_catalog.pg_proc "
                        "AS function "
                        "JOIN pg_catalog.pg_namespace AS space "
                        "ON space.oid = function.pronamespace "
                        "WHERE space.nspname = 'identity' "
                        "AND function.proname = "
                        "'commit_owner_partner_relationship_e5k'),"
                        "pg_catalog.to_regprocedure(:superseded) IS NULL"
                    ),
                    {"superseded": SUPERSEDED_FUNCTION},
                )
            ).one()
            # 0026 added its two parameters with CREATE OR REPLACE, which for
            # a changed signature is a plain CREATE. If 0024's fifteen-
            # argument function survives alongside it, a fifteen-argument
            # call resolves to neither: PostgreSQL reports 42725, "function
            # is not unique". That is not a stale caller quietly reaching an
            # older contract, it is a caller that cannot execute at all.
            assert surface == (1, True)

            shape = (
                await connection.execute(
                    text(
                        "SELECT owner.rolname, function.prosecdef, "
                        "function.provolatile, function.pronargs, "
                        "function.pronargdefaults "
                        "FROM pg_catalog.pg_proc AS function "
                        "JOIN pg_catalog.pg_roles AS owner "
                        "ON owner.oid = function.proowner "
                        "WHERE function.oid = "
                        "pg_catalog.to_regprocedure(:function)"
                    ),
                    {"function": COMMIT_FUNCTION},
                )
            ).one()
            # SECURITY DEFINER makes current_user the function OWNER. The
            # kernel's own first guard demands current_user be the kernel
            # role, so an owner of anything else is a kernel that can only
            # ever raise owner_partner_e5k_role_invalid.
            assert shape.rolname == KERNEL_ROLE
            assert shape.prosecdef is True
            assert shape.provolatile == "v"
            assert (shape.pronargs, shape.pronargdefaults) == (17, 2)

            reach = (
                await connection.execute(
                    text(
                        "SELECT "
                        "pg_catalog.has_function_privilege("
                        ":caller,pg_catalog.to_regprocedure(:function),"
                        "'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_api',"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "NOT pg_catalog.pg_has_role(:caller,:kernel,'USAGE')"
                    ),
                    {
                        "caller": CALLER_ROLE,
                        "kernel": KERNEL_ROLE,
                        "function": COMMIT_FUNCTION,
                    },
                )
            ).one()
            # The committer may call it; the API role may not; and the
            # committer must not be able to SET ROLE into the kernel, which
            # the kernel itself refuses.
            assert reach == (True, False, True)

            writes = (
                await connection.execute(
                    text(
                        "SELECT bool_and("
                        "pg_catalog.has_any_column_privilege("
                        ":kernel,target.relation,target.privilege)) "
                        "FROM (VALUES "
                        "('identity.ha_user_bindings','SELECT'),"
                        "('identity.people','SELECT'),"
                        "('identity.privacy_directives','SELECT'),"
                        "('knowledge.fact_versions','SELECT'),"
                        "('knowledge.fact_versions','INSERT'),"
                        "('knowledge.fact_support','INSERT'),"
                        "('knowledge.memory_transactions','INSERT'),"
                        "('privacy.artifact_registry','INSERT'),"
                        "('operations."
                        "partner_relationship_authority_receipts','SELECT'),"
                        "('operations."
                        "partner_relationship_authority_receipts','INSERT'),"
                        "('operations."
                        "partner_relationship_authority_receipt_edges',"
                        "'INSERT')"
                        ") AS target(relation, privilege)"
                    ),
                    {"kernel": KERNEL_ROLE},
                )
            ).scalar_one()
            # A SECURITY DEFINER kernel runs with the definer's privileges,
            # so the definer needs every one of these or the body dies part
            # way through on a permission error rather than a guard.
            assert writes is True

            fences = (
                await connection.execute(
                    text(
                        "SELECT bool_and("
                        "pg_catalog.has_function_privilege("
                        ":kernel,target.routine,'EXECUTE')) "
                        "FROM (VALUES "
                        "('privacy.lock_identity_semantic_write_fence()'),"
                        "('privacy.identity_person_is_blocked(uuid)')"
                        ") AS target(routine)"
                    ),
                    {"kernel": KERNEL_ROLE},
                )
            ).scalar_one()
            assert fences is True

            # Both receipt tables force row-level security. A table grant
            # is therefore not enough: a policy set that only ever names
            # the owner leaves the kernel unable to insert its own receipt.
            # Any of the three ways out will do -- this asserts the kernel
            # is not shut out, not which remedy was chosen.
            admitted = (
                await connection.execute(
                    text(
                        "SELECT count(*), bool_and("
                        "(SELECT role.rolbypassrls "
                        "FROM pg_catalog.pg_roles AS role "
                        "WHERE role.rolname = :kernel) "
                        "OR NOT relation.relrowsecurity "
                        "OR (NOT relation.relforcerowsecurity "
                        "AND relation.relowner = (SELECT role.oid "
                        "FROM pg_catalog.pg_roles AS role "
                        "WHERE role.rolname = :kernel)) "
                        "OR EXISTS (SELECT 1 "
                        "FROM pg_catalog.pg_policies AS policy "
                        "WHERE policy.schemaname = space.nspname "
                        "AND policy.tablename = relation.relname "
                        "AND policy.cmd IN ('INSERT','ALL') "
                        "AND :kernel = ANY(policy.roles))) "
                        "FROM pg_catalog.pg_class AS relation "
                        "JOIN pg_catalog.pg_namespace AS space "
                        "ON space.oid = relation.relnamespace "
                        "WHERE space.nspname = 'operations' "
                        "AND relation.relname IN ("
                        "'partner_relationship_authority_receipts',"
                        "'partner_relationship_authority_receipt_edges')"
                    ),
                    {"kernel": KERNEL_ROLE},
                )
            ).one()
            assert admitted == (2, True)
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_commits_a_symmetric_owner_attested_partnership() -> None:
    """Happy path: one call, and every row it is supposed to leave behind."""

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, subject_person_id, principal_id = await _bound_account(
            owner
        )
        partner_person_id = await _new_person(owner, "partner")
        values = _attestation(
            ha_user_id, partner_person_id, label="happy-path"
        )

        receipt_id = await _commit(committer, values)
        assert receipt_id == values["receipt_id"]

        facts = await _facts(owner, values["memory_transaction_id"])
        # Two directed facts, because a partnership is symmetric and a
        # half-recorded one would satisfy uq_active_partner_relationship
        # while being false in one direction.
        assert len(facts) == 2
        assert {
            (fact.subject_id, fact.object_person_id) for fact in facts
        } == {
            (subject_person_id, partner_person_id),
            (partner_person_id, subject_person_id),
        }
        assert [fact.predicate for fact in facts] == [
            "partner_of",
            "partner_of",
        ]
        assert all(fact.system_open for fact in facts)
        assert all(fact.valid_open for fact in facts)
        assert all(fact.resolution == "accepted" for fact in facts)
        assert all(fact.subject_type == "person" for fact in facts)
        assert all(fact.privacy_scope == "private" for fact in facts)
        assert all(
            fact.perspective_principal_id == principal_id for fact in facts
        )
        # The claim the whole design rests on. Recording an owner assertion
        # as 'explicit_related_party' would forge a second-party
        # confirmation that never happened.
        assert [fact.authority for fact in facts] == [
            ATTESTED_AUTHORITY,
            ATTESTED_AUTHORITY,
        ]
        assert CONFIRMED_AUTHORITY not in {fact.authority for fact in facts}
        assert all(fact.support == "explicit_authority" for fact in facts)

        receipt = await _receipt(owner, receipt_id)
        assert receipt.ceremony_id == values["ceremony_id"]
        assert receipt.contract_version == CONTRACT_VERSION
        assert receipt.edge_count == 2
        assert receipt.assertion_scope == "self"
        assert receipt.predicate == "partner_of"
        assert receipt.authority_result == "committed"
        assert receipt.principal_id == principal_id
        assert receipt.subject_person_id == subject_person_id
        assert receipt.partner_person_id == partner_person_id
        assert receipt.document_digest == values["document_digest"]
        assert (
            receipt.memory_transaction_id == values["memory_transaction_id"]
        )

        edges = await _edges(owner, receipt_id)
        assert [edge.ordinal for edge in edges] == [0, 1]
        assert [edge.fact_version_id for edge in edges] == [
            values["fact_version_id_self"],
            values["fact_version_id_partner"],
        ]
        assert [edge.receipt_edge_id for edge in edges] == [
            values["receipt_edge_id_0"],
            values["receipt_edge_id_1"],
        ]

        async with owner.connect() as connection:
            artifact = (
                await connection.execute(
                    ARTIFACT_SQL,
                    {"artifact_id": values["attestation_artifact_id"]},
                )
            ).one()
            supports = (
                await connection.execute(
                    SUPPORT_SQL,
                    {"memory_id": values["memory_transaction_id"]},
                )
            ).all()
        assert artifact.artifact_kind == "owner_attestation"
        assert artifact.store == "postgresql"
        assert artifact.content_sha256 == values["document_digest"]
        assert artifact.owner_principal_id == principal_id
        assert artifact.status == "active"

        # One support row per fact, each rooted in the attestation artifact.
        # knowledge.fact_support.artifact_id is NOT NULL: an earlier revision
        # of this kernel passed NULL here and would have failed at runtime,
        # which no source-reading test could have noticed.
        assert len(supports) == 2
        assert all(support.artifact_id is not None for support in supports)
        assert {support.artifact_id for support in supports} == {
            values["attestation_artifact_id"]
        }
        assert {support.fact_version_id for support in supports} == {
            values["fact_version_id_self"],
            values["fact_version_id_partner"],
        }
        assert all(
            support.support_role == "attestation" for support in supports
        )
        assert all(
            support.dependency_domain == "owner_attestation"
            for support in supports
        )
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_replay_returns_the_receipt_and_writes_nothing() -> None:
    """Replay is an exact-match proof, never a repair.

    Counted globally on purpose: a replay that quietly inserted one extra
    fact, support row, artifact or edge anywhere would move one of these
    numbers, and scoping the count to the ceremony would hide it.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _, _ = await _bound_account(owner)
        partner_person_id = await _new_person(owner, "replay")
        values = _attestation(ha_user_id, partner_person_id, label="replay")

        receipt_id = await _commit(committer, values)
        committed = await _totals(owner)
        receipt = await _receipt(owner, receipt_id)

        # Byte-identical replay.
        assert await _commit(committer, values) == receipt_id
        assert await _totals(owner) == committed

        # Same ceremony and digest, entirely fresh output identifiers. The
        # kernel keys replay on the ceremony, so this must also return the
        # original receipt rather than mint a second one -- and must not
        # patch the stored row to match the new identifiers.
        rerun = _attestation(
            ha_user_id,
            partner_person_id,
            label="replay",
            ceremony_id=values["ceremony_id"],
            document_digest=values["document_digest"],
        )
        assert await _commit(committer, rerun) == receipt_id
        assert await _totals(owner) == committed

        after = await _receipt(owner, receipt_id)
        assert tuple(after) == tuple(receipt)
        assert after.attested_at == receipt.attested_at
        assert after.memory_transaction_id == values["memory_transaction_id"]

        assert len(await _facts(owner, values["memory_transaction_id"])) == 2
        assert len(await _facts(owner, rerun["memory_transaction_id"])) == 0
        assert [edge.ordinal for edge in await _edges(owner, receipt_id)] == [
            0,
            1,
        ]
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_refuses_a_second_ceremony_for_the_same_pair() -> None:
    """A new ceremony over an already-recorded pair is refused by name.

    uq_active_partner_relationship would catch one direction as a raw
    constraint violation; the kernel refuses first so the reason is
    legible and neither direction is written.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _, _ = await _bound_account(owner)
        partner_person_id = await _new_person(owner, "duplicate")
        first = _attestation(
            ha_user_id, partner_person_id, label="duplicate-first"
        )
        await _commit(committer, first)
        before = await _totals(owner)

        second = _attestation(
            ha_user_id, partner_person_id, label="duplicate-second"
        )
        assert second["ceremony_id"] != first["ceremony_id"]
        with pytest.raises(DBAPIError) as duplicate:
            await _commit(committer, second)
        assert "owner_partner_e5k_already_recorded" in _message(
            duplicate.value
        )
        assert _sqlstate(duplicate.value) == "23505"
        assert await _totals(owner) == before
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_refuses_a_reflexive_partnership() -> None:
    """Nobody is their own partner."""

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, subject_person_id, _ = await _bound_account(owner)
        before = await _totals(owner)
        with pytest.raises(DBAPIError) as reflexive:
            await _commit(
                committer,
                _attestation(
                    ha_user_id, subject_person_id, label="reflexive"
                ),
            )
        assert "owner_partner_e5k_reflexive" in _message(reflexive.value)
        assert _sqlstate(reflexive.value) == "22023"
        assert await _totals(owner) == before
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_refuses_a_non_serializable_transaction() -> None:
    """The kernel reads state it then writes against.

    Anything weaker than SERIALIZABLE lets two concurrent ceremonies both
    pass the already-recorded check, so the level is a precondition rather
    than a caller's preference.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV)
    try:
        ha_user_id, _, _ = await _bound_account(owner)
        partner_person_id = await _new_person(owner, "isolation")
        before = await _totals(owner)
        with pytest.raises(DBAPIError) as isolation:
            await _commit(
                committer,
                _attestation(
                    ha_user_id, partner_person_id, label="isolation"
                ),
            )
        assert "owner_partner_e5k_transaction_invalid" in _message(
            isolation.value
        )
        assert _sqlstate(isolation.value) == "25000"
        assert await _totals(owner) == before
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_refuses_a_caller_that_is_not_the_committer() -> None:
    """Reached with the owner credential rather than the committer's.

    The owner can execute anything, which is the point: the kernel's value
    is that it refuses a caller arriving on the wrong credential, so the
    separation survives a mistake in the application wiring.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    owner_caller = _engine(OWNER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _, _ = await _bound_account(owner)
        partner_person_id = await _new_person(owner, "role")
        before = await _totals(owner)
        with pytest.raises(DBAPIError) as role:
            await _commit(
                owner_caller,
                _attestation(ha_user_id, partner_person_id, label="role"),
            )
        assert "owner_partner_e5k_role_invalid" in _message(role.value)
        assert _sqlstate(role.value) == "42501"
        assert await _totals(owner) == before
    finally:
        await owner_caller.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_refuses_a_partner_under_a_privacy_directive() -> None:
    """A person who asked not to be tracked is not made into a fact.

    The interlock is checked after the semantic write fence, so a directive
    that lands concurrently cannot slip in behind the check.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    directive_id = uuid7()
    try:
        ha_user_id, _, _ = await _bound_account(owner)
        partner_person_id = await _new_person(owner, "directive")
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.privacy_directives("
                    "directive_id,person_id,directive,enabled,expires_at,"
                    "created_at) VALUES("
                    "CAST(:directive_id AS uuid),"
                    "CAST(:person_id AS uuid),'silent',true,NULL,"
                    "transaction_timestamp())"
                ),
                {
                    "directive_id": directive_id,
                    "person_id": partner_person_id,
                },
            )
        before = await _totals(owner)
        with pytest.raises(DBAPIError) as blocked:
            await _commit(
                committer,
                _attestation(
                    ha_user_id, partner_person_id, label="directive"
                ),
            )
        assert "owner_partner_e5k_privacy_blocked" in _message(blocked.value)
        assert _sqlstate(blocked.value) == "42501"
        assert await _totals(owner) == before
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM identity.privacy_directives "
                    "WHERE directive_id = CAST(:directive_id AS uuid)"
                ),
                {"directive_id": directive_id},
            )
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_records_a_third_party_assertion_as_third_party() -> None:
    """0026: the owner asserts about two people who are not the owner.

    A weaker claim than asserting about their own life, and the record has
    to say so. The authority axis still says who had the standing --
    'authorized_administrator' -- and assertion_scope says how close they
    stood, so a later review can find every third-party assertion with a
    WHERE clause instead of re-deriving the graph.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, bound_person_id, principal_id = await _bound_account(
            owner
        )
        subject_person_id = await _new_person(owner, "third-party-subject")
        partner_person_id = await _new_person(owner, "third-party-partner")
        assert subject_person_id != bound_person_id
        assert partner_person_id != bound_person_id

        values = _attestation(
            ha_user_id,
            partner_person_id,
            label="third-party",
            subject_person_id=subject_person_id,
        )
        receipt_id = await _commit(committer, values)

        receipt = await _receipt(owner, receipt_id)
        assert receipt.assertion_scope == "third_party"
        assert receipt.subject_person_id == subject_person_id
        assert receipt.partner_person_id == partner_person_id
        assert receipt.predicate == "partner_of"
        assert receipt.edge_count == 2
        assert receipt.contract_version == CONTRACT_VERSION
        # The attester's principal, not either endpoint's: this is the
        # owner's belief about the household, not an impersonal truth.
        assert receipt.principal_id == principal_id

        facts = await _facts(owner, values["memory_transaction_id"])
        assert len(facts) == 2
        assert {
            (fact.subject_id, fact.object_person_id) for fact in facts
        } == {
            (subject_person_id, partner_person_id),
            (partner_person_id, subject_person_id),
        }
        # Scope changes; authority does not.
        assert [fact.authority for fact in facts] == [
            ATTESTED_AUTHORITY,
            ATTESTED_AUTHORITY,
        ]
        assert all(
            fact.perspective_principal_id == principal_id for fact in facts
        )
        assert all(fact.system_open for fact in facts)
        assert [edge.ordinal for edge in await _edges(owner, receipt_id)] == [
            0,
            1,
        ]
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5k URLs are not configured")
async def test_e5k_writes_parent_of_in_one_direction_only() -> None:
    """parent_of is asymmetric, and the inverse must not be written.

    Writing both directions the way partner_of does would assert that a
    child is a parent of their parent. The receipt's edge_count carries the
    same claim, so both are checked.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _, principal_id = await _bound_account(owner)
        parent_person_id = await _new_person(owner, "parent")
        child_person_id = await _new_person(owner, "child")

        values = _attestation(
            ha_user_id,
            child_person_id,
            label="parent-of",
            subject_person_id=parent_person_id,
            predicate="parent_of",
        )
        receipt_id = await _commit(committer, values)

        facts = await _facts(owner, values["memory_transaction_id"])
        assert len(facts) == 1
        assert facts[0].predicate == "parent_of"
        assert facts[0].subject_id == parent_person_id
        assert facts[0].object_person_id == child_person_id
        assert facts[0].authority == ATTESTED_AUTHORITY
        assert facts[0].fact_version_id == values["fact_version_id_self"]
        assert facts[0].fact_id == values["fact_id_self"]
        assert facts[0].system_open

        # The second set of identifiers must go unused rather than be spent
        # on an inverted fact.
        async with owner.connect() as connection:
            unused = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE fact_version_id = "
                        "CAST(:fact_version_id_partner AS uuid)),"
                        "(SELECT count(*) FROM knowledge.fact_support "
                        "WHERE support_id = "
                        "CAST(:support_id_partner AS uuid)),"
                        "(SELECT count(*) FROM operations."
                        "partner_relationship_authority_receipt_edges "
                        "WHERE receipt_edge_id = "
                        "CAST(:receipt_edge_id_1 AS uuid))"
                    ),
                    {
                        "fact_version_id_partner": (
                            values["fact_version_id_partner"]
                        ),
                        "support_id_partner": values["support_id_partner"],
                        "receipt_edge_id_1": values["receipt_edge_id_1"],
                    },
                )
            ).one()
            inverse = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge.fact_versions "
                        "AS fact WHERE fact.predicate = 'parent_of' "
                        "AND fact.subject_id = "
                        "CAST(:child_person_id AS uuid) "
                        "AND CAST(fact.object ->> 'person_id' AS uuid) = "
                        "CAST(:parent_person_id AS uuid)"
                    ),
                    {
                        "child_person_id": child_person_id,
                        "parent_person_id": parent_person_id,
                    },
                )
            ).scalar_one()
            supports = (
                await connection.execute(
                    SUPPORT_SQL,
                    {"memory_id": values["memory_transaction_id"]},
                )
            ).all()
        assert unused == (0, 0, 0)
        assert inverse == 0
        assert len(supports) == 1
        assert supports[0].artifact_id == values["attestation_artifact_id"]

        receipt = await _receipt(owner, receipt_id)
        assert receipt.edge_count == 1
        assert receipt.predicate == "parent_of"
        assert receipt.assertion_scope == "third_party"
        assert receipt.subject_person_id == parent_person_id
        assert receipt.partner_person_id == child_person_id
        assert receipt.principal_id == principal_id

        edges = await _edges(owner, receipt_id)
        assert [edge.ordinal for edge in edges] == [0]
        assert edges[0].fact_version_id == values["fact_version_id_self"]
        assert edges[0].receipt_edge_id == values["receipt_edge_id_0"]
    finally:
        await committer.dispose()
        await owner.dispose()
