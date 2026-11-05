#!/usr/bin/env python3
"""verify_SEAM-02: does den-observe refuse a RoomModel JSON Lines file with more than one revision?

    TMPDIR=<scratch dir> python3 -B verify_SEAM-02.py <rung-2-observe checkout> [<rung-1-register checkout>]

The second checkout defaults to a sibling folder named rung-1-register.
Synthetic data only, temp dirs only, no network, python -B in every child.

Steps
 1. den-register registers its synthetic scene twice (the second time after
    moving one click, as its own version-bump test does): room-model.jsonl
    then holds two RoomModel revisions, one per line.
 2. den-observe roommodel.load() on that file.
 3. Isolation from SEAM-01 (camera entry form): a JSON Lines file of TWO
    RoomModel revisions that are both in den-observe's OWN form
    (synth.room_model(), second revision with a newer camera version).
    roommodel.load(), and the CLI replay.py --fake on den-observe's own take.
 4. Control: the same file with only its last line: load + Camera build.
Prints PASS/FAIL/RESULT lines. "FAIL" here means the defect reproduced.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


def child(code, *args):
    proc = subprocess.run([sys.executable, "-B", "-c", code, *map(str, args)], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


REGISTER = r'''
import json, sys, types
sys.dont_write_bytecode = True
reg, tmp = sys.argv[1], sys.argv[2]
sys.path.insert(0, reg)
import test_den_register as TR
s = TR.Scene(types.SimpleNamespace(tmp=types.SimpleNamespace(name=tmp)))
s.click_all()
fits = [s.fit()]
s.click_all(["p1"], shift={"p1": (0.4, 0)})
fits.append(s.fit(now=1_800_000_900.0))
print(json.dumps({"fits": [[c, r.get("transform_id")] for c, r in fits], "room_model": str(s.work / "room-model.jsonl")}))
'''

OBSERVE = r'''
import copy, json, sys
sys.dont_write_bytecode = True
obs, mode, path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, obs)
sys.path.insert(0, obs.rsplit("/", 1)[0] + "/den-take")
import method, roommodel, synth
if mode == "load":
    try:
        model = roommodel.load(path)
    except roommodel.RegistrationRefused as exc:
        print(json.dumps({"result": "REFUSED " + str(exc)})); sys.exit()
    tid = roommodel.current_transform_id(model, "den-oak")
    try:
        roommodel.Camera(model, tid, max_error_bound_m=method.load().get("max_error_bound_m"))
        print(json.dumps({"result": "LOADED, Camera ACCEPTED " + tid}))
    except roommodel.RegistrationRefused as exc:
        print(json.dumps({"result": "LOADED, Camera REFUSED " + str(exc)}))
elif mode == "own-jsonl":
    one = synth.room_model()
    two = synth.room_model([synth.camera_entry(version="2026-11-13")])
    two["version"] = "den-synth-2"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in (one, two):
            f.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
    with open(path + ".last.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(two, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(json.dumps({"ok": True}))
elif mode == "take":
    scene = synth.Scene()
    frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
    folder = synth.make_take(path, frames, scene.depth_mm())
    synth.write_check(folder)
    print(json.dumps({"take": str(folder)}))
'''


def last(out, err):
    return json.loads(out.strip().splitlines()[-1]) if out.strip() else {"result": "ERROR " + err[-800:]}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    obs_root = Path(sys.argv[1]).resolve()
    reg_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else obs_root.parent / "rung-1-register"
    obs, reg = obs_root / "tools" / "den-observe", reg_root / "tools" / "den-register"
    tmp = Path(tempfile.mkdtemp(prefix="verify-seam02-"))
    print(f"RESULT temp folder {tmp}")

    code, out, err = child(REGISTER, reg, tmp / "reg")
    if code != 0:
        print("FAIL setup: den-register run failed\n" + err[-2000:])
        return 2
    info = json.loads(out.strip().splitlines()[-1])
    rm = Path(info["room_model"])
    lines = rm.read_text(encoding="utf-8").splitlines()
    print(f"RESULT den-register fits {info['fits']}; room-model.jsonl lines: {len(lines)}; "
          f"revisions {[json.loads(l)['version'] for l in lines]}")

    r2 = last(*child(OBSERVE, obs, "load", rm)[1:])["result"]
    print(("FAIL" if "no readable RoomModel" in r2 else "INFO") + f" step2 den-observe load(den-register 2-line file): {r2}")

    own = tmp / "own-form-2rev.jsonl"
    child(OBSERVE, obs, "own-jsonl", own)
    r3 = last(*child(OBSERVE, obs, "load", own)[1:])["result"]
    print(("FAIL" if "no readable RoomModel" in r3 else "PASS") + f" step3 den-observe load(2 revisions in its OWN "
          f"camera form, JSON Lines): {r3}")
    take = last(*child(OBSERVE, obs, "take", tmp / "obs-take")[1:])["take"]
    proc = subprocess.run([sys.executable, "-B", obs / "replay.py", "--take", take, "--room-model", own, "--fake",
                           "--out", tmp / "observe-out"], capture_output=True, text=True)
    msg = (proc.stderr or proc.stdout).strip().splitlines()
    print(("FAIL" if proc.returncode == 2 else "PASS") + f" step3 CLI replay.py --fake --room-model <own-form "
          f"2-line JSONL>: exit {proc.returncode}: {msg[-1] if msg else ''}")

    r4 = last(*child(OBSERVE, obs, "load", str(own) + ".last.jsonl")[1:])["result"]
    print(("PASS" if r4.startswith("LOADED, Camera ACCEPTED") else "INFO") + f" step4 control, last line only: {r4}")

    reproduced = "no readable RoomModel" in r3 and proc.returncode == 2
    print("RESULT SEAM-02 " + ("REPRODUCED: a multi-revision RoomModel JSON Lines file is 'no readable RoomModel' "
                               "even in den-observe's own camera form" if reproduced else "NOT reproduced"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
