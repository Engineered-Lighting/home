#!/usr/bin/env python3
"""Verifier probe for finding mutant-drift-occupancy (den-verify, rung 10).

What it checks: an OCCUPIED verify frame whose point misses and whose
increment also misses in the same direction, with nobody within
shadow_radius_m (two cases: a positive residual, and a negative residual with
the person 10 m away). VERIFY-V1.md's drift rule requires "the frame is not
occupied", so the real code should say 'unexplained'; the M10 mutant (drop
'not occ') says 'drift'. Also shows whether the cause changes the hit-rate
verdict (it should not: causes are reported, never gated).
Synthetic records only; no network; writes only to a temp dir.

Run:
    python3 verify_intent_mutant-drift-occupancy.py <root of the rung-10-verify checkout>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MUT = ("if (inc[\"status\"] == \"outside\" and not occ", "if (inc[\"status\"] == \"outside\"")

CHILD = r"""
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
model = syn.hue_model({"desk-1": {B0: 400}})
k = LightModel(model).predict({B0: {"level": 1, "cct_k": 2700}})["desk-1"]
out = {}
for name, factor, person_xy in (("positive residual, occupied", 1.4, [0.2, 0.1]),
                                ("negative residual, person 10 m away", 0.6, [10.0, 10.0]),
                                ("negative residual, unoccupied", 0.6, None)):
    t = syn.T0
    bg = 150.0
    ref = syn.light_state(t, {"desk-1": bg + 0.2 * k}, anchored=False, windows={"west-1": 0.5}, ident="ls-ref")
    dec = syn.hue_decision("d-1", t + 0.5, {B0: 0.2},
                           {B0: {"level": 0.8, "steps": [{"kind": "send", "sent_at": t + 0.5}]}}, "ls-ref",
                           targets={"A": {"surface_id": "desk-1", "lx": 300}})
    after = syn.light_state(t + 4, {"desk-1": bg + 0.2 * k + 0.6 * k * factor}, anchored=False,
                            windows={"west-1": 0.5})
    occ = [] if person_xy is None else [{"start": t + 3, "end": t + 10,
                                         "people": [{"track_id": "B", "xy": person_xy}]}]
    d, s, r = Run(m, LightModel(model), [dec], [ref, after], occupancy=occ, clock=Clock(syn.UTC_OFFSET_S)).run()
    v = d[0]["verify"]
    ls = next(x for x in s if x["ts"] == t + 4)
    out[name] = {"status": v["points"][0]["status"], "residual": v["points"][0]["residual"],
                 "increment": v["increments"][0]["status"],
                 "cause": (v["explanation"] or [{}])[0].get("cause"),
                 "flags": ls["residual"][0]["flags"] if ls["residual"] else None,
                 "verdict": r["hit_rate"]["verdict"], "drift_count": r["misses_explained"]["drift"]}
print(json.dumps(out))
"""


def main():
    root = Path(sys.argv[1]).resolve()
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, mut in (("real", None), ("M10", MUT)):
            work = Path(tmp) / name
            (work / "tools").mkdir(parents=True)
            shutil.copytree(root / "tools" / "den-verify", work / "tools" / "den-verify",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(root / "docs" / "architecture" / "live-v1", work / "docs" / "architecture" / "live-v1")
            f = work / "tools" / "den-verify" / "score.py"
            if mut:
                text = f.read_text(encoding="utf-8")
                assert text.count(mut[0]) == 1
                f.write_text(text.replace(mut[0], mut[1]), encoding="utf-8")
            p = subprocess.run([sys.executable, "-B", "-c", CHILD, str(work / "tools" / "den-verify")],
                               capture_output=True, text=True, timeout=600)
            if p.returncode:
                print(f"FAIL {name}: {p.stderr[-600:]}")
                return 1
            res[name] = json.loads(p.stdout)
    for case in res["real"]:
        for name in ("real", "M10"):
            print(f"RESULT {case:38} {name:4} {res[name][case]}")
    occ_cases = [c for c in res["real"] if "unoccupied" not in c]
    real_ok = all(res["real"][c]["cause"] == "unexplained" for c in occ_cases)
    mut_drift = all(res["M10"][c]["cause"] == "drift" for c in occ_cases)
    print(f"{'PASS' if real_ok else 'FAIL'} real code: occupied, no shadow -> unexplained (as VERIFY-V1's rule says)")
    print(f"{'PASS' if mut_drift else 'FAIL'} M10 mutant: the same frames -> drift (not equivalent)")
    same_verdict = all(res["real"][c]["verdict"] == res["M10"][c]["verdict"] for c in res["real"])
    print(f"RESULT verdict unchanged by the mutant in every case: {same_verdict} (causes are reported, never gated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
