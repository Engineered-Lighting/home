"""partner_of is a contract, not a value.

knowledge.fact_versions.predicate is varchar(128) with no CHECK, so everything
that makes a predicate safe is added deliberately somewhere else. These tests
pin the pieces that must accompany a new member of the vocabulary, so the next
one cannot be added by editing a Literal alone.
"""

from __future__ import annotations

import pathlib
import re

from app.context import ContextPredicate

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0023_partner_relationship_vocabulary.py"
)


def _members() -> tuple[str, ...]:
    return tuple(ContextPredicate.__args__)  # type: ignore[attr-defined]


def test_partner_of_is_admitted_and_the_vocabulary_stays_closed() -> None:
    members = _members()
    # Closed, and named exactly. Growth is a reviewed decision, not a refactor,
    # so this pins the whole set rather than its size: a count alone says a
    # member was added but not which, and passes just as happily if one is
    # swapped for another.
    assert set(members) == {
        "colleague_of",
        "friend_of",
        "neighbor_of",
        "parent_of",
        "partner_of",
        "roommate_of",
        "sibling_of",
        # Not a relationship between people: it relates a person to a place.
        "place_social_descriptor",
    }, members


def test_every_person_predicate_has_a_uniqueness_index() -> None:
    """A predicate without one gets no enforcement: the same pair could be
    asserted twice and both rows would be accepted and current."""

    schema = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.py"
    ).read_text()
    # One index guards every predicate now: the two it replaced each named a
    # single one, so a widened vocabulary silently left the rest unguarded.
    index = "uq_active_relationship"
    assert index in schema, "relationships have no uniqueness index"
    window = schema[schema.index(index): schema.index(index) + 700]
    for predicate in (
        "colleague_of",
        "friend_of",
        "neighbor_of",
        "parent_of",
        "partner_of",
        "roommate_of",
        "sibling_of",
    ):
        assert f"'{predicate}'" in window, f"{predicate} has no uniqueness index"
    assert "upper_inf(system_range)" in window, (
        f"{index} must be scoped to currently-believed facts, so a "
        "retracted edge does not block re-assertion"
    )
    assert "resolution = 'accepted'" in window


def test_partner_of_cannot_be_reflexive() -> None:
    """Nobody is their own partner, and a self-edge would also defeat the
    symmetric-pair invariant the commit kernel relies on."""

    migration = MIGRATION.read_text()
    assert "partner_relationship_is_not_reflexive" in migration
    assert "predicate <> 'partner_of'" in migration, (
        "the constraint must be scoped to partner_of so no other predicate's "
        "meaning is constrained"
    )
    schema = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.py"
    ).read_text()
    assert "partner_relationship_is_not_reflexive" in schema, (
        "schema.py must mirror the migration or the ORM and database diverge"
    )


def test_the_erasure_hole_is_closed_before_the_predicate_lands() -> None:
    """Ordering is the whole point.

    If partner_of existed while identity_fact_is_blocked still branched on
    'parent_of', an erased person would stay visible as the object of a
    partnership.
    """

    migration = MIGRATION.read_text()
    # Match the ordering, not the declaration syntax: migrations in this tree
    # write "down_revision: str = ...", and an assertion on the exact spelling
    # breaks on a style change while the invariant still holds.
    assert re.search(
        r'^down_revision(\s*:\s*[^=]+)? = "0022_fact_suppression_e5i"$',
        migration,
        re.M,
    ), "partner_of must land after the object-side suppression fix"


def test_this_revision_grants_no_ability_to_write_a_fact() -> None:
    """Vocabulary and invariants only. Writing needs a SECURITY DEFINER kernel
    under its own NOLOGIN role, which is a separate reviewed change."""

    migration = MIGRATION.read_text()
    for forbidden in ("CREATE FUNCTION", "SECURITY DEFINER", "GRANT ", "INSERT INTO"):
        assert forbidden not in migration, (
            f"{forbidden!r} in a vocabulary migration: writing a fact must not "
            "become possible as a side effect"
        )


def test_schema_defines_each_table_exactly_once() -> None:
    """A duplicated Table() raises only at import time, from a module far from
    the edit that caused it.

    Adding the partner index by copying the parent block dragged the following
    fact_support definition along with it, and the failure surfaced as
    "Table 'knowledge.fact_support' is already defined for this MetaData
    instance" while importing app.api.
    """

    import re

    schema = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "schema.py"
    ).read_text()
    names = re.findall(r"^([a-z_]+) = Table\(", schema, re.M)
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"tables defined more than once: {duplicates}"


def test_metadata_actually_imports() -> None:
    """The definitive check: a duplicate raises on import, not on inspection."""

    from app import schema

    assert schema.fact_versions is not None
    index_names = {index.name for index in schema.fact_versions.indexes}
    assert "uq_active_relationship" in index_names
