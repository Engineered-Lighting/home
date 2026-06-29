"""test_eval_anticipator.py — Phase 2 eval harness, Component 2.0.

Hand-constructed fixtures that exercise the analyzer's two load-bearing
pieces:
  - identify_walks: walk identification with bucket classification + exclusions
  - replay_brightness: linear ramp model with τ sweep

Assertion-style. MUST PASS before the analyzer is allowed to run on real
JSONL data. The adversarial review (round 4, Attack #3) flagged that
without these tests the Day-7 numbers are unverifiable.

Run: `python tools/test_eval_anticipator.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_anticipator import (
    identify_walks, replay_brightness, Walk,
    VACANT_DAY_PCT, ANTICIPATED_PCT,
    DIRECT_PAUSED_BOUNDARY_S, MANUAL_COOLDOWN_S, TARGET_DWELL_MIN_S,
)


# Minimal zone_to_room mapping — covers fixtures' rooms.
Z2R = {
    "sofa":        "living_room",
    "front_left":  "living_room",
    "dining_left": "dining_room",
    "dining_right":"dining_room",
    "sink":        "kitchen",
    "island_left": "kitchen",
    "office":      "living_room",
}


def occ(ts: float, zone: str, on: bool) -> dict:
    """Build an occupancy state-change event."""
    return {
        "ts": ts,
        "source": "ha_ws",
        "entity_id": f"binary_sensor.{zone}_person_occupancy",
        "old": "off" if on else "on",
        "new": "on" if on else "off",
        "attrs": {},
    }


def manual_event(ts: float, zone: str) -> dict:
    return {
        "ts": ts,
        "source": "ha_ws",
        "entity_id": f"input_datetime.living_lights_zone_{zone}_last_manual_at",
        "old": "2020-01-01 00:00:00",
        "new": "2026-05-26 12:00:00",
        "attrs": {},
    }


def anticipated(ts: float, room: str, on: bool) -> dict:
    return {
        "ts": ts,
        "source": "mqtt_anticipated",
        "room": room,
        "state": "ON" if on else "OFF",
    }


def _passing(name: str, cond: bool, detail: str = ""):
    status = "OK  " if cond else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line)
    return cond


# ─────────────────────────────────────────────────────────────────────────
# Fixtures + assertions
# ─────────────────────────────────────────────────────────────────────────

passing_count = 0
failing_count = 0

def assert_eq(name: str, got, expected):
    global passing_count, failing_count
    ok = got == expected
    if not ok:
        detail = f"got={got!r} expected={expected!r}"
    else:
        detail = f"= {got!r}"
    if _passing(name, ok, detail):
        passing_count += 1
    else:
        failing_count += 1


def assert_within(name: str, got: float, expected: float, tol: float):
    global passing_count, failing_count
    ok = abs(got - expected) <= tol
    detail = f"got={got:.2f} expected={expected:.2f} ± {tol}"
    if _passing(name, ok, detail):
        passing_count += 1
    else:
        failing_count += 1


def assert_true(name: str, cond: bool, detail: str = ""):
    global passing_count, failing_count
    if _passing(name, cond, detail):
        passing_count += 1
    else:
        failing_count += 1


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 1 — clean direct walk LR → DR
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 1: clean direct walk (LR → DR) ===")

f1 = [
    # User starts in LR sofa
    occ(99.0, "sofa", True),
    # Anticipator fires ON for dining_room as user starts walking
    anticipated(101.0, "dining_room", True),
    # User leaves LR (sofa OFF) at t=104
    occ(104.0, "sofa", False),
    # User arrives in DR at t=108 (4s after source-off → direct)
    occ(108.0, "dining_left", True),
    # User stays — make target dwell > 5s
    occ(120.0, "dining_left", False),
    # Prediction clears
    anticipated(108.5, "dining_room", False),
]
walks = identify_walks(f1, Z2R)
assert_eq("F1.walk count", len(walks), 1)
assert_eq("F1.source_room", walks[0].source_room, "living_room")
assert_eq("F1.target_room", walks[0].target_room, "dining_room")
assert_eq("F1.bucket", walks[0].bucket, "direct")
assert_eq("F1.excluded_reason", walks[0].excluded_reason, None)

# Replay brightness with non-flat ramp so τ matters: anticipated=70, vacant=50
# Prediction ON at t=101 → ramp begins at t=101 from vacant_floor=50.
# Arrival at t=108. dt = 7 seconds. For τ=1.5, ramp completed long ago.
# Brightness at arrival = anticipated_pct = 70.
r1 = replay_brightness(
    [{"ts": 101.0, "room": "dining_room", "state": "ON"},
     {"ts": 108.5, "room": "dining_room", "state": "OFF"}],
    walks[0], tau_s=1.5, anticipated_pct=70.0, vacant_floor=50.0)
assert_within("F1.brightness@arrival τ=1.5", r1["brightness_at_arrival"], 70.0, 2.0)
assert_eq("F1.flap_count", r1["flap_count"], 1)  # one ON


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 2 — boundary direct/paused (source_off → target_on at 8.5s)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 2: boundary just past direct/paused (8.5s gap) ===")

f2 = [
    occ(199.0, "sofa", True),
    occ(200.0, "sofa", False),
    occ(208.5, "dining_left", True),  # 8.5s — past DIRECT_PAUSED_BOUNDARY_S=8
    occ(215.0, "dining_left", False),
]
walks = identify_walks(f2, Z2R)
assert_eq("F2.walk count", len(walks), 1)
assert_eq("F2.bucket", walks[0].bucket, "paused")


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 3 — paused walk (15s hallway, no prediction fired)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 3: paused walk (15s gap, no prediction) ===")

f3 = [
    occ(299.0, "sofa", True),
    occ(300.0, "sofa", False),
    occ(315.0, "dining_left", True),  # 15s pause
    occ(330.0, "dining_left", False),
]
walks = identify_walks(f3, Z2R)
assert_eq("F3.walk count", len(walks), 1)
assert_eq("F3.bucket", walks[0].bucket, "paused")

# No anticipated events → brightness stays at vacant_floor.
r3 = replay_brightness([], walks[0], tau_s=1.5,
                       anticipated_pct=70.0, vacant_floor=50.0)
assert_within("F3.brightness@arrival (no prediction)",
              r3["brightness_at_arrival"], 50.0, 0.5)


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 4 — chained walks (LR → DR → KIT)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 4: chained walks (LR→DR→KIT) ===")

f4 = [
    # User in LR
    occ(399.0, "sofa", True),
    # LR off at 400
    occ(400.0, "sofa", False),
    # DR on at 403 (direct, 3s)
    occ(403.0, "dining_left", True),
    # DR off at 405 — quick passthrough. Use dining_left only to keep
    # the DR room as a single zone. target_dwell = 2s < TARGET_DWELL_MIN.
    # That will mark SHORT_DWELL. To avoid that, dwell longer.
    occ(415.0, "dining_left", False),  # 12s dwell — fine
    # User walks DR→KIT — second walk starts at 415, KIT on at 418 (3s, direct)
    occ(418.0, "sink", True),
    occ(430.0, "sink", False),
]
walks = identify_walks(f4, Z2R)
# Note: the second walk's source-off is 415, which is > CHAINED_GAP_MAX_S
# (5s) after the first walk's end (403). So bucket=direct, not chained.
# Adjust fixture to make truly chained:
f4_chained = [
    occ(399.0, "sofa", True),
    occ(400.0, "sofa", False),       # LR off
    occ(403.0, "dining_left", True), # DR on (walk 1 ends here at t=403)
    # DR off at t=407 (4s after walk-1 end → chained relative to walk-1)
    # but target_dwell = 4s < TARGET_DWELL_MIN_S=5; will be short_dwell.
    # To keep the chain logic clean: make first dwell 4s but accept short_dwell?
    # Better: dwell 6s, but then the gap is 6 > CHAINED_GAP_MAX_S=5.
    # Tricky to satisfy BOTH "long enough dwell" AND "chained" with current
    # thresholds. The realistic case is: brief transit through DR. So accept
    # short_dwell exclusion AND test chained on the SECOND walk's labelling.
    occ(407.0, "dining_left", False),  # short_dwell — walk 1 excluded
    occ(410.0, "sink", True),          # walk 2 starts at 407, ends 410
    occ(420.0, "sink", False),
]
walks = identify_walks(f4_chained, Z2R)
assert_eq("F4.walk count", len(walks), 2)
# First walk: short_dwell exclusion
assert_eq("F4.walk1 source", walks[0].source_room, "living_room")
assert_eq("F4.walk1 target", walks[0].target_room, "dining_room")
assert_eq("F4.walk1 excluded", walks[0].excluded_reason, "short_dwell")
# Second walk: chained (started within 5s of walk1's end)
assert_eq("F4.walk2 source", walks[1].source_room, "dining_room")
assert_eq("F4.walk2 target", walks[1].target_room, "kitchen")
assert_eq("F4.walk2 bucket", walks[1].bucket, "chained")


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 5 — orphan source-off (left source room, never appeared in any other)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 5: orphan walk ===")

f5 = [
    occ(499.0, "sofa", True),
    occ(500.0, "sofa", False),
    # ORPHAN_WINDOW_S=60; "present_ts" of last event determines whether
    # this source-off is old enough to be considered orphan.
    # Make a far-future filler event so present_ts - 500 > 60.
    {"ts": 600.0, "source": "eval_logger", "event": "filler"},
]
walks = identify_walks(f5, Z2R)
assert_eq("F5.walk count", len(walks), 1)
assert_eq("F5.bucket", walks[0].bucket, "orphan")
assert_eq("F5.target_room", walks[0].target_room, None)


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 6 — manual-cooldown exclusion
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 6: manual cooldown exclusion ===")

f6 = [
    occ(599.0, "sofa", True),
    # Manual tap 10s before source-off — within 60s cooldown window of walk
    manual_event(590.0, "sofa"),
    occ(600.0, "sofa", False),
    occ(603.0, "dining_left", True),
    occ(615.0, "dining_left", False),
]
walks = identify_walks(f6, Z2R)
assert_eq("F6.walk count", len(walks), 1)
assert_eq("F6.bucket (raw)", walks[0].bucket, "direct")
assert_eq("F6.excluded", walks[0].excluded_reason, "manual_cooldown")


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 7 — flapping prediction
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 7: flapping prediction (ON/OFF/ON/OFF/ON) ===")

f7 = [
    occ(699.0, "sofa", True),
    occ(700.0, "sofa", False),
    # Many flaps before arrival at 706
    anticipated(700.5, "dining_room", True),
    anticipated(701.5, "dining_room", False),
    anticipated(702.0, "dining_room", True),
    anticipated(703.0, "dining_room", False),
    anticipated(703.5, "dining_room", True),
    anticipated(704.5, "dining_room", False),
    anticipated(705.0, "dining_room", True),
    occ(706.0, "dining_left", True),
    occ(720.0, "dining_left", False),
    anticipated(706.5, "dining_room", False),
]
walks = identify_walks(f7, Z2R)
assert_eq("F7.walk count", len(walks), 1)

predictions_f7 = [e for e in f7 if e.get("source") == "mqtt_anticipated"]
# 8 state changes within walk window (ts <= 706.0): 4 ON + 3 OFF + final ON
# Let me count: 700.5 ON, 701.5 OFF, 702 ON, 703 OFF, 703.5 ON, 704.5 OFF,
# 705 ON. That's 7 changes within window. The 706.5 OFF is past arrival.
r7 = replay_brightness(predictions_f7, walks[0], tau_s=1.5,
                       anticipated_pct=70.0, vacant_floor=50.0)
assert_true("F7.flap_count >= 5", r7["flap_count"] >= 5,
            detail=f"flap_count={r7['flap_count']}")


# ─────────────────────────────────────────────────────────────────────────
# FIXTURE 8 — τ-monotonicity (smaller τ ⇒ higher brightness at fixed
#                arrival when prediction fires CLOSE to arrival)
# ─────────────────────────────────────────────────────────────────────────
print("\n=== FIXTURE 8: τ-monotonicity ===")

# Synthetic walk + a single late prediction
fake_walk = Walk(
    source_off_ts=800.0, target_on_ts=804.0,
    source_room="living_room", target_room="dining_room", bucket="direct"
)
# Prediction ON at t=803 (1s before arrival) — ramp won't fully complete for
# τ=2.0 by arrival (only 50% of the way).
preds = [{"ts": 803.0, "room": "dining_room", "state": "ON"}]
b_10 = replay_brightness(preds, fake_walk, tau_s=1.0,
                         anticipated_pct=70.0, vacant_floor=50.0)["brightness_at_arrival"]
b_15 = replay_brightness(preds, fake_walk, tau_s=1.5,
                         anticipated_pct=70.0, vacant_floor=50.0)["brightness_at_arrival"]
b_20 = replay_brightness(preds, fake_walk, tau_s=2.0,
                         anticipated_pct=70.0, vacant_floor=50.0)["brightness_at_arrival"]
print(f"  brightness τ=1.0 → {b_10:.2f}")
print(f"  brightness τ=1.5 → {b_15:.2f}")
print(f"  brightness τ=2.0 → {b_20:.2f}")
assert_true("F8.τ=1.0 reaches target (=70)",
            abs(b_10 - 70.0) <= 1.0, detail=f"b={b_10}")
assert_true("F8.τ=1.5 partial ramp (b ~63.3)",
            abs(b_15 - 63.33) <= 1.0, detail=f"b={b_15}")
assert_true("F8.τ=2.0 partial ramp (b=60)",
            abs(b_20 - 60.0) <= 1.0, detail=f"b={b_20}")
assert_true("F8.monotonic (b_10 >= b_15 >= b_20)",
            b_10 >= b_15 >= b_20)


# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"PASSED: {passing_count}    FAILED: {failing_count}")
if failing_count:
    raise SystemExit(1)
print("All fixture assertions passed.")
