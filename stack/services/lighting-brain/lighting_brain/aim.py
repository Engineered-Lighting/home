"""Over-shoulder aim planner: hand-region point, fixture choice, head sphere.

Pan/tilt feasibility is a port of Home's aim solver so the brain and the
renderer agree on what a fixture can reach:

* ``solveAim`` and ``calibratedBasis`` in app/src/home-apartment-aiming.js
  (pan = atan2(delta . right, delta . forward), tilt = atan2(-dz, horizontal),
  positive tilt points down, default limits pan +/-180, tilt +/-90, a target
  straight below the origin leaves pan indeterminate and is not executable).
* ``aimAtTarget`` in app/src/home-3d/equipment-virtual-fixtures.mjs, which
  applies the same formulas from the fixture's ``display_origin_m`` with its
  ``horizontal_world_reference`` (forward = reference, right = (ref.y, -ref.x)).

The room frame is Z-up metres, floor at z=0 (the registered den frame).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import DEFAULT_LIMITS, Fixture, Vec
from .settings import Settings

MIN_SHOULDER_SPAN_M = 0.10
DOWN: Vec = (0.0, 0.0, -1.0)


def add(a, b) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, k) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a) -> float:
    return math.sqrt(dot(a, a))


def unit(a, fallback: Vec | None = None) -> Vec | None:
    n = norm(a)
    if n < 1e-12:
        return fallback
    return scale(a, 1.0 / n)


def horizontal(a) -> Vec:
    return (a[0], a[1], 0.0)


def calibrated_basis(reference) -> tuple[Vec, Vec, Vec]:
    """home-apartment-aiming.js calibratedBasis: forward, right, down."""
    forward = unit(horizontal(reference), (0.0, 1.0, 0.0))
    right = unit((forward[1], -forward[0], 0.0), (1.0, 0.0, 0.0))
    return forward, right, DOWN


@dataclass(frozen=True)
class AimSolution:
    ok: bool
    executable: bool
    pan_deg: float | None = None
    tilt_deg: float | None = None
    distance_m: float | None = None
    violations: tuple[str, ...] = ()
    ambiguity: str | None = None
    reason: str | None = None


def solve_aim(origin, target, reference, limits: dict | None = None,
              current_pan_deg: float | None = None) -> AimSolution:
    """Python port of solveAim (home-apartment-aiming.js), without the raw
    encoder conversion: the virtual fixtures have no calibration."""
    delta = sub(target, origin)
    horizontal_distance = math.hypot(delta[0], delta[1])
    distance = norm(delta)
    if distance < 1e-9:
        return AimSolution(ok=False, executable=False, reason="target equals aiming origin")
    forward, right, _down = calibrated_basis(reference)
    vertical = horizontal_distance < 1e-7
    if vertical:
        pan = current_pan_deg if current_pan_deg is not None and math.isfinite(current_pan_deg) else 0.0
    else:
        pan = math.degrees(math.atan2(dot(delta, right), dot(delta, forward)))
    tilt = math.degrees(math.atan2(-delta[2], horizontal_distance))
    lim = {**DEFAULT_LIMITS, **(limits or {})}
    violations = []
    if pan < lim["pan_min_deg"] or pan > lim["pan_max_deg"]:
        violations.append("pan_limit")
    if tilt < lim["tilt_min_deg"] or tilt > lim["tilt_max_deg"]:
        violations.append("tilt_limit")
    return AimSolution(ok=True, executable=not vertical and not violations, pan_deg=pan, tilt_deg=tilt,
                       distance_m=distance, violations=tuple(violations),
                       ambiguity="vertical_target_pan_indeterminate" if vertical else None)


def direction_from_aim(pan_deg: float, tilt_deg: float, reference) -> Vec:
    """home-apartment-aiming.js directionFromAim (used by tests as a check)."""
    forward, right, down = calibrated_basis(reference)
    p, t = math.radians(pan_deg), math.radians(tilt_deg)
    flat = add(scale(forward, math.cos(p)), scale(right, math.sin(p)))
    return unit(add(scale(flat, math.cos(t)), scale(down, math.sin(t))))


def segment_point_distance(a, b, p) -> float:
    ab = sub(b, a)
    length_sq = dot(ab, ab)
    if length_sq < 1e-12:
        return norm(sub(p, a))
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / length_sq))
    return norm(sub(p, add(a, scale(ab, t))))


@dataclass(frozen=True)
class Reader:
    """One placed person, from a person_pose event (approximate 3D)."""
    shoulder_mid: Vec
    facing: Vec  # horizontal unit vector
    facing_source: str
    head: Vec
    head_source: str
    hand: Vec


def place_reader(left, right, head_m, head_pitch_deg, camera_m, settings: Settings) -> tuple[Reader | None, str | None]:
    """CONTRACT "Hand-region point": shoulder midpoint, ~0.35 m forward along
    the facing direction (tilted by head pitch, positive = looking down), then
    ~0.25 m down.

    Facing is perpendicular to the shoulder line. With pose keypoints labelled
    anatomically, facing = z x (right - left). When the camera position is
    known the contract rule wins: facing points away from the camera (the
    camera sees the reader's back)."""
    if left is None or right is None:
        return None, "shoulders_missing"
    mid = scale(add(left, right), 0.5)
    span = horizontal(sub(right, left))
    if norm(span) < MIN_SHOULDER_SPAN_M:
        return None, "shoulders_too_close"
    facing = unit((-span[1], span[0], 0.0))
    source = "shoulder_labels"
    if camera_m is not None:
        away = horizontal(sub(mid, camera_m))
        if norm(away) > 1e-6:
            source = "away_from_camera"
            if dot(facing, away) < 0:
                facing = scale(facing, -1.0)
    pitch = head_pitch_deg if head_pitch_deg is not None else 0.0
    pitch = max(settings.head_pitch_min_deg, min(settings.head_pitch_max_deg, pitch))
    p = math.radians(pitch)
    look = add(scale(facing, math.cos(p)), scale(DOWN, math.sin(p)))
    hand = add(add(mid, scale(look, settings.hand_forward_m)), (0.0, 0.0, -settings.hand_down_m))
    if head_m is not None:
        head, head_source = tuple(head_m), "pose"
    else:
        head, head_source = add(mid, (0.0, 0.0, settings.head_fallback_up_m)), "estimated_from_shoulders"
    return Reader(mid, facing, source, head, head_source, hand), None


@dataclass(frozen=True)
class Candidate:
    fixture_id: str
    ok: bool
    reason: str
    score: float | None = None
    behind_cos: float | None = None
    above_head_m: float | None = None
    pan_deg: float | None = None
    tilt_deg: float | None = None
    head_clearance_m: float | None = None


@dataclass(frozen=True)
class SpotPlan:
    ok: bool
    reason: str
    fixture_id: str | None = None
    target_m: Vec | None = None
    pan_deg: float | None = None
    tilt_deg: float | None = None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)


