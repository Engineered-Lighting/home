"""Rung 9's deterministic hold fixtures, V6-V8 and V62-V64, built under each compensation option.

    python -B tools/den-core/holds.py

prints each case's decisions under none-v0 (v0, no solve) and the register's P0-P3 (decisions.json
'compensation'). Their expected results depend on that pending owner decision, so none of them is admitted as pass
or fail: every outcome carries admitted false. Only what holds under every option is tested as pass or fail
(test_rung9.py): held bulbs pinned at their read-back and skipped, no decision in a game, hard_conflicts empty,
every decision under 100 ms, the same bytes every run.

The ladder names the cases in one list; this file reads them in order as
- V6: a dim held; V7: a brighten held; V8: a hold between solve and send;
- V62: a shared bulb dimmed while another person still needs light; V63: gaming entry and exit under a hold;
  V64: overlapping holds.
People are A (at desk-1) and B (at armchair-1), the fake light model's two task points (light_model.fake). A
hold here may carry changer_track, which den_lighting's holds never do, so P3 can be seen with and without it.
Events go to decide alone, as the runner would hand them; nothing reads a clock, file or network.
"""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import decide  # noqa: E402

T0 = 1793764800.0
FL, FR, RL, RR = decide.LIGHTS
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
NORMAL = {b: LIT for b in decide.LIGHTS}
PENDING = "compensation (decisions.json) is pending the owner's decision: not admitted as pass or fail"
SOURCES = {"person_state": "need", "mode": "incumbent", "tick": "runner"}


def ev(t, kind, data):
    return {"schema": "den-event-1", "t": float(T0 + t), "seq": 0, "kind": kind,
            "source": SOURCES.get(kind, "synthetic"), "data": data}


def person(t, track, surface, state="none"):
    """A PersonState for A or B with a working region on a named surface."""
    active = state != "none"
    return ev(t, "person_state", {
        "schema_version": "person_state/v1", "state_id": "%s@%.3f" % (track, T0 + t), "emitted_ts": T0 + t,
        "track_id": track, "identity": "unbound", "working_region": {"surface_id": surface},
        "focused_engagement": {"state": state, "since_s": 0.0 if active else None,
                               "onset_ts": T0 + t if active else None, "confidence": None, "kind": "reading"},
        "visual_demand": {"level": None, "basis": None, "confidence": None}, "posture": "sitting",
        "field_support": {"focused_engagement": {"last_support_capture_ts": T0 + t - 1.0, "value_set_ts": T0 + t,
                                                 "support_status": "observed"}},
        "sources": {"focused_engagement": []}, "interpreter": {"path": "rules", "version": "need-v0"}, "flags": []})


def hold(bulb, brightness, since, changer=None, lifetime="room_vacant_2_minutes"):
    h = {"entity_id": bulb, "pinned": {"on": True, "brightness": brightness, "kelvin": 2700}, "since": T0 + since,
         "lifetime": lifetime}
    if changer:
        h["changer_track"] = changer
    return h


def mode(t, revision, holds=(), gaming=False):
    return ev(t, "mode", {"policy": "den_lighting@1.0.4", "revision": revision, "gaming": gaming, "occupancy": "on",
                          "holds": sorted(holds, key=lambda h: h["entity_id"]), "normal": NORMAL})


def seen(t, bulb, scene=LIT):
    return ev(t, "light_observed", {"entity_id": bulb, **scene, "context": "unknown"})


def start(*people):
    """The bulbs at den_lighting's normal scene, its mode, occupancy on, then the people present."""
    out = [seen(0, b) for b in decide.LIGHTS] + [mode(0, 1), ev(0, "occupancy", {"value": "on", "via": "synthetic"})]
    return out + [person(1, track, surface) for track, surface in people]


def ticks(a, b):
    return [ev(t, "tick", {}) for t in range(a, b, 2)]


