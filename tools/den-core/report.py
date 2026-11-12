r"""Step 1's report: what the coordinator would do on your real sofa (coordinator plan, step 1).

    python -B tools\den-core\report.py            your real sofa, from the den simulation's newest fit
    python -B tools\den-core\report.py --demo     an invented room, for a dry run

It reads the den simulation's newest real-room fit, written when you last opened the simulation page:
<den-sim home>\private\fits\<fit>\light\light-model.json (your sockets and the book point above your sofa
mark) and transform.json (the stated facing). The den-sim home is DEN_SIM_HOME, else %USERPROFILE%\den-sim.
Only this process reads them; it prints labels, angles and modeled values, never a coordinate.

Nothing is sent anywhere. Standard library only.
"""
import argparse
import glob
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import body  # noqa: E402
import eye  # noqa: E402
import joints  # noqa: E402
import learn  # noqa: E402
import scurve  # noqa: E402
import share  # noqa: E402
import spot_light  # noqa: E402

SPOT_ABOVE_SEAT_M = 0.30
SHOULDER_ABOVE_SEAT_M, HEAD_ABOVE_SHOULDER_M = 0.5, 0.23
SHIFT_M = 0.20


def demo_room():
    """An invented den: four ceiling sockets, a sofa, the reader facing +y."""
    fixtures = {"F1": {"aim_origin_m": [0.5, 3.6, 2.5], "horizontal_world_reference": [0, -1, 0]},
                "F2": {"aim_origin_m": [1.8, 0.2, 2.5], "horizontal_world_reference": [0, 1, 0]},
                "F3": {"aim_origin_m": [1.6, 3.4, 2.5], "horizontal_world_reference": [0, -1, 0]},
                "F4": {"aim_origin_m": [3.5, 0.6, 2.5], "horizontal_world_reference": [-1, 0, 0]}}
    return {"fixtures": fixtures, "book": [1.2, 1.2, 0.75], "facing": [0.0, 1.0], "sockets": "invented (demo)"}


def real_room(home):
    fits = os.path.join(home, "private", "fits")
    models = sorted(glob.glob(os.path.join(fits, "*", "light", "light-model.json")), key=os.path.getmtime)
    for model_path in reversed(models):
        folder = os.path.dirname(os.path.dirname(model_path))
        transform_path = os.path.join(folder, "transform.json")
        if os.path.exists(transform_path):
            import json
            with open(model_path, encoding="utf-8") as f:
                model = json.load(f)
            with open(transform_path, encoding="utf-8") as f:
                transform = json.load(f)
            point = next(iter(model["aim_points"].values()))["point_m"]
            status = ((model.get("provenance") or {}).get("fixtures") or {}).get("status", "unknown")
            return {"fixtures": model["fixtures"], "book": point, "facing": transform["facing_unit"],
                    "sockets": status, "fit": os.path.basename(folder)}
    raise SystemExit("No real-room fit found under %s. Open the den simulation page once with your saved sockets "
                     "and sofa mark, then run this again." % fits)


def reader(book, facing):
    seat = [book[0], book[1], book[2] - SPOT_ABOVE_SEAT_M]
    shoulders = [seat[0], seat[1], seat[2] + SHOULDER_ABOVE_SEAT_M]
    return {"track_id": "you", "facing": tuple(facing), "shoulder_mid": tuple(shoulders),
            "head": (shoulders[0], shoulders[1], shoulders[2] + HEAD_ABOVE_SHOULDER_M), "target": tuple(book), "task": True}


