#!/usr/bin/env python3
"""Probe: recompute rung 9's anticipation and stability replay surrogates by hand, on a tiny crafted log, with an
independent implementation of the gate's definitions (rung9-gate-v0.json), and compare with score.py's
arms()/episodes()/surrogates() on the same log.

    python3 -B probe_v_surrogates_hand_recompute.py <den-core-live checkout root>

The crafted log (one track, 'room'; the fake light model; proposed parameters W=10 s, rise=0.2, change=0.1):
  t=0     the four bulbs observed on at brightness 100, 2700 K; occupancy on; decide requests base (100/2700)
  ep1     starting t=100, engaged t=105, decide turns the reading look on (255/2700) at t=105 (in the window),
          off at t=130 and on again at t=132 (a flicker after the window), none at t=200 (look off at t=200)
  ep2     starting t=300, engaged t=305, the look comes on late at t=312 (outside the 10 s window), none at t=360
  ep3     starting t=400, back to none at t=403 without engaged: not an episode
  ep4     starting t=500 while decide already has 255/2700 on every bulb as its base, engaged t=503, the look
          comes on at t=503 but the light does not rise; none at t=560
Hand values: anticipation = (1 + 0 + 0) / 3 = 0.3333; stability = (0 + 0 + 1) / 3 = 0.3333 (ep2's late turn-on is
a need-driven change after the window, so it also breaks stability, by the gate's text).
It also checks the incumbent's rates are 0 and 1 by construction.
Synthetic data only; nothing is written; no host is contacted. Prints PASS/FAIL per statistic.
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
    BASE = {"on": True, "brightness": 100, "kelvin": 2700}
    LOOK = {"on": True, "brightness": 255, "kelvin": 2700}
    policies = runner.load_policies(["need", "incumbent", "decide"])
    header = {"schema": events.LOG_SCHEMA, "sources": [], "policies": [runner.describe(p) for p in policies],
              "tick_s": 2.0, "tail_s": 150.0, "plant": False, "shown": "incumbent", "synthetic": False,
              "assume_occupied": False, "actuation": "replay"}
    log = []

    def add(t, kind, data, source):
        log.append({"schema": events.SCHEMA, "t": T0 + t, "seq": len(log), "kind": kind, "source": source,
                    "data": data})

    def ps(t, state):
        add(t, "person_state", {"track_id": "room", "state_id": "room@%.3f" % (T0 + t),
                                "focused_engagement": {"state": state, "kind": "reading"},
                                "field_support": {"focused_engagement": {"support_status": "observed",
                                                                         "last_support_capture_ts": T0 + t}}},
            "need")

    def dec(t, rule, scene):
        add(t, "decision", {"why": "%s: crafted" % rule, "requested_scene": {b: dict(scene) for b in events.LIGHTS}},
            "decide")

    for b in events.LIGHTS:
        add(0, "light_observed", {"entity_id": b, **BASE, "context": "unknown"}, "recorder_lights")
    add(0, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")
    dec(0, "base", BASE)
    # ep1
    ps(100, "starting"); ps(105, "engaged"); dec(105, "reading_look", LOOK)
    dec(130, "base", BASE); dec(132, "reading_look", LOOK)
    ps(200, "none"); dec(200, "base", BASE)
    # ep2
    ps(300, "starting"); ps(305, "engaged"); dec(312, "reading_look", LOOK)
    ps(360, "none"); dec(360, "base", BASE)
    # ep3 (not an episode)
    ps(400, "starting"); ps(403, "none")
    # ep4: base already at the look's scene
    dec(450, "base", LOOK)
    ps(500, "starting"); ps(503, "engaged"); dec(503, "reading_look", LOOK)
    ps(560, "none"); dec(560, "base", LOOK)
    add(600, "tick", {}, "runner")
    log.sort(key=lambda e: (e["t"], e["seq"]))
    for n, e in enumerate(log):
        e["seq"] = n

    config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
    model = light_model.LightModel(config["light_model"])
    W, RISE, CHANGE = 10.0, 0.2, 0.1

    # ---- independent implementation of the gate's text --------------------------------------------------------
    raw = config["light_model"]

    def g(u):
        lv, gv = raw["dimming"][FL]["levels"], raw["dimming"][FL]["g"]
        u = min(max(u, 0), 254)
        for i in range(1, len(lv)):
            if u <= lv[i]:
                return gv[i - 1] + (gv[i] - gv[i - 1]) * (u - lv[i - 1]) / (lv[i] - lv[i - 1])
        return 1.0

    def basis(b, k, p):
        rows = sorted((1e6 / int(kk), v[p]) for kk, v in raw["basis"][b].items())
        m = 1e6 / k
        if m <= rows[0][0]:
            return rows[0][1]
        for (m0, v0), (m1, v1) in zip(rows, rows[1:]):
            if m <= m1:
                return v0 + (v1 - v0) * (m - m0) / (m1 - m0)
        return rows[-1][1]

    def desk(scene):  # task points of track 'room' under the proposed slot: desk-1
        total = 0.0
        for b in events.LIGHTS:
            s = scene[b]
            if s["on"]:
                total += g(max(1, min(s["brightness"], 254))) * basis(b, s["kelvin"], "desk-1")
        return total

    # candidate timeline: decide's latest request (all four bulbs every time here), need-driven = reading toggles
    timeline, last_rule = [], None
    for e in log:
        if e["kind"] == "decision" and e["source"] == "decide":
            rule = e["data"]["why"].split(":")[0]
            need = (rule == "reading_look") != (last_rule == "reading_look")
            last_rule = rule
            light = desk(e["data"]["requested_scene"])
            if not timeline or abs(timeline[-1][1] - light) > 0 or True:
                timeline.append((e["t"], light, need))
    eps, open_ = [], {}
    for e in log:
        if e["kind"] == "person_state":
            st = e["data"]["focused_engagement"]["state"]
            if st != "none" and "room" not in open_:
                open_["room"] = [e["t"], False]
            if "room" in open_ and st == "engaged":
                open_["room"][1] = True
            if st == "none" and "room" in open_:
                t0, ok = open_.pop("room")
                if ok:
                    eps.append((t0, e["t"]))
    ant, stab = [], []
    for t0, t1 in eps:
        before = [x for x in timeline if x[0] < t0][-1][1]
        ant.append(any(n and t0 <= t <= t0 + W and v >= (1 + RISE) * before for t, v, n in timeline))
        ok, prev = True, None
        for t, v, n in timeline:
            if t > t1:
                break
            if n and t0 + W <= t < t1 and prev is not None and abs(v - prev) > CHANGE * prev:
                ok = False
            prev = v
        stab.append(ok)
    hand = {"anticipation": round(sum(ant) / len(ant), 4), "stability": round(sum(stab) / len(stab), 4)}
    print("RESULT hand episodes=%d anticipation=%s stability=%s per-episode ant=%s stab=%s"
          % (len(eps), hand["anticipation"], hand["stability"], ant, stab))

    # ---- the tool --------------------------------------------------------------------------------------------
    arms = score.arms(header, log)
    found = score.episodes(log)
    people = {e["data"]["track_id"]: e["data"] for e in log if e["kind"] == "person_state"}
    rates = {name: score.surrogates(arms[name], found, lambda tr: decide.task_points(people[tr], config, model),
                                    model, W, RISE, CHANGE) for name in arms}
    tool = {k: rates["candidate"][k]["rate"] for k in ("anticipation", "stability")}
    print("RESULT tool episodes=%d candidate=%s incumbent=%s" % (len(found), tool,
          {k: rates["incumbent"][k]["rate"] for k in ("anticipation", "stability")}))
    bad = 0
    for k in ("anticipation", "stability"):
        same = tool[k] == hand[k]
        bad += not same
        print("%s %s: tool %s hand %s" % ("PASS" if same else "FAIL", k, tool[k], hand[k]))
    inc = (rates["incumbent"]["anticipation"]["rate"], rates["incumbent"]["stability"]["rate"])
    print("%s incumbent rates %s (0 and 1 by construction)" % ("PASS" if inc == (0.0, 1.0) else "FAIL", inc))
    return 1 if bad or inc != (0.0, 1.0) else 0


if __name__ == "__main__":
    sys.exit(main())
