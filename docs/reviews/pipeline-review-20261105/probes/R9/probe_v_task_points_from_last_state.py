#!/usr/bin/env python3
"""Probe: score.py scores every need episode of a track at the task points of that track's LAST PersonState in the
whole log (score.py: people = {track_id: data} over the log, points_of(track) from it), not at the working region the
track had during the episode. When a track's working_region changes over an evening, earlier episodes are scored at
the wrong points.

    python3 -B probe_v_task_points_from_last_state.py <den-core-live checkout root>

Crafted log, the fake light model (desk-1 is lit by the front bulbs, armchair-1 by the rear ones), proposed slots
(task_points: desk-1 -> [desk-1], armchair-1 -> [armchair-1]), W=10 s, rise 0.2, change 0.1:
  base scene: fronts at 255, rears at 20 (2700 K); reading look: all four at 255.
  ep1 (t=100..200): A reads at armchair-1; the look comes on at t=105: armchair light rises ~100x -> anticipation 1.
  ep2 (t=300..400): A reads at desk-1; the look comes on at t=305: desk light rises ~10%     -> anticipation 0.
Hand value at each episode's own region: (1 + 0) / 2 = 0.5. PASS when the tool agrees; FAIL otherwise.
Synthetic data only; nothing written; no host contacted.
"""
import os
import sys


def main():
    root = os.path.abspath(sys.argv[1])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(root, "tools", "den-core"))
    import decide
    import events
    import light_model
    import runner
    import score

    T0 = 1793764800.0
    FL, FR, RL, RR = events.LIGHTS
    LOOK = {"on": True, "brightness": 255, "kelvin": 2700}
    BASE = {FL: LOOK, FR: LOOK, RL: {"on": True, "brightness": 20, "kelvin": 2700},
            RR: {"on": True, "brightness": 20, "kelvin": 2700}}
    policies = runner.load_policies(["need", "incumbent", "decide"])
    header = {"schema": events.LOG_SCHEMA, "sources": [], "policies": [runner.describe(p) for p in policies],
              "tick_s": 2.0, "tail_s": 150.0, "plant": False, "shown": "incumbent", "synthetic": False,
              "assume_occupied": False, "actuation": "replay"}
    log = []

    def add(t, kind, data, source):
        log.append({"schema": events.SCHEMA, "t": T0 + t, "seq": len(log), "kind": kind, "source": source,
                    "data": data})

    def ps(t, state, region):
        add(t, "person_state", {"track_id": "A", "state_id": "A@%.3f" % (T0 + t),
                                "working_region": {"surface_id": region},
                                "focused_engagement": {"state": state, "kind": "reading"},
                                "field_support": {"focused_engagement": {"support_status": "observed",
                                                                         "last_support_capture_ts": T0 + t}}},
            "need")

    def dec(t, rule, scenes):
        add(t, "decision", {"why": "%s: crafted" % rule, "requested_scene": {b: dict(s) for b, s in scenes.items()}},
            "decide")

    for b in events.LIGHTS:
        add(0, "light_observed", {"entity_id": b, **BASE[b], "context": "unknown"}, "recorder_lights")
    add(0, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")
    dec(0, "base", BASE)
    ps(100, "starting", "armchair-1"); ps(105, "engaged", "armchair-1"); dec(105, "reading_look", {b: LOOK for b in BASE})
    ps(200, "none", "armchair-1"); dec(200, "base", BASE)
    ps(300, "starting", "desk-1"); ps(305, "engaged", "desk-1"); dec(305, "reading_look", {b: LOOK for b in BASE})
    ps(400, "none", "desk-1"); dec(400, "base", BASE)
    add(450, "tick", {}, "runner")
    log.sort(key=lambda e: (e["t"], e["seq"]))
    for n, e in enumerate(log):
        e["seq"] = n

    config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
    model = light_model.LightModel(config["light_model"])
    W, RISE, CHANGE = 10.0, 0.2, 0.1

    # hand: each episode at its own region's points, by the model's own predict (the light model is not under test)
    def light(scenes, point):
        return model.predict(scenes, [point])[point]
    look_all = {b: LOOK for b in BASE}
    hand_eps = []
    for region in ("armchair-1", "desk-1"):
        before, after = light(BASE, region), light(look_all, region)
        hand_eps.append(after >= (1 + RISE) * before)
        print("RESULT hand %-10s before=%.4f after=%.4f rise=%.1f%% -> %s"
              % (region, before, after, 100 * (after / before - 1), hand_eps[-1]))
    hand = round(sum(hand_eps) / len(hand_eps), 4)

    # the tool, exactly as score.score() wires it
    people = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}
    arms, found = score.arms(header, log), score.episodes(log)
    rates = score.surrogates(arms["candidate"], found, lambda tr: decide.task_points(people[tr], config, model),
                             model, W, RISE, CHANGE)
    tool = rates["anticipation"]["rate"]
    print("RESULT tool episodes=%d anticipation=%s (points used for every episode: %s)"
          % (len(found), tool, decide.task_points(people["A"], config, model)))
    print("RESULT hand anticipation=%s" % hand)
    if tool == hand:
        print("PASS: each episode scored at its own working region")
        return 0
    print("FAIL: the tool scores every episode of A at A's last working region (%s), anticipation %s instead of %s"
          % (people["A"]["working_region"]["surface_id"], tool, hand))
    return 1


if __name__ == "__main__":
    sys.exit(main())
