#!/usr/bin/env python3
"""verify_SEAM-09: den-observe's non-canon 'freshness' field: is expiry or depth use lost to a schema-built reader?

    TMPDIR=<scratch dir> python3 -B verify_SEAM-09.py <rung-2-observe checkout> [<rung-3-track checkout>]

The second checkout defaults to a sibling folder named rung-3-track.
Synthetic data only (den-observe's synth take, synth RoomModel, fake
detector), temp dirs only, no network, python -B in every child.

Steps
 1. A six-frame synthetic take; frame 3 carries depth 0.2 s too old. Rows
    1-3 complete 0.3 s after capture (fresh), rows 4-6 2.0 s after (expired
    at the proposed detector_max_age_s 1.0). replay.py, unscored.
 2. Keys of each Observation: is 'freshness' there (the non-canon field)?
 3. Expiry: the validity of the expired rows' Observations (canon field).
 4. Depth: for the too-old-depth frame, the canon depth_ts gap and joints_room
    lift values (canon fields).
 5. A schema-built reader, den-track's records.read_observations (it never
    reads 'freshness'): which Observations does it use?
Prints PASS/FAIL/RESULT lines.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SETUP = r'''
import json, sys
sys.dont_write_bytecode = True
obs, work = sys.argv[1], sys.argv[2]
sys.path.insert(0, obs)
sys.path.insert(0, obs.rsplit("/", 1)[0] + "/den-take")
from pathlib import Path
import synth, fake_detector, detections
work = Path(work)
scene = synth.Scene()
frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - (0.2 if i == 2 else 0.004)) for i in range(6)]
folder = synth.make_take(work / "take", frames, scene.depth_mm())
synth.write_check(folder)
(work / "room-model.json").write_text(json.dumps(synth.room_model(), ensure_ascii=False), encoding="utf-8")
pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, latency_s=0.3 if i < 3 else 2.0)
        for i, (t, _) in enumerate(frames)]
detections.write_rows(work / "rows.jsonl", rows)
print(json.dumps({"take": str(folder)}))
'''

TRACK = r'''
import json, sys
sys.dont_write_bytecode = True
track, path = sys.argv[1], sys.argv[2]
sys.path.insert(0, track)
import method, records
m = method.Method()
frames, counts = records.read_observations(path, m.values["input"])
used = sorted(i for _, _, ms in frames for meas in ms for i in meas["ids"])
print(json.dumps({"counts": counts, "used": used}))
'''

SCHEMA_FIELDS = {"obs_id", "source", "capture_ts", "kind", "image_box", "keypoints", "joint_set", "joints_room",
                 "depth_ts", "label", "room_position", "transform_id", "score", "visibility", "validity"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    obs_root = Path(sys.argv[1]).resolve()
    track_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else obs_root.parent / "rung-3-track"
    obs, track = obs_root / "tools" / "den-observe", track_root / "tools" / "den-track"
    work = Path(tempfile.mkdtemp(prefix="verify-seam09-"))
    print(f"RESULT temp folder {work}")
    proc = subprocess.run([sys.executable, "-B", "-c", SETUP, str(obs), str(work)], capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL setup\n" + proc.stderr[-2000:])
        return 2
    take = json.loads(proc.stdout.strip().splitlines()[-1])["take"]
    proc = subprocess.run([sys.executable, "-B", str(obs / "replay.py"), "--take", take, "--room-model",
                           str(work / "room-model.json"), "--detections", str(work / "rows.jsonl"), "--out",
                           str(work / "observe")], capture_output=True, text=True)
    print(f"RESULT replay exit {proc.returncode}")
    path = work / "observe" / "observations.jsonl"
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    extra = sorted(set().union(*(set(r) for r in recs)) - SCHEMA_FIELDS)
    print(("INFO" if extra else "PASS") + f" step2 non-canon top-level keys: {extra}")
    lost = 0
    for r in recs:
        f = r["freshness"]
        gap = round(r["capture_ts"] - r["depth_ts"], 3)
        lifts = sorted({j["lift"].split(":")[0] for j in r.get("joints_room") or []})
        print(f"RESULT step3/4 {r['obs_id']}: freshness.status {f['status']}, freshness.depth {f['depth']}, "
              f"validity {r['validity']}, canon depth gap {gap} s, joints_room lifts {lifts}")
        if f["status"] == "expired" and r["validity"] == "valid":
            lost += 1
        if f["depth"] != "used" and "stereo" in lifts:
            lost += 1
    print(("FAIL" if lost else "PASS") + f" step3/4 records whose expiry or depth use shows ONLY in freshness: {lost}")
    proc = subprocess.run([sys.executable, "-B", "-c", TRACK, str(track), str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        print("INFO step5 den-track reader failed: " + proc.stderr[-800:])
    else:
        t = json.loads(proc.stdout.strip().splitlines()[-1])
        expired = sorted(r["obs_id"] for r in recs if r["freshness"]["status"] == "expired")
        leaked = sorted(set(expired) & set(t["used"]))
        print(("FAIL" if leaked else "PASS") + f" step5 den-track (never reads freshness) counts {t['counts']}; "
              f"expired observations it used: {leaked}")
    print("RESULT SEAM-09: 'freshness' is an extra top-level key (den-observe slot freshness_field, open); "
          + ("expiry/depth use are also carried by canon fields" if not lost else "some state is only in freshness"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
