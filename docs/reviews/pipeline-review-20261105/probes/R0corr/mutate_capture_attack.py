"""mutate_capture: apply each mutant to a temp copy of tools/correction-capture (with den-core and den_lighting next to
it, and a .git marker so the inside-checkout test still sees a checkout), run the correction-capture suite with
failfast, and print a caught/survived table. The pinned checkout is never modified.

Synthetic data only (the suite's own), temp dirs only, never contacts a host.
Run:  python3 mutate_capture.py <root of the claude/correction-capture checkout> [work dir]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="mut-capture-"))
CAP = "tools/correction-capture/capture.py"

MUTANTS = [
    ("decide: beyond_budget at >= budget", "if every_high_hours is None or every_high_hours > budget:",
     "if every_high_hours is None or every_high_hours >= budget:"),
    ("verdict: people's upper end instead of lower", "hours_per_arm(garwood(people, level)[0] / occupied_h",
     "hours_per_arm(garwood(people, level)[1] / occupied_h"),
    ("phi_hat: divide by n instead of n - p", "return (_r(pearson / (len(used) - len(strata))),",
     "return (_r(pearson / len(used)),"),
    ("phi_hat: keep strata with no corrections",
     'used = [s for s in per_session if s["occupied_h"] > 0 and totals[s["mode_stratum"]][1] > 0]',
     'used = [s for s in per_session if s["occupied_h"] > 0]'),
    ("ACK_AFTER_S 8 -> 30", "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 8.0", "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 30.0"),
    ("WINDOW_S 5 -> 1", "WINDOW_S = 5.0", "WINDOW_S = 1.0"),
    ("_in_charge ignores occupancy", 'return s["automatic"] and s["occupancy"] == "on"', 'return s["automatic"]'),
    ("walk: count exposure across restarts", "            if restart:\n                lost += t - prev_t",
     "            if False:\n                lost += t - prev_t"),
    ("_rate: printed interval at 50%", 'lo, hi = garwood(count, constants["interval_level"])',
     "lo, hi = garwood(count, 0.5)"),
    ("min_occupied_h: < -> <= in verdict", 'def verdict(occupied_h, people, every, constants):\n'
     '    """cph-budget-v0 for one stratum, from its occupied hours and the two action counts."""\n'
     '    if occupied_h < constants["min_occupied_h"]:',
     'def verdict(occupied_h, people, every, constants):\n'
     '    """cph-budget-v0 for one stratum, from its occupied hours and the two action counts."""\n'
     '    if occupied_h <= constants["min_occupied_h"]:'),
    ("hours_per_arm_at_phi_hat: * phi -> / phi", 'people_rate["hours_per_arm"] * phi)',
     'people_rate["hours_per_arm"] / phi)'),
    ("_own_fade: drop the flip-against-command exception",
     'return any(not (row["flip"] and row["on"] != target["on"]) for _, target in near)', "return bool(near)"),
]


def tree(dest):
    for rel in ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()


def suite(dest):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/correction-capture",
                           "-p", "test_*.py", "-f"], cwd=dest, capture_output=True, text=True, timeout=600)
    first = next((line for line in proc.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))), "")
    return proc.returncode, first


base = WORK / "baseline"
tree(base)
code, _ = suite(base)
print("RESULT baseline suite exit code %d (must be 0)" % code)
rows = []
for i, (name, old, new) in enumerate(MUTANTS, 1):
    dest = WORK / ("m%02d" % i)
    tree(dest)
    path = dest / CAP
    text = path.read_text("utf-8")
    if text.count(old) != 1:
        rows.append((i, name, "not-run", "pattern found %d times" % text.count(old)))
        continue
    path.write_text(text.replace(old, new), "utf-8")
    code, first = suite(dest)
    rows.append((i, name, "caught" if code else "survived", first))
print("%-3s %-52s %-9s %s" % ("#", "mutant", "result", "first failing test"))
for i, name, result, first in rows:
    print("%-3d %-52s %-9s %s" % (i, name, result, first))
print("RESULT %d caught, %d survived, %d not run" % (sum(r[2] == "caught" for r in rows),
                                                      sum(r[2] == "survived" for r in rows),
                                                      sum(r[2] == "not-run" for r in rows)))
