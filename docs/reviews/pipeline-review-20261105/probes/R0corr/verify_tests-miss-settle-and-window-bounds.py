"""R0corr verify (lens reproduce): finding tests-miss-settle-and-window-bounds.

What it checks
  For mutants M14 (SETTLE_S grouping strict '<' instead of '<=') and M22 (WINDOW_S 5 s -> 3 s), each applied alone
  to a temporary copy of the tool + den-core + den_lighting:
    1. does the tool's own unittest suite (failfast) still pass?
    2. does the mutant change exposure.json on a synthetic export?
       - app_4s: the HA app opens a hold at the call and the bulb's report (with the user id) lands 4 s later;
       - settle_3s: two switch reports whose holds are exactly 3.0 s apart (integer offsets, exact in floating point).

How to run (stdlib only, synthetic data only, never writes inside the checkout it is given):
    python3 -B verify_tests-miss-settle-and-window-bounds.py /path/to/branch/checkout [work_parent_dir]
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK_PARENT = sys.argv[2] if len(sys.argv) > 2 else None
CAP = "tools/correction-capture/capture.py"
COPY = ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting")
MUTANTS = {
    "M14": ('c["t"] - actions[-1][-1]["t"] <= SETTLE_S', 'c["t"] - actions[-1][-1]["t"] < SETTLE_S'),
    "M22": ("WINDOW_S = 5.0", "WINDOW_S = 3.0"),
}
PRELUDE = r'''
import json, sys, tempfile
sys.path.insert(0, "tools/correction-capture")
import capture, test_capture as tc
T0, L0, L1 = tc.T0, tc.L0, tc.L1
def run(den):
    with tempfile.TemporaryDirectory() as tmp:
        rec = capture.capture(*den.write(tmp))[0]
    ng = rec["planning"]["strata"]["no_game"]
    return {"actions": [(c["source"], c["action"]["n_changes"]) for c in rec["corrections"]],
            "people": ng["people"]["count"], "every_hold": ng["every_hold"]["count"]}
'''
SCENARIO = r'''
out = {}
den = tc.Den()
den.hold(T0 + 200, {L1: tc.WARM})
den.bulb(T0 + 204, L1, True, user=True)
den.row(T0 + 3600)
out["app_4s"] = run(den)
den = tc.Den()
den.bulb(T0 + 99.99, L0, False); den.hold(T0 + 100, {L0: tc.OFF})
den.bulb(T0 + 102.99, L1, False); den.hold(T0 + 103, {L1: tc.OFF})
den.row(T0 + 3600)
out["settle_3s"] = run(den)
print(json.dumps(out))
'''


def copy_tree(base, name, edit=None):
    dst = base / name
    for rel in COPY:
        shutil.copytree(ROOT / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dst / ".git").mkdir()
    if edit:
        path = dst / CAP
        text = path.read_text("utf-8")
        if text.count(edit[0]) != 1:
            raise SystemExit("FAIL: mutant pattern found %d times" % text.count(edit[0]))
        path.write_text(text.replace(*edit), "utf-8")
    return dst


def suite_rc(copy):
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/correction-capture",
                           "-p", "test_*.py"], cwd=copy, capture_output=True, text=True, timeout=600).returncode


def driver(copy):
    proc = subprocess.run([sys.executable, "-B", "-c", PRELUDE + SCENARIO], cwd=copy, capture_output=True, text=True,
                          timeout=300)
    if proc.returncode:
        raise SystemExit("FAIL: driver error in %s\n%s" % (copy, proc.stderr))
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main():
    with tempfile.TemporaryDirectory(prefix="verify-settle-", dir=WORK_PARENT) as tmp:
        base = Path(tmp)
        orig = copy_tree(base, "orig")
        ref = driver(orig)
        print("RESULT: unmutated suite rc=%d; %s" % (suite_rc(orig), json.dumps(ref)))
        for mid, edit in MUTANTS.items():
            work = copy_tree(base, mid, edit)
            rc, got = suite_rc(work), driver(work)
            changed = {k: got[k] for k in ref if ref[k] != got[k]}
            print("RESULT: %s suite rc=%d (%s); changed: %s" % (mid, rc, "survived" if rc == 0 else "caught",
                                                               json.dumps(changed)))
            verdict = "FAIL" if rc == 0 and changed else "PASS"
            print("%s: %s %s" % (verdict, mid, "survives the suite yet changes exposure.json" if verdict == "FAIL"
                                 else "is caught by the suite or is equivalent on these inputs"))


if __name__ == "__main__":
    main()
