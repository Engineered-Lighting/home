"""Sharing the spotlights: one job per fixture, people first, then art (coordinator plan C19).

Owner (2026-11-12): "there'll be up to 4 spotlights in my room, and not all of them should be aiming at me.
And I want some of them to be like aiming at my artwork ... the coordinator needs to be intelligent about this."

Jobs, highest first: task (a person doing a close-up task), pre_aim (a person not doing one yet: their best
fixture points at them dark), accent (a labeled piece of art), and rest (no job). Each person gets at most one
fixture, each piece of art at most one. A fixture keeps its job while it's still valid and no higher job needs
it. People are ranked by aim.py's over-the-shoulder score with eye.py's margin, and when two lights are about
equally over the shoulder (within 5 degrees), the brighter one wins (pick_for_person); art by how close the beam
comes in to 30 degrees from vertical (the usual gallery angle, which keeps glare off glass and varnish),
nearer being better. An accent beam's centre never runs through a seated person's head.

Standard library only. Pure.
"""
import math

import eye
import joints
import spot_light

ART_FROM_VERTICAL_DEG = 30.0
NEAR_TIE_DEG = 5.0  # lights this close on "how far behind you" count as equally over the shoulder
ART_MIN_FACING = 0.1  # the fixture must sit in front of the piece's face


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 1e-12 else None


def _joints_ok(fixture, target):
    try:
        pan, tilt = joints.room_to_joints(target, fixture["aim_origin_m"], fixture["horizontal_world_reference"])
    except ValueError:
        return None
    return (pan, tilt) if joints.in_range(pan, tilt, fixture["range"]) else None


def _pick(options, mine):
    """Keep the current fixture when it's still an option, else the best score, the lower id on a tie."""
    if mine in options:
        return mine
    best = max(score for score in options.values())
    return min(fid for fid, score in options.items() if score == best)


def brightness(fixture, point):
    """Modeled light at `point` from this fixture at full, on its profile's lumens and beam (assumed until measured)."""
    profile = fixture.get("profile") or {}
    return spot_light.spot_value(fixture["aim_origin_m"], point,
                                 lumens=profile.get("lumens", spot_light.ASSUMED_SPOT_LUMENS),
                                 fwhm_deg=profile.get("beam_fwhm_deg", 20.0))


def pick_for_person(options, fixtures, target, mine):
    """The owner's rule (2026-11-12): never in front of you; the most over your shoulder wins, except that lights
    within NEAR_TIE_DEG of each other on "how far behind you", and alike on above-the-head, count as equally over
    the shoulder, and then the brighter one wins. A light already lit for you keeps its job (no flicker).
    options: {fixture_id: aim.Candidate}."""
    if mine in options:
        return mine
    best = options[min(options, key=lambda fid: (-options[fid].score, fid))]
    best_deg = eye.degrees_behind_side_on(best.behind_cos)
    near = [fid for fid, c in options.items()
            if (c.above_head_m > 0) == (best.above_head_m > 0)
            and best_deg - eye.degrees_behind_side_on(c.behind_cos) <= NEAR_TIE_DEG]
    return max(sorted(near), key=lambda fid: (round(brightness(fixtures[fid], target), 6), options[fid].score))


