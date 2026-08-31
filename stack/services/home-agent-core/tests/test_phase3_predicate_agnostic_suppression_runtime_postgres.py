"""Run revision 0022: the object side of a fact is suppressed for EVERY predicate.

``privacy.identity_fact_is_blocked`` is the USING *and* WITH CHECK expression of
the restrictive policy ``knowledge_fact_versions_e2_identity_suppression``
(alembic/versions/0012_identity_person_erasure_tombstone.py:154-157 declares the
target, :1284-1287 creates the policy). As revision 0012 originally wrote it
(:793-830) the body short-circuited on the predicate at :817 --
``IF target_predicate <> 'parent_of' ... RETURN false`` -- **before** it ever
read ``target_object ->> 'person_id'``. An erased person therefore stayed
visible as the OBJECT of any relationship that was not ``parent_of``. Revision
0023 then admitted ``partner_of`` into the vocabulary
(0023_partner_relationship_vocabulary.py:37), which is precisely the second
person-to-person predicate that turns the latent hole into a live disclosure.

Revision 0022 deletes the predicate guard
(0022_predicate_agnostic_fact_suppression.py:50-92; its ``downgrade`` at :99-138
puts the guard back, which is what makes the downgrade a real inverse).

The existing test for this is ``tests/test_predicate_agnostic_fact_suppression.py``
and it is static: it slices the migration file and greps for substrings, and its
"expected suppression matrix" asserts ``object_has_person and person_erased``
against a Python expression -- it never touches PostgreSQL. That catches a
deleted line. It cannot catch a plpgsql logic error, a policy that stopped
referencing the function, a lost EXECUTE grant, or a deployment still sitting on
the 0012 body. This module executes the deployed function and the deployed
policy against real rows and a really erased person.

What "erased" means here is not faked. The only sanctioned way to make a person
blocked in E2 is ``privacy.replay_identity_person_retrieval_block_v2(jsonb)``
(0012:969-1225), an owner-owned SECURITY DEFINER callable whose FORCE-RLS
policies bind it to ``session_user = 'home_agent_erasure'``. It validates the
full ledger tuple, the exact residual set and every residual commitment before
it writes the tombstone. This module builds a real v2 payload with the same
shape the E2 runtime suite uses
(tests/test_phase3_identity_erasure_e2_runtime_postgres.py:49-148, :624-635) and
calls that function under the erasure session role. Nothing here inserts into
``privacy.subject_retrieval_blocks`` by hand and nothing here stubs the
predicate.

Blast radius: every person this module erases is a person this module created
seconds earlier, under a fresh UUIDv7, in the same test. It is gated on the two
E5n gate URLs and skips entirely without them, like every other runtime test
here. When the gate also exports the disposable cluster's system identifier the
module pins it, and refuses to run on a cluster that is not the one the gate
built.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.range import Range
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import schema
from app.ids import uuid7


OWNER_DATABASE_ENV = "TEST_PHASE3_OWNER_ATTESTED_E5N_OWNER_DATABASE_URL"
COMMITTER_DATABASE_ENV = "TEST_PHASE3_OWNER_ATTESTED_E5N_COMMITTER_DATABASE_URL"
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
HOSTED_GATE_SYSTEM_IDENTIFIER_ENV = (
    "TEST_PHASE3_IDENTITY_ERASURE_E1_SYSTEM_IDENTIFIER"
)

# The gate migrates to the newest revision, so this is "the current
# head", not a property of 0028. It moves with each revision that
# changes identity ownership; 0029 is the current one.
TARGET_REVISION = "0030_relationship_vocabulary_e5q"
BLOCKED_FUNCTION = "privacy.identity_fact_is_blocked(text,uuid,text,jsonb,uuid)"
PERSON_BLOCKED_FUNCTION = "privacy.identity_person_is_blocked(uuid)"
REPLAY_CALLABLE = "privacy.replay_identity_person_retrieval_block_v2"
REPLAY_FUNCTION = f"{REPLAY_CALLABLE}(jsonb)"
SUPPRESSION_FUNCTION_OWNER = "home_agent_identity_erasure_kernel"
FACT_SUPPRESSION_POLICY = "knowledge_fact_versions_e2_identity_suppression"
SUPPORT_SUPPRESSION_POLICY = "knowledge_fact_support_e2_identity_suppression"
ANTI_RESURRECTION_TRIGGER = "knowledge_fact_versions_e2_anti_resurrection"

# The reader the restrictive policy is written for. It is one of the seven
# runtime roles named in 0012:1284-1287 and it holds a real SELECT grant on
# knowledge.fact_versions, so "invisible" here can only mean the policy.
SUPPRESSION_READER_ROLE = "home_agent_api"
# 0012:1284-1287 attaches the restrictive policy to exactly these roles.
SUPPRESSION_ROLES = (
    "home_agent_api",
    "home_agent_binding_operator",
    "home_agent_ingest",
    "home_agent_worker",
    "home_agent_erasure",
    "home_agent_rollout",
    "home_agent_backup",
)
# The replay callable's FORCE-RLS policies require this session role (0012:
# subject_retrieval_blocks_e2_replay_insert).
ERASURE_SESSION_ROLE = "home_agent_erasure"
MANDATORY_RESIDUALS = (
    "live_identity_rows_retained",
    "semantic_dependencies_not_evaluated",
    "generic_artifact_lineage_unresolved",
    "external_cleanup_not_evaluated",
    "legacy_cleanup_not_evaluated",
    "backup_expiry_unverified",
    "preexisting_snapshot_visibility",
)

# A predicate that did not exist when 0012 wrote its predicate guard and does
# not exist in the schema today. Nothing may special-case it, which is the
# point: the fix has to be predicate-agnostic, not "parent_of plus partner_of".
UNKNOWN_PREDICATE = "cohabits_with_v7"


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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _residual_commitments(record_digest: str) -> dict[str, str]:
    """Mirror the commitment the replay callable recomputes (0012:1180-1190)."""

    return {
        code: _digest(f"identity-person-erasure-residual-v2:{record_digest}:{code}")
        for code in MANDATORY_RESIDUALS
    }


def _erasure_payload(person_id: uuid.UUID) -> dict[str, object]:
    """A complete ledger-replay tuple for one person.

    Key set, literals, digest formats and residual commitments all have to be
    exact or the callable raises before it writes; see 0012:1006-1100. The shape
    is the one the E2 runtime suite already uses
    (tests/test_phase3_identity_erasure_e2_runtime_postgres.py:116-148).
    """

    block_id = uuid7()
    record_digest = _digest(f"record:{block_id}")
    completed_at = datetime.now(UTC) - timedelta(seconds=1)
    return {
        "version": 2,
        "subject_kind": "identity_person",
        "outbox_id": str(uuid7()),
        "erasure_request_id": str(uuid7()),
        "person_id": str(person_id),
        "operation_id": str(uuid7()),
        "block_id": str(block_id),
        "block_commitment": _digest(f"block:{block_id}"),
        "outcome_code": "retrieval_block_active",
        "operation_codes": ["activate_identity_person_retrieval_block"],
        "policy_digest": _digest("e5i-predicate-agnostic-suppression-policy"),
        "completed_at": completed_at.isoformat(),
        "source_created_at": (completed_at - timedelta(seconds=1)).isoformat(),
        "external_pending_codes": [],
        "legacy_untracked_codes": [],
        "checkpoint_affected": True,
        "backup_expiry_at": None,
        "exact_residual_codes": list(MANDATORY_RESIDUALS),
        "ledger_epoch": secrets.randbelow(2**62 - 1) + 1,
        "ledger_record_hash": _digest(f"ledger:{block_id}"),
        "ledger_record_digest": record_digest,
        "residual_commitments": _residual_commitments(record_digest),
    }


@dataclass(frozen=True, slots=True)
class Scenario:
    """One self-contained cast. Only ``object_person_id`` is ever erased."""

    token: str
    viewer_person_id: uuid.UUID
    viewer_principal_id: uuid.UUID
    subject_person_id: uuid.UUID
    object_person_id: uuid.UUID
    object_principal_id: uuid.UUID
    control_person_id: uuid.UUID
    writer_person_id: uuid.UUID
    place_id: uuid.UUID
    memory_transaction_id: uuid.UUID
    partner_fact_version_id: uuid.UUID
    parent_fact_version_id: uuid.UUID
    control_fact_version_id: uuid.UUID
    place_fact_version_id: uuid.UUID
    partner_support_id: uuid.UUID
    partner_support_artifact_id: uuid.UUID


def _fact_probes(scenario: Scenario) -> dict[str, uuid.UUID]:
    return {
        "partner_of_names_the_erased_person": scenario.partner_fact_version_id,
        "parent_of_names_the_erased_person": scenario.parent_fact_version_id,
        "partner_of_names_an_untouched_person": scenario.control_fact_version_id,
        "place_valued_fact": scenario.place_fact_version_id,
    }


async def _assert_disposable_gate_cluster(connection) -> None:
    """Never erase anyone on a cluster the gate did not build.

    ``_start_phase`` in tools/run-home-agent-e1-postgres-gate.py stands up a
    throwaway PostgreSQL container per phase and exports that container's
    ``pg_control_system()`` system identifier to pytest. A production cluster
    can never present the same identifier. This is a pin, not the primary
    control: when the gate does not export the variable there is nothing to pin
    against and the module's URL gating is the whole barrier, exactly as it is
    for tests/test_phase3_parent_relationship_commit_e5f_runtime_postgres.py.
    """

    expected = os.getenv(HOSTED_GATE_SYSTEM_IDENTIFIER_ENV)
    if not expected:
        return
    observed = (
        await connection.execute(
            text("SELECT system_identifier::text FROM pg_control_system()")
        )
    ).scalar_one()
    if observed != expected:
        pytest.fail(
            "refusing to erase a person: this is not the gate's disposable "
            f"cluster (system identifier {observed!r} != {expected!r})"
        )


async def _seed(owner: AsyncEngine) -> Scenario:
    """Four facts under one perspective, three people, one place.

    Every fact shares ``perspective_principal_id`` so that the pre-existing
    permissive policy ``knowledge_fact_versions_principal`` admits all four for
    one ``app.principal_id``. That isolates the restrictive suppression policy
    as the only thing that can remove a row later. The perspective principal
    belongs to a person who is never erased, so the perspective arm of
    ``identity_fact_is_blocked`` (0012:808-812) cannot confound the object arm.
    """

    token = uuid.uuid4().hex
    now = datetime.now(UTC) - timedelta(minutes=1)
    scenario = Scenario(
        token=token,
        viewer_person_id=uuid7(),
        viewer_principal_id=uuid7(),
        subject_person_id=uuid7(),
        object_person_id=uuid7(),
        object_principal_id=uuid7(),
        control_person_id=uuid7(),
        writer_person_id=uuid7(),
        place_id=uuid7(),
        memory_transaction_id=uuid7(),
        partner_fact_version_id=uuid7(),
        parent_fact_version_id=uuid7(),
        control_fact_version_id=uuid7(),
        place_fact_version_id=uuid7(),
        partner_support_id=uuid7(),
        partner_support_artifact_id=uuid7(),
    )
    async with owner.begin() as connection:
        await _assert_disposable_gate_cluster(connection)
        await connection.execute(
            insert(schema.people),
            [
                {
                    "person_id": person_id,
                    "display_name": f"E5i {role} {token}",
                    "status": "active",
                    "privacy_scope": "private",
                }
                for role, person_id in (
                    ("viewer", scenario.viewer_person_id),
                    ("subject", scenario.subject_person_id),
                    ("object", scenario.object_person_id),
                    ("control", scenario.control_person_id),
                    ("writer", scenario.writer_person_id),
                )
            ],
        )
        await connection.execute(
            insert(schema.principals),
            [
                {
                    "principal_id": scenario.viewer_principal_id,
                    "person_id": scenario.viewer_person_id,
                    "kind": "ha_user",
                    "display_label": f"E5i viewer {token}",
                    "status": "active",
                },
                {
                    "principal_id": scenario.object_principal_id,
                    "person_id": scenario.object_person_id,
                    "kind": "ha_user",
                    "display_label": f"E5i object {token}",
                    "status": "active",
                },
            ],
        )
        await connection.execute(
            insert(schema.memory_transactions).values(
                transaction_id=scenario.memory_transaction_id,
                principal_id=scenario.viewer_principal_id,
                kind="e5i_predicate_agnostic_suppression",
                state="committed",
                candidate={"kind": "e5i_test"},
                preview={"kind": "e5i_test"},
                verifier_results=[],
                policy_version="e5i-runtime-v1",
                policy_digest=_digest(f"memory:{token}"),
                confirmation_digest=_digest(f"confirmation:{token}"),
                confirmed_at=now,
            )
        )
        common = {
            "version": 1,
            "subject_type": "person",
            "subject_id": scenario.subject_person_id,
            "perspective_principal_id": scenario.viewer_principal_id,
            "valid_range": Range(now, None, bounds="[)"),
            "system_range": Range(now, None, bounds="[)"),
            "authority": "authorized_administrator",
            "support": "explicit_authority",
            "contradiction": "none",
            "freshness": "not_applicable",
            "coverage": "not_applicable",
            "resolution": "accepted",
            "privacy_scope": "private",
            "memory_transaction_id": scenario.memory_transaction_id,
        }
        await connection.execute(
            insert(schema.fact_versions),
            [
                {
                    **common,
                    "fact_version_id": scenario.partner_fact_version_id,
                    "fact_id": uuid7(),
                    "predicate": "partner_of",
                    "object": {"person_id": str(scenario.object_person_id)},
                },
                {
                    **common,
                    "fact_version_id": scenario.parent_fact_version_id,
                    "fact_id": uuid7(),
                    "predicate": "parent_of",
                    "object": {"person_id": str(scenario.object_person_id)},
                },
                {
                    **common,
                    "fact_version_id": scenario.control_fact_version_id,
                    "fact_id": uuid7(),
                    "predicate": "partner_of",
                    "object": {"person_id": str(scenario.control_person_id)},
                },
                {
                    **common,
                    "fact_version_id": scenario.place_fact_version_id,
                    "fact_id": uuid7(),
                    "predicate": "place_social_descriptor",
                    "object": {"place_id": str(scenario.place_id)},
                },
            ],
        )
        await connection.execute(
            insert(schema.fact_support).values(
                support_id=scenario.partner_support_id,
                fact_version_id=scenario.partner_fact_version_id,
                artifact_id=scenario.partner_support_artifact_id,
                root_observation_id=uuid7(),
                dependency_domain="e5i_test",
                support_role="authority",
            )
        )
    return scenario


async def _erase(owner: AsyncEngine, person_id: uuid.UUID) -> uuid.UUID:
    """Erase one person through the only sanctioned E2 path.

    ``privacy.replay_identity_person_retrieval_block_v2`` (0012:969-1225) is
    owner-owned SECURITY DEFINER and its FORCE-RLS policies require
    ``session_user = 'home_agent_erasure'``. The gate hands this module the
    owner login, so the erasure session role is assumed with ``SET LOCAL
    SESSION AUTHORIZATION`` for the duration of that one transaction; the
    assertion below refuses to proceed if the assumption did not take. Every
    payload guard, the residual-set check and every residual commitment are
    evaluated by the callable, unchanged.
    """

    payload = _erasure_payload(person_id)
    async with owner.begin() as connection:
        await _assert_disposable_gate_cluster(connection)
        await connection.execute(
            text(f"SET LOCAL SESSION AUTHORIZATION {ERASURE_SESSION_ROLE}")
        )
        assert (
            await connection.execute(text("SELECT session_user"))
        ).scalar_one() == ERASURE_SESSION_ROLE
        block_id = (
            await connection.execute(
                text(f"SELECT {REPLAY_CALLABLE}(CAST(:payload AS jsonb))"),
                {"payload": json.dumps(payload, separators=(",", ":"))},
            )
        ).scalar_one()
    assert block_id == uuid.UUID(str(payload["block_id"]))

    async with owner.connect() as connection:
        residuals = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM "
                    "privacy.identity_person_erasure_residuals "
                    "WHERE person_id=:person AND state='open'"
                ),
                {"person": person_id},
            )
        ).scalar_one()
    assert residuals == len(MANDATORY_RESIDUALS)
    return block_id


def _predicate_cases(scenario: Scenario) -> tuple[tuple[str, dict[str, object]], ...]:
    """Every input shape the 0022 body can take, named by what it proves."""

    erased = json.dumps({"person_id": str(scenario.object_person_id)})
    untouched = json.dumps({"person_id": str(scenario.control_person_id)})
    place = json.dumps({"place_id": str(scenario.place_id)})
    unerased_subject = str(scenario.subject_person_id)
    viewer = str(scenario.viewer_principal_id)

    def case(
        predicate: str,
        object_json: str | None,
        *,
        subject_id: str = unerased_subject,
        subject_type: str = "person",
        perspective: str | None = viewer,
    ) -> dict[str, object]:
        return {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "predicate": predicate,
            "object": object_json,
            "perspective": perspective,
        }

    return (
        # The bug, exactly: pre-0022 this returned false.
        ("partner_of_object_is_erased", case("partner_of", erased)),
        # No regression on the one predicate the old body did check.
        ("parent_of_object_is_erased", case("parent_of", erased)),
        # Predicate-agnostic, not a two-predicate allowlist.
        (
            "unknown_predicate_object_is_erased",
            case(UNKNOWN_PREDICATE, erased),
        ),
        # ...and not keyed on subject_type either.
        (
            "non_person_subject_object_is_erased",
            case(
                "partner_of",
                erased,
                subject_id=str(scenario.place_id),
                subject_type="place",
            ),
        ),
        # Not over-blocking: a living person as object stays visible.
        ("partner_of_object_is_untouched", case("partner_of", untouched)),
        ("parent_of_object_is_untouched", case("parent_of", untouched)),
        # A place-valued object carries no person_id, for any predicate.
        ("partner_of_object_is_place", case("partner_of", place)),
        (
            "place_predicate_object_is_place",
            case("place_social_descriptor", place),
        ),
        (
            "unknown_predicate_object_is_place",
            case(UNKNOWN_PREDICATE, place),
        ),
        # Non-object jsonb: the shape guard has to survive the fix.
        ("object_is_json_string", case("partner_of", '"not-an-object"')),
        (
            "object_is_json_array_of_the_erased_person",
            case(
                "partner_of",
                json.dumps([{"person_id": str(scenario.object_person_id)}]),
            ),
        ),
        ("object_is_json_number", case("partner_of", "42")),
        ("object_is_json_null", case("partner_of", "null")),
        ("object_is_sql_null", case("partner_of", None)),
        # person_id present but not a UUID: the regex arm, not an exception.
        (
            "object_person_id_is_malformed",
            case("partner_of", json.dumps({"person_id": "not-a-uuid"})),
        ),
        # The two arms 0022 promised not to touch.
        (
            "subject_side_is_erased",
            case(
                "partner_of",
                place,
                subject_id=str(scenario.object_person_id),
            ),
        ),
        (
            "perspective_side_is_erased",
            case(
                "place_social_descriptor",
                place,
                perspective=str(scenario.object_principal_id),
            ),
        ),
    )


async def _evaluate(owner: AsyncEngine, scenario: Scenario) -> dict[str, bool]:
    """Call the deployed function as a role that really holds EXECUTE on it.

    0012 grants EXECUTE to the seven runtime roles only; the owner login would
    pass on its superuser attribute alone and prove nothing about the grant.
    """

    results: dict[str, bool] = {}
    async with owner.begin() as connection:
        await connection.execute(
            text(f"SET LOCAL ROLE {SUPPRESSION_READER_ROLE}")
        )
        assert (
            await connection.execute(text("SELECT current_user"))
        ).scalar_one() == SUPPRESSION_READER_ROLE
        for name, values in _predicate_cases(scenario):
            results[name] = (
                await connection.execute(
                    text(
                        "SELECT privacy.identity_fact_is_blocked("
                        "CAST(:subject_type AS text),"
                        "CAST(:subject_id AS uuid),"
                        "CAST(:predicate AS text),"
                        "CAST(:object AS jsonb),"
                        "CAST(:perspective AS uuid))"
                    ),
                    values,
                )
            ).scalar_one()
    return results


async def _visible_to_suppressed_reader(
    owner: AsyncEngine, scenario: Scenario
) -> dict[str, bool]:
    """What the restrictive policy actually lets ``home_agent_api`` select."""

    visible: dict[str, bool] = {}
    async with owner.begin() as connection:
        await connection.execute(
            text(f"SET LOCAL ROLE {SUPPRESSION_READER_ROLE}")
        )
        assert (
            await connection.execute(text("SELECT current_user"))
        ).scalar_one() == SUPPRESSION_READER_ROLE
        await connection.execute(
            text("SELECT set_config('app.principal_id', :principal, true)"),
            {"principal": str(scenario.viewer_principal_id)},
        )
        for name, fact_version_id in _fact_probes(scenario).items():
            visible[name] = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE fact_version_id=:fact_version_id"
                    ),
                    {"fact_version_id": fact_version_id},
                )
            ).scalar_one() == 1
        visible["partner_fact_support"] = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM knowledge.fact_support "
                    "WHERE support_id=:support_id"
                ),
                {"support_id": scenario.partner_support_id},
            )
        ).scalar_one() == 1
    return visible


async def _rows_still_exist(owner: AsyncEngine, scenario: Scenario) -> dict[str, bool]:
    """The same rows, read by the credential that bypasses row security."""

    present: dict[str, bool] = {}
    async with owner.connect() as connection:
        for name, fact_version_id in _fact_probes(scenario).items():
            present[name] = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE fact_version_id=:fact_version_id"
                    ),
                    {"fact_version_id": fact_version_id},
                )
            ).scalar_one() == 1
        present["partner_fact_support"] = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM knowledge.fact_support "
                    "WHERE support_id=:support_id"
                ),
                {"support_id": scenario.partner_support_id},
            )
        ).scalar_one() == 1
    return present


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5i_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    """Inside the gate, a missing URL must fail rather than skip 0022's proof."""

    assert _configured()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5i_deployed_function_and_policy_are_wired_to_each_other() -> None:
    """The deployed body carries the fix and the policy still calls that body.

    A correct function that nothing references suppresses nothing, so the
    binding is asserted from the catalog rather than assumed.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one() == TARGET_REVISION

            function = (
                (
                    await connection.execute(
                        text(
                            "SELECT owner.rolname AS function_owner, "
                            "function.prosecdef, function.provolatile, "
                            "function.proconfig, function.prosrc "
                            "FROM pg_catalog.pg_proc AS function "
                            "JOIN pg_catalog.pg_roles AS owner "
                            "ON owner.oid=function.proowner "
                            "WHERE function.oid="
                            "pg_catalog.to_regprocedure(:function)"
                        ),
                        {"function": BLOCKED_FUNCTION},
                    )
                )
                .mappings()
                .one()
            )
            assert function["function_owner"] == SUPPRESSION_FUNCTION_OWNER
            assert function["prosecdef"] is True
            assert function["provolatile"] == "s"
            assert set(function["proconfig"]) == {"search_path=pg_catalog"}
            source = function["prosrc"]
            assert "target_predicate <> 'parent_of'" not in source, (
                "the deployed body still short-circuits on the predicate: this "
                "database is running the 0012 body, not the 0022 replacement"
            )
            assert "jsonb_typeof(target_object) <> 'object'" in source
            assert "target_object ->> 'person_id'" in source
            assert "privacy.identity_person_is_blocked(target_subject_id)" in source
            assert "privacy.identity_principal_is_blocked(" in source

            for policy_name, relation in (
                (FACT_SUPPRESSION_POLICY, "knowledge.fact_versions"),
                (SUPPORT_SUPPRESSION_POLICY, "knowledge.fact_support"),
            ):
                policy = (
                    (
                        await connection.execute(
                            text(
                                "SELECT policy.polpermissive, policy.polcmd, "
                                "pg_catalog.pg_get_expr("
                                "policy.polqual, policy.polrelid) AS using_expression, "
                                "pg_catalog.pg_get_expr("
                                "policy.polwithcheck, policy.polrelid"
                                ") AS check_expression, "
                                "(SELECT pg_catalog.array_agg("
                                "pg_catalog.pg_get_userbyid(role_oid) "
                                "ORDER BY pg_catalog.pg_get_userbyid(role_oid)) "
                                "FROM pg_catalog.unnest(policy.polroles) "
                                "AS role_oid) AS roles, "
                                "relation.relrowsecurity, "
                                "relation.relforcerowsecurity "
                                "FROM pg_catalog.pg_policy AS policy "
                                "JOIN pg_catalog.pg_class AS relation "
                                "ON relation.oid=policy.polrelid "
                                "WHERE policy.polrelid="
                                "pg_catalog.to_regclass(:relation) "
                                "AND policy.polname=:policy"
                            ),
                            {"relation": relation, "policy": policy_name},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert policy["polpermissive"] is False, (
                    f"{policy_name} must be RESTRICTIVE; a permissive policy "
                    "would be OR-ed with the others and suppress nothing"
                )
                assert policy["polcmd"] == "*"
                assert policy["relrowsecurity"] is True
                assert policy["relforcerowsecurity"] is True
                assert tuple(sorted(policy["roles"])) == tuple(
                    sorted(SUPPRESSION_ROLES)
                )
                for expression in (
                    policy["using_expression"],
                    policy["check_expression"],
                ):
                    assert expression is not None
                    assert "privacy.identity_fact_is_blocked(" in expression

            role_array = ",".join(f"'{role}'" for role in SUPPRESSION_ROLES)
            grants = (
                await connection.execute(
                    text(
                        "SELECT bool_and(pg_catalog.has_function_privilege("
                        "role_name, pg_catalog.to_regprocedure(:blocked), "
                        "'EXECUTE')) "
                        f"FROM pg_catalog.unnest(ARRAY[{role_array}]) "
                        "AS role_name"
                    ),
                    {"blocked": BLOCKED_FUNCTION},
                )
            ).scalar_one()
            assert grants is True, (
                "a runtime role lost EXECUTE on the suppression predicate; the "
                "restrictive policy cannot evaluate without it"
            )

            trigger = (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.pg_get_triggerdef(trigger.oid) "
                        "FROM pg_catalog.pg_trigger AS trigger "
                        "WHERE trigger.tgrelid="
                        "pg_catalog.to_regclass('knowledge.fact_versions') "
                        "AND trigger.tgname=:trigger"
                    ),
                    {"trigger": ANTI_RESURRECTION_TRIGGER},
                )
            ).scalar_one()
            assert "reject_tombstoned_identity_write('fact:subject_id')" in trigger
    finally:
        await owner.dispose()

    # The committer credential is table-blind here: it can reach the E5k/E5n
    # kernels but never the fact table those kernels write, so nothing in this
    # module could have been proved from the committer's own view.
    committer = _engine(COMMITTER_DATABASE_ENV)
    try:
        with pytest.raises(DBAPIError) as denied:
            async with committer.begin() as connection:
                await connection.execute(
                    text("SELECT count(*) FROM knowledge.fact_versions")
                )
        assert _sqlstate(denied.value) == "42501"
    finally:
        await committer.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5i_object_side_is_suppressed_for_every_predicate() -> None:
    """Call the function for real, before and after a real erasure.

    The whole matrix is asserted as one dict in each direction, so a change in
    any case -- including one that starts over-blocking -- fails here.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        scenario = await _seed(owner)

        before = await _evaluate(owner, scenario)
        assert before == {
            "partner_of_object_is_erased": False,
            "parent_of_object_is_erased": False,
            "unknown_predicate_object_is_erased": False,
            "non_person_subject_object_is_erased": False,
            "partner_of_object_is_untouched": False,
            "parent_of_object_is_untouched": False,
            "partner_of_object_is_place": False,
            "place_predicate_object_is_place": False,
            "unknown_predicate_object_is_place": False,
            "object_is_json_string": False,
            "object_is_json_array_of_the_erased_person": False,
            "object_is_json_number": False,
            "object_is_json_null": False,
            "object_is_sql_null": False,
            "object_person_id_is_malformed": False,
            "subject_side_is_erased": False,
            "perspective_side_is_erased": False,
        }

        await _erase(owner, scenario.object_person_id)

        after = await _evaluate(owner, scenario)
        assert after == {
            # The three that flip on the object arm. The first one is the bug:
            # the 0012 body returned false here because the predicate was not
            # 'parent_of', and an erased person stayed visible as the object of
            # a partner_of relationship.
            "partner_of_object_is_erased": True,
            "unknown_predicate_object_is_erased": True,
            "non_person_subject_object_is_erased": True,
            # Unchanged from the 0012 body: no regression.
            "parent_of_object_is_erased": True,
            # The object arm is keyed on the person, not on the predicate.
            "partner_of_object_is_untouched": False,
            "parent_of_object_is_untouched": False,
            # No person_id, so nothing to suppress, whatever the predicate is.
            "partner_of_object_is_place": False,
            "place_predicate_object_is_place": False,
            "unknown_predicate_object_is_place": False,
            # The jsonb shape guard survived the fix.
            "object_is_json_string": False,
            "object_is_json_array_of_the_erased_person": False,
            "object_is_json_number": False,
            "object_is_json_null": False,
            "object_is_sql_null": False,
            "object_person_id_is_malformed": False,
            # The arms 0022 said it would not touch, still working.
            "subject_side_is_erased": True,
            "perspective_side_is_erased": True,
        }

        assert after["partner_of_object_is_erased"] is True, (
            "an erased person is still not suppressed as the object of a "
            "partner_of fact; this is the exact disclosure revision 0022 "
            "exists to close"
        )
        # Strictly stronger, case by case: nothing the old body suppressed may
        # have become visible.
        assert not any(
            before[name] and not after[name] for name in before
        ), "0022 must never turn a suppressed case back into a visible one"
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5i_restrictive_policy_hides_the_object_side_row() -> None:
    """The function returning true is necessary; the policy acting on it is the point.

    ``home_agent_api`` holds a real SELECT grant on knowledge.fact_versions and
    is named in the restrictive policy, so a row that disappears for it
    disappeared because of the policy.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        scenario = await _seed(owner)

        before = await _visible_to_suppressed_reader(owner, scenario)
        assert before == {
            "partner_of_names_the_erased_person": True,
            "parent_of_names_the_erased_person": True,
            "partner_of_names_an_untouched_person": True,
            "place_valued_fact": True,
            "partner_fact_support": True,
        }, (
            "the reader could not see the seeded facts before any erasure, so "
            "a later disappearance would prove nothing"
        )

        await _erase(owner, scenario.object_person_id)

        after = await _visible_to_suppressed_reader(owner, scenario)
        assert after == {
            # Pre-0022 this row stayed visible. That is the disclosure.
            "partner_of_names_the_erased_person": False,
            "parent_of_names_the_erased_person": False,
            "partner_of_names_an_untouched_person": True,
            "place_valued_fact": True,
            # knowledge.fact_support is suppressed derivatively, through the
            # fact version it supports (0012:160-170).
            "partner_fact_support": False,
        }

        # Deletion-free: E2 suppresses retrieval, it does not remove rows. If
        # these were gone, the test above would pass for the wrong reason.
        assert await _rows_still_exist(owner, scenario) == {
            "partner_of_names_the_erased_person": True,
            "parent_of_names_the_erased_person": True,
            "partner_of_names_an_untouched_person": True,
            "place_valued_fact": True,
            "partner_fact_support": True,
        }
    finally:
        await owner.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5n URLs are not configured")
async def test_e5i_object_side_write_path_is_closed_for_every_predicate() -> None:
    """The same function guards writes, so the fix has to close the write too.

    ``knowledge_fact_versions_e2_anti_resurrection`` (0012:1291-1296) calls
    ``privacy.reject_tombstoned_identity_write('fact:subject_id')``, whose
    'fact' branch (0012:892-899) hands the whole candidate row to
    ``identity_fact_is_blocked``. Under the 0012 body a brand-new partner_of
    fact naming an erased person as its object would have been accepted. The
    insert runs as the owner, which bypasses row security, so only the trigger
    can reject it.
    """

    owner = _engine(OWNER_DATABASE_ENV)
    try:
        scenario = await _seed(owner)
        await _erase(owner, scenario.object_person_id)

        now = datetime.now(UTC)
        common = {
            "version": 1,
            "subject_type": "person",
            "subject_id": scenario.writer_person_id,
            "perspective_principal_id": scenario.viewer_principal_id,
            "valid_range": Range(now, None, bounds="[)"),
            "system_range": Range(now, None, bounds="[)"),
            "authority": "authorized_administrator",
            "support": "explicit_authority",
            "contradiction": "none",
            "freshness": "not_applicable",
            "coverage": "not_applicable",
            "resolution": "accepted",
            "privacy_scope": "private",
            "memory_transaction_id": scenario.memory_transaction_id,
        }

        for predicate in ("partner_of", UNKNOWN_PREDICATE):
            with pytest.raises(DBAPIError) as rejected:
                async with owner.begin() as connection:
                    await connection.execute(
                        insert(schema.fact_versions).values(
                            **common,
                            fact_version_id=uuid7(),
                            fact_id=uuid7(),
                            predicate=predicate,
                            object={"person_id": str(scenario.object_person_id)},
                        )
                    )
            assert _sqlstate(rejected.value) == "23514", (
                f"a new {predicate} fact naming an erased person as its object "
                "was accepted"
            )
            assert "identity_person_retrieval_block_active" in str(
                rejected.value.orig
            )

        # The same insert naming a living person still works, so the rejection
        # above is the erasure and not a broken insert.
        accepted_fact_version_id = uuid7()
        async with owner.begin() as connection:
            await connection.execute(
                insert(schema.fact_versions).values(
                    **common,
                    fact_version_id=accepted_fact_version_id,
                    fact_id=uuid7(),
                    predicate="partner_of",
                    object={"person_id": str(scenario.control_person_id)},
                )
            )
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE fact_version_id=:fact_version_id"
                    ),
                    {"fact_version_id": accepted_fact_version_id},
                )
            ).scalar_one() == 1
    finally:
        await owner.dispose()
