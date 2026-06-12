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

# The value vocabulary below is the USER'S spec verbatim (plan §Label Schema) —
# it must stay in lockstep with the frontend's frozen VL_* lists.
ACTIVITY_PRIMARY = (
    "cooking",
    "food_prep",
    "eating_drinking",
    "washing_dishes",
    "cleaning",
    "laundry",
    "organizing",
    "reading",
    "watching_tv",
    "working_computer",
    "phone_use",
    "conversation",
    "resting",
    "sleeping",
    "exercising",
    "stretching_yoga",
    "walking",
    "entering_leaving",
    "personal_care",
    "pet_care",
    "idle_present",
    "no_person",
    "unknown",
)

POSTURE = (
    "standing",
    "walking",
    "sitting_upright",
    "sitting_reclined",
    "lying_down",
    "bending",
    "crouching",
    "kneeling",
    "reaching",
    "leaning",
    "exercising_dynamic",
    "partially_visible",
    "no_person",
    "unknown",
)

QUALITY_FLAGS = (
    "clear",
    "occluded",
    "dark",
    "blurry",
    "backlit",
    "partial_body",
    "multiple_people",
    "ambiguous",
    "private_skip",
    "screen_sensitive",
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

# Seed ACTIVE SET: the M2 VLM constrained enum is ACTIVE_SET + a literal
# "other" (which routes to the custom-label flow — "other" itself is NOT a
# canonical value); classes activate at >= N human-reviewed examples.
ACTIVE_SET = (
    "no_person",
    "unknown",
    "walking",
    "cooking",
    "eating_drinking",
    "washing_dishes",
    "watching_tv",
    "working_computer",
    "phone_use",
    "idle_present",
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