def evaluate_fixture(fixture: Fixture, reader: Reader, settings: Settings) -> Candidate:
    fid = fixture.fixture_id
    origin = fixture.aim_origin_m
    if origin is None:
        return Candidate(fid, False, "no_registered_position")
    solution = solve_aim(origin, reader.hand, fixture.horizontal_world_reference, fixture.limits)
    if not solution.ok or not solution.executable:
        detail = solution.violations or tuple(x for x in (solution.ambiguity, solution.reason) if x)
        return Candidate(fid, False, "pan_tilt_infeasible:" + ",".join(detail),
                         pan_deg=solution.pan_deg, tilt_deg=solution.tilt_deg)
    if solution.distance_m > settings.max_beam_m:
        return Candidate(fid, False, "too_far", pan_deg=solution.pan_deg, tilt_deg=solution.tilt_deg)
    clearance = segment_point_distance(origin, reader.hand, reader.head)
    if clearance < settings.head_clearance_m:
        return Candidate(fid, False, "beam_through_head", pan_deg=solution.pan_deg,
                         tilt_deg=solution.tilt_deg, head_clearance_m=clearance)
    to_fixture = unit(horizontal(sub(origin, reader.shoulder_mid)), (0.0, 0.0, 0.0))
    behind_cos = dot(to_fixture, scale(reader.facing, -1.0))
    above = origin[2] - reader.head[2]
    if behind_cos < settings.min_behind_cos:
        return Candidate(fid, False, "in_front_of_reader", behind_cos=behind_cos, above_head_m=above,
                         pan_deg=solution.pan_deg, tilt_deg=solution.tilt_deg, head_clearance_m=clearance)
    # Prefer behind (cosine up to 1), then above head height, then nearer.
    score = behind_cos + (0.5 if above > 0 else -0.5) - 0.02 * solution.distance_m
    return Candidate(fid, True, "ok", score, behind_cos, above, solution.pan_deg, solution.tilt_deg, clearance)


def plan_spot(reader: Reader, fixtures, settings: Settings, excluded=frozenset(),
              prefer: str | None = None) -> SpotPlan:
    """Pick the best behind/above fixture with a clear beam to the hand point.
    `prefer` (the fixture already lit) gets a small bonus so near-ties don't
    hop between fixtures."""
    candidates = []
    for fixture in fixtures:
        if fixture.fixture_id in excluded:
            candidates.append(Candidate(fixture.fixture_id, False, "paused"))
            continue
        candidates.append(evaluate_fixture(fixture, reader, settings))
    usable = [c for c in candidates if c.ok]
    if not usable:
        reasons = sorted({c.reason.split(":")[0] for c in candidates})
        return SpotPlan(False, "no_clear_fixture:" + ",".join(reasons), candidates=tuple(candidates))
    best = max(usable, key=lambda c: (c.score + (0.15 if c.fixture_id == prefer else 0.0), c.fixture_id))
    return SpotPlan(True, "ok", best.fixture_id, reader.hand, best.pan_deg, best.tilt_deg, tuple(candidates))
