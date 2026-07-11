from __future__ import annotations

import uuid

import pytest

from app.memory import (
    descriptor_preview_payload,
    preview_digest,
    propose_parents_mountain_house,
    render_travel_arrival,
    require_untampered_preview,
)


def test_itaipava_descriptor_is_perspective_scoped_and_never_ownership() -> None:
    parent_ids = [uuid.uuid4(), uuid.uuid4()]
    decision = propose_parents_mountain_house(
        exact_text="This is my parents’ mountain house.",
        authenticated=True,
        principal_bound=True,
        location_memory_opted_in=True,
        active_place_count=1,
        anchor_supported=True,
        parent_person_ids=parent_ids,
    )
    payload = descriptor_preview_payload(
        transaction_id=uuid.uuid4(),
        place_id=None,
        decision=decision,
        locator_summary={"coordinates": "encrypted", "radius_m": 40},
    )

    assert decision.state == "needs_confirmation"
    assert decision.role_expression == "parents_of(self)"
    assert decision.resolved_parent_person_ids == tuple(parent_ids)
    assert "ownership" in payload["not_asserted"]
    assert "current presence" in payload["not_asserted"]


def test_missing_parent_facts_defers_expansion_but_does_not_invent_names() -> None:
    decision = propose_parents_mountain_house(
        exact_text="This is my parents' mountain house",
        authenticated=True,
        principal_bound=True,
        location_memory_opted_in=True,
        active_place_count=1,
        anchor_supported=True,
        parent_person_ids=[],
    )

    assert decision.state == "needs_confirmation"
    assert decision.unresolved_role_expression is True
    assert decision.resolved_parent_person_ids == ()


def test_privacy_hidden_parent_keeps_role_expansion_unresolved() -> None:
    visible_parent = uuid.uuid4()
    decision = propose_parents_mountain_house(
        exact_text="This is my parents' mountain house",
        authenticated=True,
        principal_bound=True,
        location_memory_opted_in=True,
        active_place_count=1,
        anchor_supported=True,
        parent_person_ids=[visible_parent],
        role_expansion_complete=False,
    )

    assert decision.state == "needs_confirmation"
    assert decision.resolved_parent_person_ids == (visible_parent,)
    assert decision.unresolved_role_expression is True
    assert "role_expression_partially_unresolved" in {
        item.reason_code for item in decision.results
    }


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"authenticated": False}, "principal_unknown"),
        ({"principal_bound": False}, "principal_unbound"),
        ({"location_memory_opted_in": False}, "location_memory_disabled"),
        ({"active_place_count": 2}, "ambiguous_current_place"),
    ],
)
def test_hard_verifier_failures_quarantine(override: dict, reason: str) -> None:
    values = {
        "exact_text": "This is my parents' mountain house",
        "authenticated": True,
        "principal_bound": True,
        "location_memory_opted_in": True,
        "active_place_count": 1,
        "anchor_supported": True,
        "parent_person_ids": [],
    }
    values.update(override)
    decision = propose_parents_mountain_house(**values)

    assert decision.state == "quarantined"
    assert reason in {result.reason_code for result in decision.results}


def test_unresolved_locator_stays_reviewable_without_becoming_committable() -> None:
    decision = propose_parents_mountain_house(
        exact_text="This is my parents' mountain house",
        authenticated=True,
        principal_bound=True,
        location_memory_opted_in=True,
        active_place_count=1,
        anchor_supported=False,
        parent_person_ids=[],
    )

    assert decision.state == "needs_confirmation"
    assert any(
        item.rule == "location_anchor"
        and item.outcome == "fail"
        and item.reason_code == "location_unresolved"
        for item in decision.results
    )


def test_travel_greeting_is_a_deterministic_typed_template() -> None:
    assert render_travel_arrival("This is my parents' mountain retreat.") == (
        "Looks like you\u2019re back at your parents' mountain retreat."
    )
    with pytest.raises(ValueError, match="typed"):
        render_travel_arrival("This is my villa.")


def test_confirmation_digest_detects_preview_tampering() -> None:
    payload = {"transaction_id": str(uuid.uuid4()), "not_asserted": ["ownership"]}
    key = b"k" * 32
    digest = preview_digest(payload, key)
    require_untampered_preview(payload, digest, key)

    payload["not_asserted"] = []
    with pytest.raises(ValueError, match="digest"):
        require_untampered_preview(payload, digest, key)
