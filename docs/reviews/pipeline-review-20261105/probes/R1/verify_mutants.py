#!/usr/bin/env python3
"""Verifier for findings mutant-fit-residual-gate (M3), mutant-collinear-floor (M5)
and mutant-error-bound-composition (M9) on tools/den-register (rung 1).

For each mutant: copy tools/den-register, tools/den-take and docs/architecture/live-v1
from the checkout into a temp folder (git init, as the work-folder test needs),
apply one exact text replacement to register.py, and run the FULL test module
(python -B -m unittest test_den_register), not only FitTests. Then run one
distinguishing synthetic scene in the clean copy and in the mutant copy, to show
the mutant changes a verdict or a written number (so it is not an equivalent
mutant). The checkout is never modified. Synthetic data only; temp dirs only;
contacts nothing.

    python3 -B verify_mutants.py <root of the rung-1-register checkout>
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

MUTANTS = [
    ("M3", "if residual > gate:", "if residual > 2 * gate:"),
    ("M5", 'if spread < m["min_spread_m"]:', 'if spread < m["min_spread_m"] / 2:'),
    ("M9", 'bound = max(result["residuals_m"] + [p["error_m"] for p in check_pairs] + (result["loo_m"] or []))',
     'bound = max(result["residuals_m"])'),
]

SCENE = r'''
import io, json, math, re, sys, tempfile
from pathlib import Path
TOOL = Path(sys.argv[1]); which = sys.argv[2]
sys.path.insert(0, str(TOOL))
import method, records, register, rigid, synthetic, worksheet as ws

def closed():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed", text, flags=re.M)
    return method.parse(text)

def run(points, fit, checks, offsets):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        depth, left, seen = synthetic.render(points)
        take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"; work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet(points, fit, checks, offsets)), encoding="utf-8")
        m = closed()
        sheet = ws.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
        for pid in list(fit) + list(checks):
            u, v, _ = seen[pid]
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
        entry = records.read_jsonl(work / "room-model.jsonl", "x")[-1]["cameras"][-1] if code == 0 else {}
        return report, entry

P = synthetic.POINTS
R, t = synthetic.camera_pose()
cam = {k: synthetic.to_camera(R, t, v) for k, v in P.items()}
if which == "M3":
    # one fit point mismeasured so its residual lands between 5 and 10 cm while both checks stay under 5 cm
    chosen = None
    for d in [x / 1000 for x in range(60, 260, 5)]:
        for direction in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0], [0, -1, 0], [0, 0, -1]):
            for pid in ("p1", "p2", "p3", "p4"):
                room = {k: list(v) for k, v in P.items()}
                room[pid] = [room[pid][i] + d * direction[i] for i in range(3)]
                res = rigid.fit([cam[k] for k in synthetic.FIT], [room[k] for k in synthetic.FIT])
                chk = [math.dist(rigid.apply(res["rotation"], res["translation"], cam[k]), room[k]) for k in synthetic.CHECKS]
                if 0.055 < res["max_m"] < 0.095 and max(chk) < 0.045:
                    chosen = (pid, [d * v for v in direction]); break
            if chosen: break
        if chosen: break
    pid, off = chosen
    report, entry = run(P, synthetic.FIT, synthetic.CHECKS, {pid: off})
    worst = max(p.get("residual_m", 0) for p in report["pairs"])
    checks = [round(p["error_m"] * 100, 2) for p in report["pairs"] if "error_m" in p]
    print(f"SCENE M3 offset {pid} {off}: {report['result']} {[r['reason'] for r in report['reasons']]}; "
          f"max residual {worst*100:.2f} cm; checks {checks} cm")
elif which == "M5":
    LINE = {"l1": [1.6, 2.0, 0.3], "l2": [2.2, 2.6, 0.3], "l3": [2.8, 3.2, 0.3], "l4": [3.1, 3.5, 0.3],
            "c1": [3.0, 3.5, 1.2], "c2": [1.6, 2.6, 0.4]}
    best = None
    for k in range(1, 200):
        d = k / 200
        pts = dict(LINE); pts["l2"] = [2.2 + d * 0.7071, 2.6 - d * 0.7071, 0.3]
        sp = rigid.spread([pts[i] for i in ("l1", "l2", "l3", "l4")])
        if 0.15 < sp < 0.22:
            best = (pts, sp); break
    pts, sp = best
    depth, left, seen = synthetic.render(pts)
    assert set(seen) == set(pts), seen
    report, entry = run(pts, ("l1", "l2", "l3", "l4"), ("c1", "c2"), None)
    print(f"SCENE M5 fit points spread {sp*100:.1f} cm (min_spread 25 cm, half 12.5 cm): {report['result']} "
          f"{[r['reason'] for r in report['reasons']]}")
elif which == "M9":
    off = {"p1": [-0.002, -0.0052, -0.015], "p2": [-0.0037, 0.0328, -0.0091], "p3": [-0.002, 0.0169, 0.0112],
           "p4": [0.0114, 0.0129, -0.0295], "c1": [-0.013, 0.0019, 0.0075], "c2": [0.0194, 0.007, -0.0036]}
    report, entry = run(P, synthetic.FIT, synthetic.CHECKS, off)
    print(f"SCENE M9: {report['result']}; written error_bound_m {entry.get('error_bound_m')}; "
          f"loo_max {report['fit']['loo_max_m']}; residual max {report['fit']['max_m']}")
'''


def make_copy(dest):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__", "work"))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)


def suite(dest):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_den_register"],
                         cwd=dest / "tools" / "den-register", capture_output=True, text=True, timeout=600)
    lines = out.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "?")
    return out.returncode, ran + " / " + (lines[-1] if lines else ""), [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]


def scene(dest, which):
    script = dest / "scene_probe.py"
    script.write_text(SCENE, encoding="utf-8")
    out = subprocess.run([sys.executable, "-B", str(script), str(dest / "tools" / "den-register"), which],
                         capture_output=True, text=True, timeout=300)
    return (out.stdout + out.stderr).strip()


with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    clean = base / "clean"
    make_copy(clean)
    code, summary, failing = suite(clean)
    print(f"RESULT baseline full module: exit {code}; {summary}")
    for mid, old, new in MUTANTS:
        dest = base / mid
        make_copy(dest)
        path = dest / "tools" / "den-register" / "register.py"
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (mid, text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")
        code, summary, failing = suite(dest)
        verdict = "SURVIVED" if code == 0 else "CAUGHT"
        print(f"RESULT {mid} full module: {verdict} (exit {code}; {summary}) {failing[:2]}")
        print(f"  clean : {scene(clean, mid)}")
        print(f"  mutant: {scene(dest, mid)}")
