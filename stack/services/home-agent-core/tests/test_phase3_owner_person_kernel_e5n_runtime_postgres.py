"""Run identity.create_owner_attested_person_e5n against a real PostgreSQL.

The kernel shipped in 0027 and has never executed: live there are zero
`owner_person_attestation` artifacts. The existing suite for it
(`test_owner_person_creation_kernel.py`) reads the migration's source text and
asserts substrings are present. That catches a deleted guard clause. It cannot
catch a missing GRANT, an RLS policy nobody wrote, a constraint violation, or a
plpgsql branch that never evaluates the way it reads.

These tests call the function and then read the rows it wrote, with the owner
credential, and assert on them.

Everything is gated on the two E5n URLs; without them the module skips. The
gate supplies a cloned throwaway database, so no cleanup is attempted, exactly
as in test_phase3_parent_relationship_commit_e5f_runtime_postgres.py.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
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

REVISION = "0030_relationship_vocabulary_e5q"
CALLER_ROLE = "home_agent_binding_committer"
# 0029 gives owner-attested person creation a role of its own. Every
# assertion below is about the kernel's own ownership, grants and
# policies, so all of them move with it -- the finalizer kernel is
# precisely the role 0029 exists to stop doing this work.
KERNEL_ROLE = "home_agent_owner_person_kernel"
# 0027_owner_person_creation_kernel.py:48-51 declares this once, so restate it
# once here too: a signature retyped per assertion drifts silently.
CREATE_FUNCTION = (
    "identity.create_owner_attested_person_e5n("
    "uuid,text,text,text,text,text,timestamptz,text,uuid,uuid,uuid)"
)

# Mirrors app/db.py:264-276 verbatim, casts included. The call site is part of
# what is under test: if a second overload of this name is ever declared, the
# way the app spells the call is what decides which one runs.
CALL_SQL = (
    "SELECT identity.create_owner_attested_person_e5n("
    ":ceremony_id,"
    "CAST(:ha_user_id AS text),"
    "CAST(:display_name AS text),"
    "CAST(:pronouns AS text),"
    "CAST(:privacy_scope AS text),"
    "CAST(:directive AS text),"
    ":directive_expires_at,"
    "CAST(:document_digest AS text),"
    ":person_id,:attestation_artifact_id,"
    ":directive_id)"
)

# The columns 0027:214-217 grants INSERT on, one at a time.
ATTESTATION_COLUMNS = (
    "artifact_id",
    "artifact_kind",
    "store",
    "external_ref",
    "content_sha256",
    "owner_principal_id",
    "retention_class",
    "status",
    "created_at",
)
# The columns the kernel reads at 0027:150-154.
BINDING_COLUMNS = ("ha_user_id", "principal_id", "person_id", "revoked_at")


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
    """The server's primary message, not SQLAlchemy's wrapper.

    Two different failures here share SQLSTATE 42501 -- the kernel's own
    `owner_person_e5n_binding_missing` and a plain `permission denied for
    table ha_user_bindings`. Only the message tells them apart, so asserting
    on the state alone would let the second masquerade as the first.
    """

    original = error.orig if isinstance(error, DBAPIError) else error
    diagnostic = getattr(original, "diag", None)
    return getattr(diagnostic, "message_primary", None) or str(original)


def _digest() -> str:
    """A 64-character lowercase hex string, the shape 0027:97 demands."""

    return secrets.token_bytes(32).hex()


def _values(ha_user_id: str, **overrides: object) -> dict[str, object]:
    person_id = uuid7()
    values: dict[str, object] = {
        "ceremony_id": uuid7(),
        "ha_user_id": ha_user_id,
        "display_name": "Nia Okafor",
        "pronouns": "she/her",
        "privacy_scope": "household",
        "directive": None,
        "directive_expires_at": None,
        "document_digest": _digest(),
        "person_id": person_id,
        "attestation_artifact_id": uuid7(),
        "directive_id": uuid7(),
    }
    values.update(overrides)
    assert values["person_id"] != values["attestation_artifact_id"]
    return values


async def _attester(owner_engine: AsyncEngine) -> tuple[str, uuid.UUID]:
    """An account the kernel will accept as the attester.

    Read with the owner credential because, live, no other role holds SELECT
    on identity.ha_user_bindings.
    """

    async with owner_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT binding.ha_user_id, binding.principal_id "
                    "FROM identity.ha_user_bindings AS binding "
                    "WHERE binding.revoked_at IS NULL "
                    "AND NOT privacy.identity_person_is_blocked("
                    "binding.person_id) "
                    "ORDER BY binding.confirmed_at "
                    "LIMIT 1"
                )
            )
        ).all()
    assert rows, "the fixture needs one live, unblocked, confirmed binding"
    return rows[0][0], rows[0][1]


async def _create(
    engine: AsyncEngine,
    values: dict[str, object],
    *,
    read_only: bool = False,
    assign_xid: bool = False,
) -> uuid.UUID:
    async with engine.begin() as connection:
        if read_only:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        if assign_xid:
            # Not a write, but it assigns the transaction id that 0027:92
            # refuses. The kernel must be the first thing in its transaction.
            await connection.execute(
                text("SELECT pg_catalog.pg_current_xact_id()")
            )
        return (
            await connection.execute(text(CALL_SQL), values)
        ).scalar_one()


async def _person_row(owner_engine: AsyncEngine, person_id: uuid.UUID):
    async with owner_engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT display_name, pronouns, status, privacy_scope, "
                    "legacy_source_ref, legacy_source_version, "
                    "legacy_source_sha256, status_source_ref, "
                    "created_at, updated_at "
                    "FROM identity.people WHERE person_id=:person_id"
                ),
                {"person_id": person_id},
            )
        ).mappings().all()


async def _counts(
    owner_engine: AsyncEngine, person_id: uuid.UUID
) -> tuple[int, ...]:
    """Everything the kernel is supposed to have decided about this person."""

    async with owner_engine.connect() as connection:
        return tuple(
            (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM identity.people "
                        "WHERE person_id=:person_id),"
                        "(SELECT count(*) FROM identity.privacy_directives "
                        "WHERE person_id=:person_id),"
                        "(SELECT count(*) FROM identity.ha_user_bindings "
                        "WHERE person_id=:person_id),"
                        "(SELECT count(*) FROM identity.principals "
                        "WHERE principal_id=:person_id)"
                    ),
                    {"person_id": person_id},
                )
            ).one()
        )


async def _totals(owner_engine: AsyncEngine) -> tuple[int, ...]:
    async with owner_engine.connect() as connection:
        return tuple(
            (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM identity.people),"
                        "(SELECT count(*) FROM identity.principals),"
                        "(SELECT count(*) FROM identity.ha_user_bindings),"
                        "(SELECT count(*) FROM identity.privacy_directives),"
                        "(SELECT count(*) FROM privacy.artifact_registry)"
                    )
                )
            ).one()
        )


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5n_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    """Inside the gate, "skipped" must not be reachable."""

    assert _configured()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_catalog_is_split_credential_and_kernel_owned() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one() == REVISION

            function = (
                await connection.execute(
                    text(
                        "SELECT owner.rolname, function.prosecdef, "
                        "function.provolatile, function.proconfig "
                        "FROM pg_catalog.pg_proc AS function "
                        "JOIN pg_catalog.pg_roles AS owner "
                        "ON owner.oid=function.proowner "
                        "WHERE function.oid="
                        "pg_catalog.to_regprocedure(:function)"
                    ),
                    {"function": CREATE_FUNCTION},
                )
            ).one()
            # SECURITY DEFINER owned by the kernel role is the whole mechanism
            # by which current_user becomes the kernel (0027:70-73, 222-224).
            assert function[0:3] == (KERNEL_ROLE, True, "v")
            assert "search_path=pg_catalog" in set(function.proconfig)

            privileges = (
                await connection.execute(
                    text(
                        "SELECT "
                        "pg_catalog.has_function_privilege("
                        ":caller,"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_api',"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_operator',"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.pg_has_role(:caller,:kernel,'SET')"
                    ),
                    {
                        "function": CREATE_FUNCTION,
                        "caller": CALLER_ROLE,
                        "kernel": KERNEL_ROLE,
                    },
                )
            ).one()
            # The committer may call it and may not become it. That last false
            # is the premise the role guard at 0027:81-83 rests on; if it ever
            # becomes true the guard stops meaning anything.
            assert privileges == (True, False, False, False)
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_kernel_can_write_the_provenance_it_is_required_to() -> None:
    """0027:206-219 widens artifact_registry to exactly nine columns.

    The migration's own comment explains why this is checked at column level:
    has_table_privilege cannot see column grants and reports the same false
    for a role that holds them and a role that does not.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            granted = (
                await connection.execute(
                    text(
                        "SELECT bool_and("
                        "pg_catalog.has_column_privilege("
                        ":kernel,'privacy.artifact_registry',"
                        "column_name,'INSERT')) "
                        "FROM unnest(CAST(:columns AS text[])) AS column_name"
                    ),
                    {
                        "kernel": KERNEL_ROLE,
                        "columns": list(ATTESTATION_COLUMNS),
                    },
                )
            ).scalar_one()
            assert granted is True

            # Scoped to those columns, not a blanket table privilege.
            assert (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.has_table_privilege("
                        ":kernel,'privacy.artifact_registry','INSERT')"
                    ),
                    {"kernel": KERNEL_ROLE},
                )
            ).scalar_one() is False
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_kernel_can_read_the_binding_it_authenticates_against(
) -> None:
    """The kernel reads identity.ha_user_bindings at 0027:150-154.

    A SECURITY DEFINER function runs as its owner, and the owner is a role
    like any other: it needs the privilege. The sibling stage kernel was given
    a column-scoped SELECT for exactly this read (0019:731-733). If this
    assertion fails, every call reaches the binding lookup and dies there with
    "permission denied for table ha_user_bindings" -- SQLSTATE 42501, the same
    state as the kernel's own binding_missing guard, which is how such a
    failure gets mistaken for a legitimate refusal.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT bool_and("
                        "pg_catalog.has_column_privilege("
                        ":kernel,'identity.ha_user_bindings',"
                        "column_name,'SELECT')) "
                        "FROM unnest(CAST(:columns AS text[])) AS column_name"
                    ),
                    {"kernel": KERNEL_ROLE, "columns": list(BINDING_COLUMNS)},
                )
            ).scalar_one() is True
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_kernel_is_admitted_by_row_level_security() -> None:
    """A grant is only half of it where FORCE ROW LEVEL SECURITY is set.

    identity.people, identity.ha_user_bindings and privacy.artifact_registry
    all force RLS, and the kernel role is neither their owner nor BYPASSRLS.
    Every other kernel that writes here was given its own policies -- the
    binding kernel's principal_binding_e5b_insert, the parent kernel's
    parent_relationship_commit_e5f_w01_insert (0019:715-731). The only
    policy on artifact_registry that reaches an unlisted role is the public
    one keyed on current_setting('app.principal_id'), and app/db.py:254-292
    sets no such GUC before calling.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            insert_policies = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_policies "
                        "WHERE schemaname='privacy' "
                        "AND tablename='artifact_registry' "
                        "AND permissive='PERMISSIVE' "
                        "AND cmd IN ('INSERT','ALL') "
                        "AND :kernel = ANY(roles)"
                    ),
                    {"kernel": KERNEL_ROLE},
                )
            ).scalar_one()
            assert insert_policies >= 1, (
                "no policy admits the kernel's attestation INSERT"
            )
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_creates_the_person_the_attestation_and_nothing_else(
) -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, principal_id = await _attester(owner)
        before = await _totals(owner)
        values = _values(ha_user_id, display_name="  Nia Okafor  ")

        returned = await _create(committer, values)
        assert returned == values["person_id"]

        rows = await _person_row(owner, values["person_id"])
        assert len(rows) == 1, (
            "exactly one person, or the kernel is not one act"
        )
        person = rows[0]
        # A literal, not a parameter: nobody may be created already erased
        # (0027:177-187). Compared exactly, because 'archived' and 'erased'
        # both satisfy a laxer check.
        assert person["status"] == "active"
        assert person["display_name"] == "Nia Okafor"
        assert person["pronouns"] == "she/her"
        assert person["privacy_scope"] == "household"
        # An owner-created person must never look like a reviewed import: that
        # path had a verifier this one does not, so borrowing its provenance
        # would launder an unverified person into a verified one.
        assert person["legacy_source_ref"] is None
        assert person["legacy_source_version"] is None
        assert person["legacy_source_sha256"] is None
        assert person["status_source_ref"] is None
        # One clock read for the whole act (0027:165).
        assert person["created_at"] == person["updated_at"]

        async with owner.connect() as connection:
            artifact = (
                await connection.execute(
                    text(
                        "SELECT artifact_kind, store, external_ref, "
                        "content_sha256, owner_principal_id, "
                        "retention_class, status, created_at "
                        "FROM privacy.artifact_registry "
                        "WHERE artifact_id=:artifact_id"
                    ),
                    {"artifact_id": values["attestation_artifact_id"]},
                )
            ).mappings().all()
        assert len(artifact) == 1
        assert artifact[0]["artifact_kind"] == "owner_person_attestation"
        assert artifact[0]["store"] == "postgresql"
        assert artifact[0]["external_ref"] is None
        assert artifact[0]["content_sha256"] == values["document_digest"]
        assert artifact[0]["owner_principal_id"] == principal_id
        assert artifact[0]["retention_class"] == "governed_history"
        assert artifact[0]["status"] == "active"
        # Provenance and person are the same act, so the same instant.
        assert artifact[0]["created_at"] == person["created_at"]

        # Being known to the household is not having an account.
        people, directives, bindings, principals = await _counts(
            owner, values["person_id"]
        )
        assert (people, directives, bindings, principals) == (1, 0, 0, 0)

        after = await _totals(owner)
        # people +1, principals +0, bindings +0, directives +0, artifacts +1.
        assert tuple(b - a for a, b in zip(before, after)) == (1, 0, 0, 0, 1)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_writes_the_directive_against_the_attestation() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        values = _values(
            ha_user_id, display_name="Ada Mensah", directive="do_not_track"
        )
        await _create(committer, values)

        async with owner.connect() as connection:
            directives = (
                await connection.execute(
                    text(
                        "SELECT directive_id, directive, enabled, "
                        "expires_at, source_artifact_id, source_ref, "
                        "source_version, source_snapshot_sha256, created_at "
                        "FROM identity.privacy_directives "
                        "WHERE person_id=:person_id"
                    ),
                    {"person_id": values["person_id"]},
                )
            ).mappings().all()
        assert len(directives) == 1, "one directive, decided in the same act"
        directive = directives[0]
        assert directive["directive_id"] == values["directive_id"]
        assert directive["directive"] == "do_not_track"
        # A directive written disabled is a decision that does nothing.
        assert directive["enabled"] is True
        assert directive["expires_at"] is None
        # It points at the attestation, not at an import snapshot.
        assert directive["source_artifact_id"] == (
            values["attestation_artifact_id"]
        )
        assert directive["source_ref"] is None
        assert directive["source_version"] is None
        assert directive["source_snapshot_sha256"] is None
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_auto_expire_must_carry_its_schedule() -> None:
    """An auto-expiring person who never expires is the opposite of the ask.

    The table's own ck_privacy_directives_privacy_expiry_shape would also
    refuse this, but as 23514 after the artifact and the person were already
    inserted. Asserting the kernel's named 22023 is what proves the guard at
    0027:128-132 ran first and nothing was written.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        before = await _totals(owner)
        values = _values(
            ha_user_id,
            directive="auto_expire",
            directive_expires_at=None,
        )
        with pytest.raises(DBAPIError) as refused:
            await _create(committer, values)
        assert _sqlstate(refused.value) == "22023"
        assert "owner_person_e5n_expiry_missing" in _message(refused.value)
        assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
        assert await _totals(owner) == before
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_only_auto_expire_takes_an_expiry() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
        for directive in ("do_not_track", "ignored", "silent", "private"):
            values = _values(
                ha_user_id,
                directive=directive,
                directive_expires_at=expires_at,
            )
            with pytest.raises(DBAPIError) as refused:
                await _create(committer, values)
            assert _sqlstate(refused.value) == "22023"
            assert "owner_person_e5n_expiry_unexpected" in _message(
                refused.value
            )
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_null_arguments_slip_past_three_guards() -> None:
    """Pins a gap, not a guarantee -- read these as descriptions.

    Three guards are written as `x <operator> literal` and can be reached with
    x NULL, where each comparison is NULL rather than true, so the IF takes
    its else branch and the guard does not fire:

      0027:97   target_document_digest !~ '^[0-9a-f]{64}$'
      0027:112  target_privacy_scope NOT IN ('private', 'household')
      0027:133  target_directive <> 'auto_expire'

    The display name guard is the one written correctly (0027:105, an explicit
    `IS NULL` arm), which is what makes the other three look like oversights
    rather than a decision.

    app/models.py rejects all three shapes before they reach the database, so
    the gap is only reachable by a caller that does not go through the
    adapter. The kernel is the thing that is supposed to hold the line when
    one does.

    If these start failing because the guards were rewritten with
    `IS DISTINCT FROM` / `IS NOT NULL`, that is the fix: delete this test.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)

        # A NULL digest is accepted, and privacy.artifact_registry allows a
        # NULL content_sha256, so the attestation is written hashing nothing.
        # Provenance that commits to no document is the failure this kernel
        # exists to prevent.
        no_digest = _values(
            ha_user_id, display_name="Ines Duarte", document_digest=None
        )
        await _create(committer, no_digest)
        assert await _counts(owner, no_digest["person_id"]) == (1, 0, 0, 0)
        async with owner.connect() as connection:
            digest = (
                await connection.execute(
                    text(
                        "SELECT content_sha256 "
                        "FROM privacy.artifact_registry "
                        "WHERE artifact_id=:artifact_id"
                    ),
                    {"artifact_id": no_digest["attestation_artifact_id"]},
                )
            ).scalar_one()
        assert digest is None

        # A NULL scope does fail closed, but on identity.people's NOT NULL
        # rather than the named guard, so the caller is told the person was
        # not created instead of which argument was wrong
        # (app/owner_person_adapter.py:97-98 matches on the guard's name).
        no_scope = _values(ha_user_id, privacy_scope=None)
        with pytest.raises(DBAPIError) as refused:
            await _create(committer, no_scope)
        assert "owner_person_e5n_privacy_scope_invalid" not in _message(
            refused.value
        )
        assert await _counts(owner, no_scope["person_id"]) == (0, 0, 0, 0)

        # An expiry with no directive is accepted and then thrown away.
        dropped = _values(
            ha_user_id,
            display_name="Ola Bergstrom",
            directive=None,
            directive_expires_at=dt.datetime.now(dt.UTC)
            + dt.timedelta(days=30),
        )
        await _create(committer, dropped)
        assert await _counts(owner, dropped["person_id"]) == (1, 0, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_refuses_a_name_that_is_not_a_name() -> None:
    """The name is how a human recognises this person in a later confirmation.

    The identifier guard at 0027:97-101 runs first, so these calls carry a
    well-formed digest and distinct ids: otherwise they would raise the wrong
    exception and the name guard would never be reached.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        for display_name in ("", "   ", "\t\n ", None, "N" * 256):
            values = _values(ha_user_id, display_name=display_name)
            with pytest.raises(DBAPIError) as refused:
                await _create(committer, values)
            assert _sqlstate(refused.value) == "22023"
            assert "owner_person_e5n_display_name_invalid" in _message(
                refused.value
            )
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_vocabularies_are_closed() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)

        # 'public' is the plausible wrong answer: it reads like a scope and is
        # not one, and privacy.people defaults to 'private' so a silent
        # fallthrough would look harmless.
        # NULL is deliberately not in this list: it does not reach this guard
        # at all. test_e5n_null_arguments_slip_past_three_guards covers it.
        for privacy_scope in ("public", "", "HOUSEHOLD", "Private"):
            values = _values(ha_user_id, privacy_scope=privacy_scope)
            with pytest.raises(DBAPIError) as refused:
                await _create(committer, values)
            assert _sqlstate(refused.value) == "22023"
            assert "owner_person_e5n_privacy_scope_invalid" in _message(
                refused.value
            )
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)

        # Scope is checked before directive (0027:112-123), so these must
        # carry a valid scope or they prove nothing about the directive guard.
        for directive in ("erased", "do_not_trace", "", "DO_NOT_TRACK"):
            values = _values(
                ha_user_id, privacy_scope="private", directive=directive
            )
            with pytest.raises(DBAPIError) as refused:
                await _create(committer, values)
            assert _sqlstate(refused.value) == "22023"
            assert "owner_person_e5n_directive_invalid" in _message(
                refused.value
            )
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)

        # Every accepted directive is genuinely accepted -- a closed
        # vocabulary that rejects its own members is the other failure.
        for directive in ("do_not_track", "ignored", "silent", "private"):
            values = _values(
                ha_user_id,
                display_name=f"Vocabulary {directive}",
                directive=directive,
            )
            await _create(committer, values)
            assert await _counts(owner, values["person_id"]) == (1, 1, 0, 0)
        values = _values(
            ha_user_id,
            display_name="Vocabulary auto_expire",
            directive="auto_expire",
            directive_expires_at=dt.datetime.now(dt.UTC)
            + dt.timedelta(days=30),
        )
        await _create(committer, values)
        assert await _counts(owner, values["person_id"]) == (1, 1, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_identifiers_must_be_well_formed_and_distinct() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        shared = uuid7()
        cases = (
            {"document_digest": ""},
            {"document_digest": "not-a-digest"},
            {"document_digest": _digest().upper()},
            {"document_digest": _digest()[:63]},
            {"document_digest": _digest() + "a"},
            # NULL is absent on purpose: it does not reach this guard either.
            {"person_id": shared, "attestation_artifact_id": shared},
        )
        for overrides in cases:
            values = _values(ha_user_id)
            values.update(overrides)
            with pytest.raises(DBAPIError) as refused:
                await _create(committer, values)
            assert _sqlstate(refused.value) == "22023"
            assert "owner_person_e5n_identifiers_invalid" in _message(
                refused.value
            )
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_refuses_an_account_with_no_confirmed_binding() -> None:
    """The named guard, not a bare permission error that shares its SQLSTATE.

    0027:155-158 raises `owner_person_e5n_binding_missing` with 42501, and
    app/owner_person_adapter.py:105-106 turns exactly that string into a
    ForbiddenError. A permission-denied on identity.ha_user_bindings arrives
    with the same 42501 and would be reported to the caller as a generic
    ConflictError instead -- which is why this asserts the message.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        values = _values(f"ha-absent-{uuid.uuid4()}")
        with pytest.raises(DBAPIError) as refused:
            await _create(committer, values)
        assert _sqlstate(refused.value) == "42501"
        assert "owner_person_e5n_binding_missing" in _message(refused.value)
        assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_replay_returns_the_row_without_repairing_it() -> None:
    """Replay is a proof, not a repair (0027:143-146).

    The second call runs in its own transaction: the first one assigned a
    transaction id, and 0027:92 refuses a transaction that already has one.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        values = _values(
            ha_user_id, display_name="Tomas Reyes", directive="silent"
        )
        first = await _create(committer, values)
        created = await _person_row(owner, values["person_id"])
        before = await _totals(owner)

        replayed = await _create(committer, values)
        assert replayed == first
        assert await _person_row(owner, values["person_id"]) == created
        assert await _totals(owner) == before
        assert await _counts(owner, values["person_id"]) == (1, 1, 0, 0)

        # Replayed under different arguments the row is still returned
        # untouched -- no second artifact, no renamed person, no second
        # directive. The identifiers are derived from a digest over the name,
        # the scope and the directive (app/owner_person_adapter.py:45-62), so
        # a caller reaching this through the adapter cannot construct it; a
        # caller that does not go through the adapter can.
        drifted = dict(values)
        drifted["display_name"] = "Someone Else"
        drifted["directive"] = "do_not_track"
        drifted["document_digest"] = _digest()
        drifted["attestation_artifact_id"] = uuid7()
        drifted["directive_id"] = uuid7()
        assert await _create(committer, drifted) == first
        assert await _person_row(owner, values["person_id"]) == created
        assert await _totals(owner) == before

        async with owner.connect() as connection:
            stray = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM privacy.artifact_registry "
                        "WHERE artifact_id=:artifact_id"
                    ),
                    {"artifact_id": drifted["attestation_artifact_id"]},
                )
            ).scalar_one()
        assert stray == 0
    finally:
        await committer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_refuses_every_caller_but_the_committer() -> None:
    """SECURITY DEFINER makes current_user the kernel for anyone who calls.

    So session_user is the only thing left that identifies the caller, and
    0027:79-86 checks it. The owner credential is a superuser: it can execute
    the function regardless of the EXECUTE grant, which is precisely the
    caller the guard has to turn away.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    owner_writer = _engine(OWNER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)
        values = _values(ha_user_id, display_name="Should Not Exist")
        with pytest.raises(DBAPIError) as refused:
            await _create(owner_writer, values)
        assert _sqlstate(refused.value) == "42501"
        assert "owner_person_e5n_role_invalid" in _message(refused.value)
        assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await owner_writer.dispose()
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5n_refuses_a_transaction_it_cannot_be_atomic_in() -> None:
    """Three separable ways the transaction can be wrong (0027:88-95).

    Not serializable, read-only, or already carrying a transaction id -- the
    last meaning the kernel is not the first act in its transaction, so
    something else's writes would ride along with the person it creates.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    read_committed = _engine(COMMITTER_DATABASE_ENV)
    committer = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, _ = await _attester(owner)

        not_serializable = _values(ha_user_id)
        with pytest.raises(DBAPIError) as refused:
            await _create(read_committed, not_serializable)
        assert _sqlstate(refused.value) == "25000"
        assert "owner_person_e5n_transaction_invalid" in _message(
            refused.value
        )

        read_only = _values(ha_user_id)
        with pytest.raises(DBAPIError) as refused:
            await _create(committer, read_only, read_only=True)
        assert _sqlstate(refused.value) == "25000"
        assert "owner_person_e5n_transaction_invalid" in _message(
            refused.value
        )

        late = _values(ha_user_id)
        with pytest.raises(DBAPIError) as refused:
            await _create(committer, late, assign_xid=True)
        assert _sqlstate(refused.value) == "25000"
        assert "owner_person_e5n_transaction_invalid" in _message(
            refused.value
        )

        for values in (not_serializable, read_only, late):
            assert await _counts(owner, values["person_id"]) == (0, 0, 0, 0)
    finally:
        await committer.dispose()
        await read_committed.dispose()
        await owner.dispose()