A, B = ("A", "desk-1"), ("B", "armchair-1")
CASES = {
    "V6": ("a dim held: A dims the front left bulb at the desk",
           start(A) + [mode(10, 2, [hold(FL, 60, 10, "A")])] + ticks(12, 20)),
    "V7": ("a brighten held: A turns the front right bulb up",
           start(A) + [mode(10, 2, [hold(FR, 255, 10, "A")])] + ticks(12, 20)),
    "V8": ("a hold between solve and send: A starts reading, and 0.2 s after the plan, before a 2 s fade could "
           "finish, A dims the rear right bulb",
           start(A) + [person(10, "A", "desk-1", "engaged"), mode(10.2, 2, [hold(RR, 120, 10.2, "A")])]
           + ticks(12, 20)),
    "V62": ("a shared bulb dimmed while another person still needs light: A dims the rear left bulb, which lights "
            "both the desk and B's armchair",
            start(A, B) + [mode(10, 2, [hold(RL, 40, 10, "A")])] + ticks(12, 20)),
    "V63": ("gaming entry and exit under a hold: A dims the front left bulb, a game starts, and its end clears "
            "every hold as den_lighting does",
            start(A) + [mode(10, 2, [hold(FL, 60, 10, "A")])] + ticks(12, 20)
            + [mode(20, 3, [hold(FL, 60, 10, "A", "game_ends")], gaming=True)] + ticks(22, 40)
            + [mode(40, 4)] + ticks(42, 50)),
    "V64": ("overlapping holds: A dims the front left bulb, then B dims the rear right one while the first is held, "
            "then the first ends",
            start(A, B) + [mode(10, 2, [hold(FL, 60, 10, "A")])] + ticks(12, 20)
            + [mode(20, 3, [hold(FL, 60, 10, "A"), hold(RR, 90, 20, "B")])] + ticks(22, 30)
            + [mode(30, 4, [hold(RR, 90, 20, "B")])] + ticks(32, 40)),
}
OPTIONS = decide.OPTIONS


def events_of(case):
    return sorted(copy.deepcopy(CASES[case][1]), key=lambda e: (e["t"], e["kind"] == "tick"))


def run_case(case, option, config=None):
    """(events with seq, decide's decisions as den-event-1 dicts) for one case under one compensation option."""
    policy = decide.make_policy({**(config or {}), "compensation": {"option": option}})
    out, seq, fed = [], 0, []
    for event in events_of(case):
        event = dict(event, seq=seq)
        fed.append(event)
        seq += 1
        for o in policy.handle(event):
            out.append({"schema": "den-event-1", "t": event["t"], "seq": seq, "kind": o["kind"], "source": policy.id,
                        "data": o["data"]})
            seq += 1
    return fed, out


def short(bulb):
    return bulb.replace("light.", "").replace("den_", "")


def outcome(decisions):
    rows = []
    for e in decisions:
        d = e["data"]
        rows.append({
            "t": round(e["t"] - T0, 3), "trigger": d["trigger"], "chosen": d["why"].split("chose ", 1)[1].split(" ")[0],
            "scene": {short(b): (s["brightness"] if s["on"] else "off") for b, s in d["requested_scene"].items()},
            "held": {short(h["entity_id"]): {k: h.get(k) for k in ("direction", "footprint", "changer_track")
                                             if k in h} for h in d["inputs"]["hold_set"]["held"]},
            "hold_limited": d.get("hold_limited"),
        })
    return rows


def report():
    """{case: {title, admitted: False, pending, options: {option: outcome}}}."""
    return {case: {"title": title, "admitted": False, "pending": PENDING,
                   "options": {option: outcome(run_case(case, option)[1]) for option in OPTIONS}}
            for case, (title, _) in CASES.items()}


def main():
    for case, entry in report().items():
        print("%s: %s" % (case, entry["title"]))
        print("  not admitted: %s" % entry["pending"])
        for option, rows in entry["options"].items():
            print("  %s:" % option)
            for row in rows:
                limited = row["hold_limited"]
                print("    %+7.1f s %-20s %-26s %s%s" % (
                    row["t"], row["trigger"], row["chosen"], json.dumps(row["scene"], sort_keys=True),
                    "  hold_limited: %s" % ", ".join("%s at %s" % (x["track_id"], x["point_id"]) for x in limited)
                    if limited else ""))


if __name__ == "__main__":
    main()
