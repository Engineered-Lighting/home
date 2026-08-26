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

