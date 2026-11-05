#!/usr/bin/env python3
"""Verifier (intent lens) for the R1 test-coverage findings mutant-mount-thresholds,
mutant-depth-valid-fraction and mutant-round-trip.

For each mutant (M11, M12, M13, M14, M16 of mutate_den_register.py):
  1. copy tools/den-register, tools/den-take and docs/architecture/live-v1 from the
     checkout into a fresh temp folder (git init'd, as the work-folder test needs),
     apply the one text replacement, run the WHOLE suite
     (`python -B -m unittest discover -s . -f`) and record caught / survived;
  2. run a behavioural differential on the clean copy and on the mutant copy:
       M11  depth_at on a 7x7 window with 10 of 49 valid pixels (fraction 0.5)
       M12  check_mount with 10 comparable, unchanged cells (minimum 16)
       M13  check_mount with every cell 70 mm nearer at 3.5 m (50 mm / 3 %)
       M14  check_mount with 38 of 64 cells 700 mm nearer (changed fraction 0.5)
       M16  (a) the finding's suggested test: a pre-existing entry with a different
                pose already holds the "next" version, then a second fit;
            (b) a forced version collision (next_version patched to reuse .1);
            (c) the largest round-trip distance math.dist(back, fitted) seen.
     A mutant whose differential output equals the clean one is equivalent on these
     inputs (no test can kill it there).
The checkout is never modified. Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_intent2_mutants.py <root of the rung-1-register checkout> [M11 M12 M13 M14 M16]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ONLY = {a for a in sys.argv[2:]}  # optional: M11 M12 ... to run a subset

MUTANTS = [
    ("M11-depth-valid-fraction-ignored", "scene.py",
     "if not values or len(values) < min_valid_fraction * total:", "if not values:", "M11"),
    ("M12-mount-min-cells-ignored", "scene.py", "if compared < minimum_cells:", "if compared < 1:", "M12"),
    ("M13-mount-relative-tolerance-dropped", "scene.py",
     "changed += abs(value - reference) > max(tolerance_mm, tolerance_fraction * reference)",
     "changed += abs(value - reference) > tolerance_mm", "M13"),
    ("M14-mount-changed-fraction-doubled", "scene.py", 'status = "changed" if changed > changed_fraction * compared',
     'status = "changed" if changed > min(0.99, 2 * changed_fraction) * compared', "M14"),
    ("M16-round-trip-check-disabled", "register.py",
     "if resolved[\"invalidated\"] or math.dist(back, fitted) > 1e-9:",
     "if resolved[\"invalidated\"] or math.dist(back, fitted) > 1e9:", "M16"),
]

SNIPPET = r'''
import array, io, json, math, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
import method, records, register, scene, synthetic, worksheet as ws
which = sys.argv[1]
KW = dict(tolerance_mm=50, tolerance_fraction=0.03, changed_fraction=0.5, minimum_cells=16)
def sig(cells):
    return {"grid": [8, 8], "shape": [400, 640], "cells": cells}
if which == "M11":
    plane = array.array("H", [0] * (640 * 400))
    for i in range(10):                      # 10 valid pixels of the 49 around (100, 100)
        plane[(97 + i // 7) * 640 + 97 + i % 7] = 2000
    try:
        r = scene.depth_at({"depth": plane}, 100, 100, window_px=3, min_valid_fraction=0.5)
        print("OUT accepted z_m=%.3f from %d of %d pixels" % (r["z_m"], r["valid"], r["pixels"]))
    except scene.Refused as exc:
        print("OUT refused", exc.reason)
elif which == "M12":
    ref = [[3500.0] * 8 for _ in range(8)]
    cur = [[3500.0 if r * 8 + c < 10 else None for c in range(8)] for r in range(8)]
    print("OUT", scene.check_mount(sig(ref), sig(cur), **KW))
elif which == "M13":
    ref = [[3500.0] * 8 for _ in range(8)]
    cur = [[3430.0] * 8 for _ in range(8)]
    print("OUT", scene.check_mount(sig(ref), sig(cur), **KW))
elif which == "M14":
    ref = [[3500.0] * 8 for _ in range(8)]
    cur = [[2800.0 if r * 8 + c < 38 else 3500.0 for c in range(8)] for r in range(8)]
    print("OUT", scene.check_mount(sig(ref), sig(cur), **KW))
elif which == "M16":
    text = Path("REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: m[1] + " = " + m[2], text, flags=re.M)
    m = method.parse(text)
    dists = []
    real_dist = math.dist
    class M:  # register.math wrapper that records every distance taken
        def __getattr__(self, n):
            return getattr(math, n)
        @staticmethod
        def dist(a, b):
            d = real_dist(a, b)
            if sys._getframe(1).f_code.co_name == "build_records":   # only the round-trip comparison
                dists.append(d)
            return d
    def scene_work(tmp):
        depth, left, seen = synthetic.render()
        take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"; work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
        sheet = ws.load(work / "worksheet.json", need_positions=False)
        return take, work, seen, sheet
    def click(work, take, seen, sheet, shift=0.0):
        s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
        for pid, (u, v, _) in seen.items():
            s.click({"id": pid, "u": round(u + (shift if pid == "p1" else 0), 1), "v": round(v, 1)})
        s.done()
    # (a) the finding's suggested test
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp); take, work, seen, sheet = scene_work(tmp)
        click(work, take, seen, sheet)
        c1, r1 = register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
        models = records.read_jsonl(work / "room-model.jsonl", "x")
        forged = json.loads(json.dumps(models[-1]))
        e = forged["cameras"][-1]
        v1 = e["version"]; e["version"] = v1[:-1] + "2"                    # holds the "next" version
        e["pose"]["room_from_camera"][0][3] += 0.5                        # with a different pose
        forged["version"] = "den-v2"
        records.append_jsonl(work / "room-model.jsonl", forged)
        click(work, take, seen, sheet, shift=0.4)
        register.math = M()
        c2, r2 = register.run_fit(work, take, m=m, now=lambda: 1_800_000_900.0, out=io.StringIO())
        register.math = math
        cal = records.read_jsonl(work / "calibration-records.jsonl", "x")
        print("OUT (a) first", r1["result"], r1["transform_id"], "| second", r2["result"], r2["transform_id"],
              [x["reason"] for x in r2["reasons"]], "| round_trip", cal[-1]["checks"]["round_trip"]["result"])
    # (b) a forced version collision with a different pose
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp); take, work, seen, sheet = scene_work(tmp)
        click(work, take, seen, sheet)
        register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
        click(work, take, seen, sheet, shift=0.4)
        real_next = records.next_version
        records.next_version = lambda rm, cid, date: date + ".1"
        register.math = M()
        c3, r3 = register.run_fit(work, take, m=m, now=lambda: 1_800_000_900.0, out=io.StringIO())
        register.math = math
        records.next_version = real_next
        print("OUT (b) forced collision:", r3["result"], [x["reason"] for x in r3["reasons"]])
    print("OUT (c) build_records round-trip distances math.dist(back, fitted):", dists)
'''


def make_copy(tmp):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__", "work"))
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)


def suite(tmp):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-f"],
                         cwd=tmp / "tools" / "den-register", capture_output=True, text=True, timeout=900)
    tail = (out.stderr.strip().splitlines() or [""])[-1]
    ran = next((line for line in out.stderr.splitlines() if line.startswith("Ran ")), "")
    return out.returncode, f"{ran}; {tail}"


def differential(tmp, which):
    snippet = tmp / "verify_snippet.py"
    snippet.write_text(SNIPPET, encoding="utf-8")
    out = subprocess.run([sys.executable, "-B", str(snippet), which], cwd=tmp / "tools" / "den-register",
                         capture_output=True, text=True, timeout=300)
    lines = [line[4:] for line in out.stdout.splitlines() if line.startswith("OUT ")]
    if out.returncode != 0:
        lines.append("ERROR " + (out.stderr.strip().splitlines() or ["?"])[-1])
    return lines


with tempfile.TemporaryDirectory() as base:
    base = Path(base)
    clean = base / "clean"
    make_copy(clean)
    code, note = suite(clean)
    print(f"baseline full suite: exit {code} ({note})")
    if code != 0:
        print("FAIL baseline does not pass")
        sys.exit(2)
    rows = []
    for mid, rel, old, new, which in MUTANTS:
        if ONLY and which not in ONLY:
            continue
        tmp = base / mid
        make_copy(tmp)
        path = tmp / "tools" / "den-register" / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (mid, text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")
        code, note = suite(tmp)
        clean_out, mutant_out = differential(clean, which), differential(tmp, which)
        equivalent = clean_out == mutant_out
        rows.append((mid, "caught" if code else "survived", equivalent))
        print(f"\n{mid}: full suite {'CAUGHT' if code else 'SURVIVED'} ({note})")
        for line in clean_out:
            print(f"   clean : {line}")
        for line in mutant_out:
            print(f"   mutant: {line}")
        print(f"   behaviour on these inputs: {'SAME (equivalent here)' if equivalent else 'DIFFERS (meaningful survivor)'}")
        shutil.rmtree(tmp)
    print()
    for mid, result, equivalent in rows:
        print(f"RESULT {mid:<40} {result:<9} {'equivalent on probed inputs' if equivalent else 'non-equivalent'}")
