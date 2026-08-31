"""A migration must not recreate what the greenfield revision already built.

``0001_greenfield_core`` builds a database with ``metadata.create_all()`` from
``app/schema.py``, which describes the CURRENT shape of everything. So a fresh
database already carries every index that module declares -- including ones a
later revision believes it is creating for the first time.

Revision 0023 created ``uq_active_partner_relationship`` with a bare
``CREATE UNIQUE INDEX``. On production that worked, because production reached
0023 from a database built by an older ``app/schema.py`` that did not declare
it yet. On a greenfield chain it fails outright:

    DuplicateTable: relation "uq_active_partner_relationship" already exists

It stayed invisible for a different reason worth recording: the hosted gate
stopped migrating at 0021, so revisions 0022 through 0027 had never been
executed by CI at all. The defect was not missed by a weak test -- there was no
run.

This is the same family as ``test_phase3_frozen_migration_ddl_e5n``: both are
about a migration's meaning shifting because ``app/schema.py`` moved underneath
it.
"""

from __future__ import annotations

import pathlib
import re

CORE = pathlib.Path(__file__).resolve().parents[1]
VERSIONS = CORE / "alembic" / "versions"
SCHEMA = CORE / "app" / "schema.py"


def _greenfield_index_names() -> set[str]:
    """Index names app/schema.py declares, so greenfield already creates them."""

    source = SCHEMA.read_text()
    names: set[str] = set()
    for match in re.finditer(r"Index\(\s*\n?\s*[\"']([a-z_]+)[\"']", source):
        names.add(match.group(1))
    for match in re.finditer(r"name=[\"']([a-z_]+)[\"']", source):
        names.add(match.group(1))
    return names


def test_migrations_do_not_recreate_greenfield_indexes() -> None:
    """A bare CREATE INDEX on a greenfield-declared name aborts the chain."""

    greenfield = _greenfield_index_names()
    problems = []
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name.startswith("0001"):
            continue
        source = path.read_text()
        for match in re.finditer(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(IF NOT EXISTS\s+)?([a-z_]+)", source
        ):
            guarded, name = match.group(1), match.group(2)
            if name in greenfield and not guarded:
                problems.append(
                    f"{path.name} creates index {name} with a bare CREATE, but "
                    "app/schema.py declares it so a greenfield database already "
                    "has it; the migration chain aborts with DuplicateTable"
                )
    assert not problems, "\n".join(problems)


def test_the_partner_index_agrees_with_the_greenfield_definition() -> None:
    """IF NOT EXISTS is only safe while the two definitions still match.

    Skipping creation is the right behaviour when the existing index is the one
    the migration would have made. If app/schema.py later changes the predicate
    and the migration does not, the greenfield database would silently keep a
    different index and nothing would say so -- exactly the divergence that made
    the E3 catalog manifest unreachable.
    """

    # 0031 replaced 0023's and 0026's predicate-scoped indexes with a single
    # index keyed on predicate, so the index app/schema.py declares -- and that a
    # greenfield database therefore already carries -- is that one. The pairing
    # this test exists to protect is unchanged: the migration that creates an
    # index and the greenfield definition of it must not drift apart.
    migration = (VERSIONS / "0031_relationship_uniqueness.py").read_text()
    schema = SCHEMA.read_text()

    index = schema[schema.index('"uq_active_relationship"'):]
    index = index[: index.index(")\n\n")]

    for column in ("subject_id", "predicate", "person_id", "perspective_principal_id"):
        assert column in index, f"greenfield index lost {column}"
        assert column in migration, f"migration index lost {column}"

    clauses = (
        "colleague_of",
        "friend_of",
        "neighbor_of",
        "parent_of",
        "partner_of",
        "roommate_of",
        "sibling_of",
        "upper_inf(system_range)",
        "resolution = 'accepted'",
    )
    for clause in clauses:
        assert clause in index, f"greenfield predicate lost {clause}"
        assert clause in migration, f"migration predicate lost {clause}"
