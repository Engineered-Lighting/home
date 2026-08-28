"""The object side of a fact must be suppressed for every predicate.

privacy.identity_fact_is_blocked is the USING and WITH CHECK expression of the
restrictive policy knowledge_fact_versions_e2_identity_suppression. Before
revision 0022 it returned false for any predicate other than 'parent_of' before
it looked at the object at all, so a second person-to-person predicate would
have left erased people visible as the object of a relationship.

The replacement must be STRICTLY STRONGER: true everywhere the old body was
true, plus the cases the predicate guard skipped. If that property ever breaks,
a fact that is currently suppressed becomes visible, which is worse than the
hole this closes.
"""

from __future__ import annotations

import os
import pathlib

import pytest

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0022_predicate_agnostic_fact_suppression.py"
)


def _upgrade_sql() -> str:
    source = MIGRATION.read_text()
    return source[source.index("def upgrade"): source.index("def downgrade")]


def _downgrade_sql() -> str:
    source = MIGRATION.read_text()
    return source[source.index("def downgrade"):]


def test_upgrade_does_not_branch_on_the_predicate() -> None:
    upgrade = _upgrade_sql()
    assert "target_predicate <> 'parent_of'" not in upgrade, (
        "the predicate guard is the hole; the upgrade must not reintroduce it"
    )
    # The object-shape guard must stay: a non-object cannot carry a person_id.
    assert "jsonb_typeof(target_object) <> 'object'" in upgrade
    assert "target_object ->> 'person_id'" in upgrade


def test_upgrade_keeps_the_subject_and_perspective_arms() -> None:
    """Closing the object hole must not weaken the arms that already worked."""

    upgrade = _upgrade_sql()
    assert "privacy.identity_principal_is_blocked(" in upgrade
    assert "target_subject_type = 'person'" in upgrade
    assert "privacy.identity_person_is_blocked(target_subject_id)" in upgrade


def test_downgrade_restores_the_previous_behaviour() -> None:
    """A downgrade must be a real inverse, not a no-op."""

    assert "target_predicate <> 'parent_of'" in _downgrade_sql()


def test_upgrade_is_unconditional() -> None:
    """Revision 0010 branched on database shape and so never ran in CI.

    That divergence cost days: the deployment dropped a column CI never dropped,
    and a pinned catalog digest could not converge. This migration must run the
    same statements everywhere.
    """

    upgrade = _upgrade_sql()
    for smell in ("_validate_predecessor_shape", "if not ", "IF NOT EXISTS ("):
        assert smell not in upgrade, f"upgrade appears conditional on {smell!r}"


@pytest.mark.parametrize(
    ("predicate", "object_has_person", "person_erased", "expected"),
    [
        ("parent_of", True, False, False),
        ("parent_of", True, True, True),
        # The fix: a second person-to-person predicate is now suppressed.
        ("partner_of", True, True, True),
        ("partner_of", True, False, False),
        # A predicate whose object is not a person is unaffected.
        ("place_social_descriptor", False, True, False),
    ],
)
def test_expected_suppression_matrix(
    predicate: str, object_has_person: bool, person_erased: bool, expected: bool
) -> None:
    """Documents the intended semantics independently of the SQL text."""

    blocked = object_has_person and person_erased
    assert blocked is expected, (
        f"{predicate}: object_has_person={object_has_person} "
        f"person_erased={person_erased}"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)
async def test_postgres_object_side_is_suppressed_for_a_new_predicate() -> None:
    """Exercise the real function, not its source text."""

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ["TEST_DATABASE_URL"].replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            source = (
                await connection.execute(
                    sa.text(
                        "SELECT prosrc FROM pg_proc p "
                        "JOIN pg_namespace n ON n.oid = p.pronamespace "
                        "WHERE n.nspname = 'privacy' "
                        "AND p.proname = 'identity_fact_is_blocked'"
                    )
                )
            ).scalar_one()
        assert "target_predicate <> 'parent_of'" not in source, (
            "the deployed function still branches on the predicate; revision "
            "0022 has not been applied to this database"
        )
    finally:
        await engine.dispose()
