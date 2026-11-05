#!/usr/bin/env python3
"""verify_mutant-drift-occupancy: verifier's re-run of finding mutant-drift-occupancy (rung 10).

What it checks (synthetic only, no network, nothing written outside a temp dir):
  1. copies tools/den-verify (without __pycache__) and docs/architecture/live-v1
     of the given checkout into a temp dir and runs the WHOLE den-verify suite
     (no failfast) as a baseline;
  2. applies mutant M10 alone (drop `not occ` from the drift rule in
     Run.explain) and runs the whole suite again;
  3. in the baseline copy and the mutant copy, scores two synthetic decisions
     built like the suite's DaylightAndDrift cases (a dim-to-bright change on
     front_left, the change landing 40% off in the same direction as the
     residual, steady window daylight), each with its verify frame OCCUPIED:
       occ-far-low   residual negative, the only person 3.6 m from the point
                     (beyond shadow_radius_m), so shadow does not apply;
       occ-near-high residual positive, a person within the radius (shadow
                     needs a negative residual, so it does not apply);
     VERIFY-V1.md's rule 3 says drift needs "the frame is not occupied", so the
     expected cause is `unexplained` for both. It prints the Decision.verify
     cause, the verify frame's residual flags and misses_explained for each copy.
A FAIL line marks M10 surviving the suite while turning those misses into drift.

Run:
    python3 verify_mutant-drift-occupancy.py <root of the rung-10-verify checkout> [work dir parent]
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OLD = "if (inc[\"status\"] == \"outside\" and not occ"
NEW = "if (inc[\"status\"] == \"outside\""

PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import method
import synthetic as syn
from light_model import LightModel
from records import Clock
from score import Run
B0 = syn.HUE[0]
m = method.parse(method.closed_copy(method.lf_text(Path(sys.argv[1]) / "VERIFY-V1.md"))).scored()
model = syn.hue_model({"desk-1": {B0: 400}})          # desk-1 sits at x=0, y=0
k = LightModel(model).predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"]

def case(did, t, factor, bg=150.0):
    ref = syn.light_state(t, {"desk-1": bg + 0.2 * k}, anchored=False, windows={"west-1": 0.5}, ident=f"ls-{did}")
    dec = syn.hue_decision(did, t + 0.5, {B0: 0.2}, {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}},
                           f"ls-{did}", targets={"A": {"surface_id": "desk-1", "lx": 300}})
    # the change itself lands at `factor` x the predicted change (0.6 k)
    after = syn.light_state(t + 4, {"desk-1": bg + 0.2 * k + 0.6 * k * factor}, anchored=False,
                            windows={"west-1": 0.5})
    return dec, [ref, after]

t = syn.T0
d1, f1 = case("occ-far-low", t, 0.6)
d2, f2 = case("occ-near-high", t + 100, 1.4)
occupancy = [{"start": t, "end": t + 10, "people": [{"track_id": "A", "xy": [3.0, 2.0]}]},
             {"start": t + 100, "end": t + 110, "people": [{"track_id": "B", "xy": [0.2, 0.1]}]}]
decs, states, rep = Run(m, LightModel(model), [d1, d2], f1 + f2, occupancy=occupancy,
                        clock=Clock(syn.UTC_OFFSET_S)).run()
out = {"shadow_radius_m": m["shadow_radius_m"], "cases": {}}
for d, ts in ((decs[0], t + 4), (decs[1], t + 104)):
    st = next(s for s in states if s["ts"] == ts)
    res = next(e for e in st["residual"] if e["surface_id"] == "desk-1")
    pt = d["verify"]["points"][0]
    out["cases"][d["decision_id"]] = {
        "status": pt["status"], "residual": pt["residual"],
        "increment": d["verify"]["increments"][0]["status"],
        "cause": d["verify"]["explanation"][0]["cause"] if d["verify"]["explanation"] else None,
        "flags": res["flags"]}
me = rep["misses_explained"]
out["misses_explained"] = {k2: me[k2] for k2 in ("daylight", "shadow", "drift", "unexplained", "share_explained",
                                                 "share_explained_admitted")}
out["rows_occupied"] = {r["decision_id"]: r["occupied"] for r in rep["rows"]}
print(json.dumps(out))
'''


def suite(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-verify",
                        "-p", "test_*.py"], cwd=root, env=env, capture_output=True, text=True, timeout=600)
    err = p.stderr or ""
    ran = re.search(r"Ran (\d+) test", err)
    failing = re.findall(r"^(?:FAIL|ERROR): (\S+)", err, re.M)
    return p.returncode, int(ran[1]) if ran else None, failing


def cases(root):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", "-c", PROBE, str(root / "tools" / "den-verify")],
                       cwd=root, env=env, capture_output=True, text=True, timeout=600)
    if p.returncode:
        raise SystemExit(f"FAIL case probe crashed: {p.stderr}")
    return json.loads(p.stdout)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    parent = sys.argv[2] if len(sys.argv) > 2 else None
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        base = Path(tmp) / "base"
        (base / "tools").mkdir(parents=True)
        shutil.copytree(src / "tools" / "den-verify", base / "tools" / "den-verify",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(src / "docs" / "architecture" / "live-v1", base / "docs" / "architecture" / "live-v1")
        code, ran, failing = suite(base)
        print(f"RESULT baseline suite exit={code} ran={ran} failing={failing}")
        if code:
            print("FAIL baseline suite does not pass")
            return 1
        b = cases(base)
        print(f"RESULT baseline: {json.dumps(b)}")
        work = Path(tmp) / "M10"
        shutil.copytree(base, work)
        f = work / "tools" / "den-verify" / "score.py"
        text = f.read_text(encoding="utf-8")
        if text.count(OLD) != 1:
            print(f"FAIL mutant M10 not applied: pattern count {text.count(OLD)}")
            return 1
        f.write_text(text.replace(OLD, NEW), encoding="utf-8")
        code, ran, failing = suite(work)
        print(f"RESULT M10 suite exit={code} ran={ran} failing={failing} -> {'caught' if code else 'survived'}")
        mu = cases(work)
        print(f"RESULT M10: {json.dumps(mu)}")
        for did in b["cases"]:
            bc, mc = b["cases"][did], mu["cases"][did]
            print(f"RESULT {did}: occupied={b['rows_occupied'][did]} status={bc['status']} residual={bc['residual']} "
                  f"increment={bc['increment']} | cause baseline={bc['cause']} M10={mc['cause']} | "
                  f"flags baseline={bc['flags']} M10={mc['flags']}")
        if not code and any(b["cases"][d]["cause"] != mu["cases"][d]["cause"] for d in b["cases"]):
            print("FAIL M10 survives the whole suite, yet it turns occupied-frame misses that VERIFY-V1 rule 3 "
                  "leaves unexplained into drift (Decision.verify cause, LightState residual flags and "
                  f"share_explained_admitted {b['misses_explained']['share_explained_admitted']} -> "
                  f"{mu['misses_explained']['share_explained_admitted']})")
        else:
            print("PASS M10 is caught or equivalent on these cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
