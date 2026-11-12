"""The one eye rule, and the over-the-shoulder ranking (coordinator plan C5, C7).

Owner's rule, after Codex's review: the light lighting you is never in front of you, and the centre of its
beam goes on your task, not through your head. Light on your back and shoulders is fine, and other people
aren't checked ("lights, not lasers"). This file reuses the lighting brain's aim.py, unchanged, through
spot_rule.py's loader:
- refusals: pan_tilt_infeasible, too_far (8 m), beam_through_head (centre line within 0.15 m of the head),
  in_front_of_reader;
- the score: behind_cos + (0.5 if above the head else -0.5) - 0.02 * distance.

The margin (owner, 2026-11-12, after step 1's report found every socket on the real sofa within 3 degrees of
side-on: "allow beside me"): a fixture not yet lit for this person needs to be beside or behind them
(behind_cos >= 0, side-on); the one already lit for them keeps its job until it's 5 degrees past side-on,
so a 1 cm shift can't flip the choice.

Standard library only. Pure.
"""
import dataclasses
import math

import joints
import spot_rule

aim = spot_rule.aim
ADMIT_BEHIND_COS = 0.0
KEEP_PAST_SIDE_ON_DEG = 5.0
KEEP_BEHIND_COS = -math.sin(math.radians(KEEP_PAST_SIDE_ON_DEG))
HEAD_CLEARANCE_M = 0.15
_ADMIT = dataclasses.replace(spot_rule.SETTINGS, min_behind_cos=ADMIT_BEHIND_COS)
_KEEP = dataclasses.replace(spot_rule.SETTINGS, min_behind_cos=KEEP_BEHIND_COS)


def evaluate(fixture_id, fixture, person, point, lit_for_them):
    """aim.py's Candidate for this fixture lighting `point` for `person`, or a refusal.

    fixture: {aim_origin_m, horizontal_world_reference}. person: {head, shoulder_mid, facing} in room
    metres, facing a horizontal unit vector. Missing head or facing refuses: no facing, no spot."""
    if not person.get("head"):
        return aim.Candidate(fixture_id, False, "no_head")
    if not person.get("facing"):
        return aim.Candidate(fixture_id, False, "no_facing")
    facing = tuple(person["facing"][:2]) + (0.0,)
    reader = aim.Reader(tuple(person["shoulder_mid"]), facing, "shoulders", tuple(person["head"]), "pose", tuple(point))
    origin = fixture.get("aim_origin_m")
    target = aim.Fixture(fixture_id, origin and tuple(origin), tuple(fixture["horizontal_world_reference"]),
                         dict(aim.DEFAULT_LIMITS))
    return aim.evaluate_fixture(target, reader, _KEEP if lit_for_them else _ADMIT)


def degrees_behind_side_on(behind_cos):
    """How far behind the person's side-on line a fixture sits, in degrees (negative: in front)."""
    return math.degrees(math.asin(max(-1.0, min(1.0, behind_cos))))


def centre_clear(origin, point, head):
    """Distance from the head to the beam's centre line between the pivot and the point."""
    return aim.segment_point_distance(tuple(origin), tuple(point), tuple(head))


def path_clear(origin, reference, start, end, distance_m, head, step_deg=1.0):
    """(ok, closest metres) for a move from joint angles `start` to `end`: the beam's centre line, out to
    distance_m, keeps HEAD_CLEARANCE_M from `head` at every sample along the straight joint-space path."""
    d_pan, d_tilt = joints.route(start, end)
    samples = max(1, int(math.ceil(max(abs(d_pan), abs(d_tilt)) / step_deg)))
    closest = float("inf")
    for k in range(samples + 1):
        s = k / samples
        ray = joints.direction(start[0] + s * d_pan, start[1] + s * d_tilt, reference)
        tip = tuple(origin[i] + ray[i] * distance_m for i in range(3))
        closest = min(closest, aim.segment_point_distance(tuple(origin), tip, tuple(head)))
    return closest >= HEAD_CLEARANCE_M, closest
