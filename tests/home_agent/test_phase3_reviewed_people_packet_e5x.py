from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
PACKET = OPERATOR / "phase3_reviewed_people_packet.py"


def _module() -> ModuleType:
    sys.path.insert(0, str(OPERATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_phase3_reviewed_people_packet_e5x", PACKET
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(OPERATOR))


def _plan(module, *, include_second_parent: bool = True):
    migration = sys.modules["migrate_legacy_identity"]
    people = (
        migration.PersonCandidate(
            "11111111-1111-4111-8111-111111111111",
            "Marcelo",
            None,
            7,
            True,
            False,
            False,
            False,
            False,
            None,
            False,
        ),
        migration.PersonCandidate(
            "22222222-2222-4222-8222-222222222222",
            "Amelia",
            None,
            4,
            True,
            False,
            False,
            False,
            False,
            None,
            False,
        ),
        migration.PersonCandidate(
            "33333333-3333-4333-8333-333333333333",
            "Marcelo Sr.",
            None,
            5,
            True,
            False,
            False,
            False,
            False,
            None,
            False,
        ),
    )
    roles = [
        migration.RoleCandidate(
            people[0].person_id, "me", "relationship_type", 7, "a" * 64, "me-id"
        ),
        migration.RoleCandidate(
            people[1].person_id,
            "parent",
            "relationship_subrole",
            4,
            "b" * 64,
            "parent-one",
        ),
    ]
    if include_second_parent:
        roles.append(
            migration.RoleCandidate(
                people[2].person_id,
                "parent",
                "relationship_subrole",
                5,
                "c" * 64,
                "parent-two",
            )
        )
    return migration.MigrationPlan(
        schema_version=1,
        people=people,
        aliases=(migration.AliasCandidate(people[1].person_id, "Mom", "nickname"),),
        external_bindings=(
            migration.ExternalBindingCandidate(
                people[1].person_id, "frigate", "amelia", "active"
            ),
        ),
        role_candidates=tuple(roles),
        relationship_candidates=(),
        digest="d" * 64,
    )


def test_private_review_preserves_people_evidence_without_creating_parent_facts() -> (
    None
):
    module = _module()
    artifact = module.compile_private_review(
        _plan(module), sqlite_snapshot_sha256="a" * 64
    )
    review = artifact["private_review"]
    assert review["sqlite_snapshot_sha256"] == "a" * 64

    assert review["slice_readiness"] == {
        "blockers": [],
        "me_candidate_person_ids": ["11111111-1111-4111-8111-111111111111"],
        "parent_candidate_person_ids": [
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
        ],
        "ready_for_private_content_review": True,
    }
    assert review["migration_semantics"]["authoritative_parent_facts_created"] == 0
    assert review["migration_semantics"]["principal_binding_created"] is False
    amelia = next(item for item in review["people"] if item["display_name"] == "Amelia")
    assert amelia["aliases"] == [{"alias": "Mom", "alias_kind": "nickname"}]
    assert amelia["recognition_bindings"] == [
        {"external_id": "amelia", "external_system": "frigate", "status": "active"}
    ]
    assert amelia["legacy_role_labels"][0]["authoritative"] is False
    assert amelia["legacy_role_labels"][0]["perspective"] == "unknown"


def test_review_fails_readiness_when_the_exact_candidate_set_is_missing() -> None:
    module = _module()
    review = module.compile_private_review(
        _plan(module, include_second_parent=False),
        sqlite_snapshot_sha256="a" * 64,
    )["private_review"]

    assert review["slice_readiness"]["ready_for_private_content_review"] is False
    assert review["slice_readiness"]["blockers"] == [
        "exactly_two_legacy_parent_candidates_not_established"
    ]


def test_ignored_people_are_content_suppressed_before_review() -> None:
    module = _module()
    migration = sys.modules["migrate_legacy_identity"]
    plan = _plan(module)
    ignored = migration.PersonCandidate(
        "44444444-4444-4444-8444-444444444444",
        "Private Guest",
        "they/them",
        2,
        True,
        True,
        True,
        False,
        True,
        None,
        False,
    )
    plan = migration.MigrationPlan(
        plan.schema_version,
        plan.people + (ignored,),
        plan.aliases + (migration.AliasCandidate(ignored.person_id, "Secret", "name"),),
        plan.external_bindings
        + (
            migration.ExternalBindingCandidate(
                ignored.person_id, "frigate", "secret", "active"
            ),
        ),
        plan.role_candidates
        + (
            migration.RoleCandidate(
                ignored.person_id,
                "friend",
                "relationship_type",
                2,
                "e" * 64,
                "friend-id",
            ),
        ),
        (),
        plan.digest,
    )

    review = module.compile_private_review(plan, sqlite_snapshot_sha256="a" * 64)[
        "private_review"
    ]
    subject = next(
        item for item in review["people"] if item["person_id"] == ignored.person_id
    )
    assert subject["display_name"] == "[suppressed legacy identity]"
    assert subject["aliases"] == []
    assert subject["recognition_bindings"] == []
    assert subject["legacy_role_labels"] == []
    assert {item["directive"] for item in subject["privacy_directives"]} == {
        "do_not_track",
        "ignored",
        "private",
        "silent",
    }


def test_cli_is_fixed_path_root_only_and_content_minimized() -> None:
    source = PACKET.read_text(encoding="utf-8")
    assert 'Path("/srv/home-agent/private/phase3-identity")' in source
    assert "len(sys.argv) != 1" in source
    assert "os.geteuid() != 0" in source
    assert "0o700" in source
    assert "0o600" in source
    assert "private_review_sha256" in source
    assert '"people": people' not in source.split("def stage()", 1)[1]
    assert 'authoritative_parent_facts_created": 0' in source


def _two_column_plan(module: ModuleType):
    """A plan shaped like the real legacy store.

    Every identity row carries a `relationship_type`, and rows that name a more
    specific relationship also carry a `relationship_subrole`. The original
    fixture gave each person a single candidate, so it never exercised the pair
    that the registration kernel rejects.
    """

    migration = sys.modules["migrate_legacy_identity"]
    plan = _plan(module)
    people = plan.people
    roles = (
        # Type only -- no subrole on the legacy row.
        migration.RoleCandidate(
            people[0].person_id, "me", "relationship_type", 7, "a" * 64, "me-type"
        ),
        # Type + subrole on one row, in both orderings.
        migration.RoleCandidate(
            people[1].person_id,
            "family_immediate",
            "relationship_type",
            4,
            "b" * 64,
            "amelia-type",
        ),
        migration.RoleCandidate(
            people[1].person_id,
            "parent",
            "relationship_subrole",
            4,
            "b" * 64,
            "amelia-subrole",
        ),
        migration.RoleCandidate(
            people[2].person_id,
            "parent",
            "relationship_subrole",
            5,
            "c" * 64,
            "senior-subrole",
        ),
        migration.RoleCandidate(
            people[2].person_id,
            "family_immediate",
            "relationship_type",
            5,
            "c" * 64,
            "senior-type",
        ),
    )
    return migration.MigrationPlan(
        schema_version=plan.schema_version,
        people=people,
        aliases=plan.aliases,
        external_bindings=plan.external_bindings,
        role_candidates=roles,
        relationship_candidates=plan.relationship_candidates,
        digest=plan.digest,
    )


def _labels(review) -> dict[str, list[dict]]:
    return {
        person["person_id"]: person["legacy_role_labels"]
        for person in review["people"]
    }


def test_one_role_candidate_per_person_survives_the_two_column_legacy_shape() -> None:
    """The 0008 kernel refuses two decisions of one kind on one source item.

    A legacy row splits one relationship across `relationship_type` and
    `relationship_subrole`. Emitting both made every packet containing a
    subrole unregistrable.
    """

    module = _module()
    review = module.compile_private_review(
        _two_column_plan(module), sqlite_snapshot_sha256="a" * 64
    )["private_review"]

    for person_id, labels in _labels(review).items():
        assert len(labels) <= 1, f"{person_id} carries {len(labels)} role candidates"


def test_the_subrole_wins_and_the_type_is_the_fallback() -> None:
    """The specific term carries more than the enum it implies."""

    module = _module()
    review = module.compile_private_review(
        _two_column_plan(module), sqlite_snapshot_sha256="a" * 64
    )["private_review"]
    chosen = {
        person_id: labels[0]["role_label"]
        for person_id, labels in _labels(review).items()
        if labels
    }

    assert chosen == {
        "11111111-1111-4111-8111-111111111111": "me",  # type, no subrole
        "22222222-2222-4222-8222-222222222222": "parent",  # subrole beats type
        "33333333-3333-4333-8333-333333333333": "parent",  # order does not matter
    }


def test_the_collapse_keeps_both_readiness_scans_resolving() -> None:
    """`slice_readiness` matches exact strings, and the two live on
    opposite columns: "me" is only ever a type, "parent" only ever a subrole.
    A collapse that dropped either column would strand the parent slice."""

    module = _module()
    review = module.compile_private_review(
        _two_column_plan(module), sqlite_snapshot_sha256="a" * 64
    )["private_review"]

    assert review["slice_readiness"]["me_candidate_person_ids"] == [
        "11111111-1111-4111-8111-111111111111"
    ]
    assert review["slice_readiness"]["parent_candidate_person_ids"] == [
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]


def test_the_collapse_is_deterministic_under_input_reordering() -> None:
    """The review is signed, so the same plan must compile byte-identically."""

    module = _module()
    migration = sys.modules["migrate_legacy_identity"]
    plan = _two_column_plan(module)
    reversed_plan = migration.MigrationPlan(
        schema_version=plan.schema_version,
        people=plan.people,
        aliases=plan.aliases,
        external_bindings=plan.external_bindings,
        role_candidates=tuple(reversed(plan.role_candidates)),
        relationship_candidates=plan.relationship_candidates,
        digest=plan.digest,
    )

    first = module.compile_private_review(plan, sqlite_snapshot_sha256="a" * 64)
    second = module.compile_private_review(
        reversed_plan, sqlite_snapshot_sha256="a" * 64
    )

    assert first == second
