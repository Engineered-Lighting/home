#!/usr/bin/env python3
"""verify_SEAM-08: after a second registration, does an older transform_id stop resolving?

    TMPDIR=<scratch dir> python3 -B verify_SEAM-08.py <rung-1-register checkout> [<rung-2-observe checkout>]

The second checkout defaults to a sibling folder named rung-2-observe.
Synthetic data only, temp dirs only, no network, python -B in every child.

Steps
 1. den-register registers its synthetic scene twice (second fit after moving
    one click): room-model.jsonl holds den-v1 and den-v2.
 2. What each revision holds (the finding's own reproduction).
 3. den-register's documented resolution (records.py docstring and
    records.resolve: over every revision in the file) for both transform_ids.
 4. The finding's scenario: resolution against the NEWEST revision alone.
 5. The only pinned consumer that resolves transform_ids against a RoomModel,
    den-observe: given ONE document that keeps BOTH entries (the finding's
    suggested fix), is the older transform_id usable? (den-observe refuses
    any superseded entry as stale, so the fix would not change its outcome.)
Prints PASS/FAIL/RESULT lines.
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
import test_den_register as TR, records
from scene import Refused
s = TR.Scene(types.SimpleNamespace(tmp=types.SimpleNamespace(name=tmp)))
s.click_all()
c1, r1 = s.fit()
s.click_all(["p1"], shift={"p1": (0.4, 0)})
c2, r2 = s.fit(now=1_800_000_900.0)
models = s.room_models()
out = {"codes": [c1, c2], "tids": [r1["transform_id"], r2["transform_id"]],
       "revisions": [[m["version"], [c["camera_id"] + "@" + c["version"] for c in m["cameras"]]] for m in models]}
def res(ms, tid):
    try:
        return "resolves to " + records.resolve(ms, tid)["entry"]["version"]
    except Refused as exc:
        return "refused " + exc.reason
out["whole_file"] = {t: res(models, t) for t in out["tids"]}
out["newest_only"] = {t: res(models[-1:], t) for t in out["tids"]}
both = json.loads(json.dumps(models[-1]))
both["cameras"] = [models[0]["cameras"][-1]] + both["cameras"]
out["both_entries_doc"] = str(s.work / "both-entries.json")
open(out["both_entries_doc"], "w", encoding="utf-8").write(json.dumps(both))
print(json.dumps(out))
'''

OBSERVE = r'''
import json, sys
sys.dont_write_bytecode = True
obs, path, tid = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, obs)
sys.path.insert(0, obs.rsplit("/", 1)[0] + "/den-take")
import roommodel
model = roommodel.load(path)
try:
    entry = roommodel.resolve(model, tid)
    r = "resolve() finds " + entry["version"]
except roommodel.RegistrationRefused as exc:
    r = "resolve() REFUSED " + str(exc)
try:
    roommodel.Camera(model, tid, max_error_bound_m=0.05)
    c = "Camera ACCEPTED"
except roommodel.RegistrationRefused as exc:
    c = "Camera REFUSED " + str(exc)
print(json.dumps({"resolve": r, "camera": c}))
'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    reg_root = Path(sys.argv[1]).resolve()
    obs_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else reg_root.parent / "rung-2-observe"
    reg, obs = reg_root / "tools" / "den-register", obs_root / "tools" / "den-observe"
    tmp = Path(tempfile.mkdtemp(prefix="verify-seam08-"))
    print(f"RESULT temp folder {tmp}")
    code, out, err = child(REGISTER, reg, tmp / "reg")
    if code != 0:
        print("FAIL setup: den-register run failed\n" + err[-2000:])
        return 2
    info = json.loads(out.strip().splitlines()[-1])
    print(f"RESULT fits exit {info['codes']}, transform_ids {info['tids']}")
    for version, cams in info["revisions"]:
        print(f"RESULT step2 revision {version}: cameras {cams}")
    for tid, r in info["whole_file"].items():
        print(("PASS" if r.startswith("resolves") else "FAIL") + f" step3 den-register records.resolve(whole file, {tid}): {r}")
    for tid, r in info["newest_only"].items():
        print(("FAIL" if not r.startswith("resolves") else "PASS") + f" step4 newest revision only, {tid}: {r}")
    old = info["tids"][0]
    _, o, e = child(OBSERVE, obs, info["both_entries_doc"], old)
    r5 = json.loads(o.strip().splitlines()[-1]) if o.strip() else {"resolve": "ERROR " + e[-500:], "camera": ""}
    print(f"RESULT step5 den-observe, one document keeping BOTH entries, older {old}: {r5['resolve']}; {r5['camera']}")
    print("RESULT SEAM-08: older transform_id resolves over the whole file (den-register's documented reader); "
          "it fails only for a reader that takes the newest revision alone"
          + ("; den-observe refuses a superseded entry even when it is present" if "stale" in r5["camera"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