def lines(room):
    out = ["Coordinator report: a reader at your sofa mark (sockets: %s)%s" % (
        room["sockets"], ", fit " + room["fit"] if room.get("fit") else "")]
    profile = scurve.FICTIONAL_PROFILE
    fixtures = {fid: dict(row, range=profile["range"], usable=True) for fid, row in sorted(room["fixtures"].items())}
    person = reader(room["book"], room["facing"])
    for fid, row in fixtures.items():
        c = eye.evaluate(fid, row, person, person["target"], lit_for_them=False)
        if c.ok:
            out.append("%s  clear    %4.1f deg behind side-on  above head %-3s  %.1f m  score %.2f  at full %.0f of 300" % (
                fid, eye.degrees_behind_side_on(c.behind_cos), "yes" if c.above_head_m > 0 else "no",
                math.dist(row["aim_origin_m"], person["target"]), c.score, share.brightness(row, person["target"])))
        else:
            extra = ""
            if c.reason == "in_front_of_reader" and c.behind_cos is not None:
                extra = ": %.1f deg past side-on (it must be beside or behind you)" % abs(eye.degrees_behind_side_on(c.behind_cos))
            out.append("%s  refused  %s%s" % (fid, c.reason.replace("_", " "), extra))
    jobs, _ = share.allocate(fixtures, [person], [], {})
    if not jobs:
        out.append("No fixture may light this reader: every one is refused.")
        return out
    fid, job = next(iter(jobs.items()))
    full = spot_light.spot_value(fixtures[fid]["aim_origin_m"], person["target"])
    level, value, short = spot_light.level_for(full, 300.0)
    out.append("CHOSEN %s: level %.2f, predicts %.0f of 300 (assumed 450 lm)%s" % (
        fid, level, value, ", short %.0f" % short if short else ""))
    for other in fixtures:
        if other != fid:
            out.append("%s rests: no art is labeled yet, so the other lights stay dark" % other)
            break
    left = (-room["facing"][1], room["facing"][0])
    moved = dict(person, target=tuple(person["target"][i] + (left[i] * SHIFT_M if i < 2 else 0) for i in range(3)),
                 head=tuple(person["head"][i] + (left[i] * SHIFT_M if i < 2 else 0) for i in range(3)))
    row = fixtures[fid]
    start = joints.room_to_joints(person["target"], row["aim_origin_m"], row["horizontal_world_reference"])
    end = joints.room_to_joints(moved["target"], row["aim_origin_m"], row["horizontal_world_reference"])
    delta = joints.route(start, end)
    ok, closest = eye.path_clear(row["aim_origin_m"], row["horizontal_world_reference"], start, end,
                                 math.dist(row["aim_origin_m"], moved["target"]), moved["head"])
    out.append("Shift 20 cm along the sofa: glide %.1f deg, %s, %.2f s on the fictional profile, beam centre clears your head by %.2f m" % (
        max(abs(d) for d in delta), "lit" if ok else "dark (the centre would cross your head)",
        scurve.move_time(delta, profile["axes"]), closest))
    shift = body.ShiftFilter()
    shift.update(0.0, person["target"])
    page = [shift.update(t / 5.0, tuple(person["target"][i] + (0.15 if i == 0 and 1.0 <= t / 5.0 < 1.6 else 0) for i in range(3)))
            for t in range(1, 20)]
    out.append("A page turn (15 cm out and back in 0.6 s): %s" % ("no move" if not any(page) else "MOVED"))
    learner = learn.Learner()
    record = {"seq": 1, "entity_id": "light.rear_left", "at": 0, "before": {"on": True, "brightness": 178, "kelvin": 2700},
              "after": {"on": True, "brightness": 102, "kelvin": 2700}}
    learner.apply(record, "sitting/evening", people=1, on=True, occupied=True)
    learned = learner.scene("sitting/evening", "light.rear_left")["brightness"]
    out.append("Learning: sitting, evening, a lamp at %d%% -> you set %d%% -> learned %d%%" % (
        round(178 / 2.54), round(102 / 2.54), round(learned / 2.54)))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--demo", action="store_true", help="use an invented room instead of your real sofa")
    args = parser.parse_args(argv)
    home = os.environ.get("DEN_SIM_HOME") or os.path.join(os.path.expanduser("~"), "den-sim")
    room = demo_room() if args.demo else real_room(home)
    print("\n".join(lines(room)))


if __name__ == "__main__":
    main()
