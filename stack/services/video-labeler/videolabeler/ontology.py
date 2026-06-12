"""Canonical label ontology + ACTIVE SET + custom-value validation.

Shipped at M0 (M1 consumes it) so the value vocabulary is frozen alongside the
schema. Constraints: activity_primary has exactly 23 values, posture 14,
quality_flags 10, review_states 6 — asserted at import so a drive-by edit
cannot silently change the contract.
"""
from __future__ import annotations

import json
import re

AXES = ("activity", "posture", "quality", "custom")

ACTIVITY_PRIMARY = (
    "no_person",
    "walking",
    "standing_idle",
    "sitting_idle",
    "cooking",
    "eating",
    "drinking",
    "washing_dishes",
    "cleaning",
    "working_computer",
    "reading",
    "watching_tv",
    "phone_use",
    "exercising",
    "stretching",
    "dressing",
    "grooming",
    "sleeping",
    "lying_resting",
    "playing_instrument",
    "pet_interaction",
    "door_transit",
    "other",
)

POSTURE = (
    "standing",
    "sitting_chair",
    "sitting_couch",
    "sitting_floor",
    "lying_back",
    "lying_side",
    "lying_front",
    "crouching",
    "kneeling",
    "bending",
    "walking",
    "reaching_up",
    "transitioning",
    "unknown",
)

QUALITY_FLAGS = (
    "no_person",
    "multi_person",
    "private",
    "screen_sensitive",
    "low_light",
    "motion_blur",
    "occluded",
    "partial_view",
    "camera_artifact",
    "wrong_room",
)

REVIEW_STATES = (
    "prelabel",
    "needs_review",
    "reviewed",
    "accepted",
    "rejected",
    "excluded_from_export",
)

# Aux metadata keys carried in segments.aux_json (activity axis only).
AUX_AXES = (
    "verb",
    "object_noun",
    "room_zone",
    "attention_target",
    "motion_intensity",
    "activity_phase",
    "notes",
)
MOTION_INTENSITY = ("none", "low", "medium", "high")
ACTIVITY_PHASE = ("starting", "ongoing", "ending")

# Seed ACTIVE SET: the VLM constrained-enum starts here ('other' routes to the
# custom-label flow); classes activate at >= N human-reviewed examples.
ACTIVE_SET = (
    "no_person",
    "walking",
    "cooking",
    "eating",
    "cleaning",
    "working_computer",
    "watching_tv",
    "phone_use",
    "other",
)

# custom:<slug> — lowercase slug, 1..48 chars, [a-z0-9_-], no leading/trailing
# separator. M1's custom-label CRUD validates against this.
CUSTOM_VALUE_RE = re.compile(r"^custom:[a-z0-9](?:[a-z0-9_-]{0,46}[a-z0-9])?$")

_CANONICAL_BY_AXIS = {
    "activity": ACTIVITY_PRIMARY,
    "posture": POSTURE,
    "quality": QUALITY_FLAGS,
    "custom": (),
}


def is_custom_value(value: str) -> bool:
    return bool(CUSTOM_VALUE_RE.match(value or ""))


def is_valid_value(axis: str, value) -> bool:
    """True when ``value`` is legal for ``axis``. The quality axis takes a
    list (or JSON array string) of flags — multi-select, one lane."""
    if axis not in AXES:
        return False
    if axis == "quality":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return False
        if not isinstance(value, list) or not value:
            return False
        return all(v in QUALITY_FLAGS or is_custom_value(v) for v in value)
    if not isinstance(value, str):
        return False
    if is_custom_value(value):
        return True
    if axis == "custom":
        return False  # custom axis takes custom:<slug> values only
    return value in _CANONICAL_BY_AXIS[axis]


assert len(ACTIVITY_PRIMARY) == 23, "activity_primary contract is 23 values"
assert len(POSTURE) == 14, "posture contract is 14 values"
assert len(QUALITY_FLAGS) == 10, "quality_flags contract is 10 values"
assert len(REVIEW_STATES) == 6, "review_states contract is 6 values"
assert all(v in ACTIVITY_PRIMARY for v in ACTIVE_SET), "ACTIVE_SET must be canonical"