def allocate(fixtures, people, art, current, pre_aim=True, seated_heads=()):
    """Assign jobs.

    fixtures: {fixture_id: {aim_origin_m, horizontal_world_reference, range, usable}}.
    people: [{track_id, head, shoulder_mid, facing, target, task}], target being the task point when seated,
      else the torso.
    art: [{label_id, point, front}], already in priority order.
    current: {fixture_id: {"kind", "who"}}, the jobs as they stand.
    Returns (jobs {fixture_id: {kind, who, target, score, joints}}, notes [{who, fixture, reason}])."""
    free = sorted(f for f, x in fixtures.items() if x.get("usable"))
    holder = {job["who"]: fid for fid, job in current.items()}
    jobs, notes = {}, []

    order = [p for p in people if p.get("task")] + ([p for p in people if not p.get("task")] if pre_aim else [])
    for person in order:
        if not person.get("target"):
            notes.append({"who": person["track_id"], "fixture": None, "reason": "no_target"})
            continue
        mine, options = holder.get(person["track_id"]), {}
        for fid in (f for f in free if f not in jobs):
            fixture = fixtures[fid]
            candidate = eye.evaluate(fid, fixture, person, person["target"], lit_for_them=(fid == mine))
            angles = candidate.ok and _joints_ok(fixture, person["target"])
            if not candidate.ok or not angles:
                notes.append({"who": person["track_id"], "fixture": fid,
                              "reason": candidate.reason if not candidate.ok else "out_of_range",
                              "behind_cos": candidate.behind_cos})
                continue
            options[fid] = (candidate.score, angles, candidate)
        if not options:
            notes.append({"who": person["track_id"], "fixture": None, "reason": "no_fixture"})
            continue
        fid = pick_for_person({f: o[2] for f, o in options.items()}, fixtures, person["target"], mine)
        jobs[fid] = {"kind": "task" if person.get("task") else "pre_aim", "who": person["track_id"],
                     "target": tuple(person["target"]), "score": round(options[fid][0], 4), "joints": options[fid][1]}

    for piece in art:
        mine, options = holder.get(piece["label_id"]), {}
        for fid in (f for f in free if f not in jobs):
            fixture, point = fixtures[fid], piece["point"]
            origin = fixture["aim_origin_m"]
            back = _unit([origin[i] - point[i] for i in range(3)])
            reason = None
            if back is None or sum(back[i] * piece["front"][i] for i in range(3)) < ART_MIN_FACING:
                reason = "behind_the_piece"
            elif any(eye.centre_clear(origin, point, h) < eye.HEAD_CLEARANCE_M for h in seated_heads):
                reason = "through_a_seated_head"
            angles = None if reason else _joints_ok(fixture, point)
            if reason or not angles:
                notes.append({"who": piece["label_id"], "fixture": fid, "reason": reason or "out_of_range"})
                continue
            from_vertical = math.degrees(math.acos(max(-1.0, min(1.0, back[2]))))
            distance = math.dist(origin, point)
            options[fid] = (-abs(from_vertical - ART_FROM_VERTICAL_DEG) / ART_FROM_VERTICAL_DEG - 0.02 * distance, angles)
        if not options:
            notes.append({"who": piece["label_id"], "fixture": None, "reason": "no_free_fixture"})
            continue
        fid = _pick({f: o[0] for f, o in options.items()}, mine)
        jobs[fid] = {"kind": "accent", "who": piece["label_id"], "target": tuple(piece["point"]),
                     "score": round(options[fid][0], 4), "joints": options[fid][1]}
    return jobs, notes


def art_from_labels(doc, learned_levels=None):
    """Confirmed `picture` labels as art, in priority order: brighter learned levels first, then larger.
    Aim at the centre of the box's front face; front_unit = (-sin yaw, cos yaw, 0)."""
    learned_levels = learned_levels or {}
    pieces = []
    for label in (doc or {}).get("labels", []):
        if label.get("kind") != "picture" or label.get("status") != "confirmed":
            continue
        box = label["box"]
        yaw = math.radians(box.get("yaw_deg", 0.0))
        front = (-math.sin(yaw), math.cos(yaw), 0.0)
        half_depth = box["size_m"][1] / 2.0
        point = tuple(box["centre_m"][i] + front[i] * half_depth for i in range(3))
        area = box["size_m"][0] * box["size_m"][2]
        pieces.append({"label_id": label["id"], "name": label.get("name"), "point": point, "front": front,
                       "rank": (learned_levels.get(label["id"], 1.0), area)})
    return sorted(pieces, key=lambda p: (-p["rank"][0], -p["rank"][1], p["label_id"]))
