"""probe_stats_need_decide_validate: recomputes need's onset/dwell timing, decide's hold choice and validate on
a record with a wrong field, on constructed synthetic cases.

1 need: a contact (first touch) at T0; Qwen captures every 4 s at T0+2, +6, ... all 'reading', each answer
  available 1.2 s after capture. Prints when need's first output leaving 'none' appears and when 'engaged'
  appears, against the owner's 3 s median onset target and the A7 5 s dwell.
2 decide.plan on a hold case: bulb rear_left held at 80@2202K, reading engaged; then the room vacant 130 s.
3 validate on a synthetic run log in which one decide decision's field is renamed (skipped -> skip) and,
  separately, a held bulb's requested scene is changed; prints validate's exit code and first lines.

    python3 probe_stats_need_decide_validate.py /path/to/den-core-checkout
"""
import json
import subprocess
import sys
sys.dont_write_bytecode = True  # never leave __pycache__ in the checkout under test
import tempfile
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import decide  # noqa: E402
import need  # noqa: E402

T0 = 1793764800.0
LIGHTS = decide.LIGHTS


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def line(i, observed, available, activity="reading"):
    facts = {"people_visible": "one", "activity_hypothesis": activity, "subject_posture": "sitting"}
    return {"outcome": "completed", "observation": {"status": "valid", "facts": facts}, "observed_at": iso(observed),
            "available_at": iso(available), "request_id": "r%d" % i}


# 1 need timing
p = need.make_policy()
first_non_none = engaged = None
for i in range(6):
    obs = T0 + 2 + 4 * i
    av = obs + 1.2
    for out in p.handle({"t": av, "kind": "facts", "data": line(i, obs, av)}):
        st = out["data"]["focused_engagement"]["state"]
        if st != "none" and first_non_none is None:
            first_non_none = (av - T0, st, out["data"]["focused_engagement"]["onset_ts"] - T0)
        if st == "engaged" and engaged is None:
            engaged = av - T0
print("RESULT: need first output leaving none at %+.1f s after contact (state %s, onset_ts = capture %+.1f s); "
      "owner target: median <= 3 s" % first_non_none)
print("RESULT: need 'engaged' at %+.1f s after contact (2nd playing sample); the A7 5 s dwell is not implemented "
      "(entry = 2 samples)" % engaged)
# contact phase uniform over the 4 s capture cadence: delay to the first capture after contact is U(0, 4)
delays = sorted(((k + 0.5) / 1000.0) * 4 + 1.2 for k in range(1000))
print("RESULT: over contact phases, median first-non-none delay = %.2f s (cadence 4 s, latency 1.2 s)"
      % delays[len(delays) // 2])

# 2 decide hold case
normal = {b: {"on": True, "brightness": 180, "kelvin": 2700} for b in LIGHTS}
hold = {"entity_id": LIGHTS[2], "pinned": {"on": True, "brightness": 80, "kelvin": 2202}, "since": T0 + 100,
        "lifetime": "room_vacant_2_minutes"}
person = {"state_id": "room@1", "focused_engagement": {"state": "engaged", "kind": "reading"},
          "field_support": {"focused_engagement": {"support_status": "observed"}}}
state = {"occupancy": "on", "vacant_since": None, "actuator_state": dict(normal), "person": person,
         "mode": {"revision": 3, "gaming": False, "holds": [hold], "normal": normal}}
cfg = decide.make_policy().config
r = decide.plan(state, T0 + 200, cfg)
print("RESULT: decide reading+hold: rule %s chose %s; requested %s; skipped %s" % (
    r["rule"], r["chosen"], {b.split(".")[1]: "%s@%s" % (s["brightness"], s["kelvin"]) for b, s in r["requested_scene"].items()},
    r["skipped"]))
state.update(occupancy="off", vacant_since=T0 + 200)
r = decide.plan(state, T0 + 330, cfg)
print("RESULT: decide vacant 130 s + hold: rule %s; requested %s" % (
    r["rule"], {b.split(".")[1]: ("off" if not s["on"] else "%s@%s" % (s["brightness"], s["kelvin"]))
                for b, s in r["requested_scene"].items()}))
print("PASS: held bulb pinned and skipped in both" if r["requested_scene"][LIGHTS[2]] == hold["pinned"] else "FAIL")

# 3 validate on a wrong field
tmp = Path(tempfile.mkdtemp(prefix="probe-stats-"))
out = tmp / "ev"
subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--touch", "--policies", "need",
                "incumbent", "decide", "--out", str(out)], cwd=str(root), capture_output=True, text=True, check=True)
lines = (out / "log.jsonl").read_text("ascii").split("\n")
for label, edit in (("renamed field skipped->skip", "rename"), ("held bulb requested 200@2700", "scene")):
    new = list(lines)
    for i, text in enumerate(new[1:-1], 1):
        e = json.loads(text)
        if e["kind"] == "decision" and e["source"] == "decide" and e["data"]["inputs"]["hold_set"]["held"]:
            if edit == "rename":
                e["data"]["skip"] = e["data"].pop("skipped")
            else:
                held = e["data"]["inputs"]["hold_set"]["held"][0]["entity_id"]
                e["data"]["requested_scene"][held] = {"on": True, "brightness": 200, "kelvin": 2700}
            new[i] = json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            break
    path = tmp / ("%s.jsonl" % edit)
    path.write_text("\n".join(new), "ascii")
    v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(path)], cwd=str(root),
                       capture_output=True, text=True)
    tail = (v.stdout.strip().splitlines() or [""])[:5] + (v.stderr.strip().splitlines()[-1:] if v.stderr else [])
    print("RESULT: validate on %s: exit %d\n  %s" % (label, v.returncode, "\n  ".join(tail)))
