"""R0corr verifier probe for finding tests-miss-restart-and-occupancy-gate.

What it checks: whether the three mutants that survive the suite (M10 restart ignores the unavailable row, M11 restart
on an equal revision, M21 occupancy 'unknown' counted as occupied) are NON-equivalent on inputs real den_lighting
produces, i.e. whether the suite really misses a behaviour change:
  A  rows with an unchanged revision right after a command (den_lighting changes pending_fixtures, which is in its
     status attributes, without bumping policy.revision: __init__.py:468 + notify at :479, and :383 + wake at :398);
  B  a span with occupancy 'unknown' (den_lighting sets it at start-up and on Frigate or broker loss:
     __init__.py:145, :194, :339, :349);
  C  a restart marked only by HA's 'unavailable' placeholder, with the first revision after it not lower
     (den_lighting does not persist revision: policy.dump/restore; it restarts from 0, so this needs a low revision
     before the restart, e.g. a quick second restart).
It copies tools/correction-capture, tools/den-core and the den_lighting component to a temp dir, applies each mutant
to a copy, runs the same synthetic scenarios (built with the suite's own Den fixture) on the original and each mutant,
and prints what changes.

How to run (stdlib only, synthetic data, temp dirs only, no host contacted):
    python3 -B verify_tests-miss-restart-and-occupancy-gate.py /path/to/branch/checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CAP = "tools/correction-capture/capture.py"
MUTANTS = {
    "M10": ('restart = prev is not None and (broken or s["revision"] < prev["revision"])',
            'restart = prev is not None and (s["revision"] < prev["revision"])'),
    "M11": ('s["revision"] < prev["revision"]', 's["revision"] <= prev["revision"]'),
    "M21": ('return s["automatic"] and s["occupancy"] == "on"', 'return s["automatic"] and s["occupancy"] != "off"'),
}

SCENARIOS = r'''
import json, tempfile
import capture
from test_capture import Den, T0, L0, L1, L2, WARM, OFF

def run(den):
    with tempfile.TemporaryDirectory() as d:
        rec, _ = capture.capture(*den.write(d))
    ng = rec["planning"]["strata"]["no_game"]
    return {"restarts": rec["span"]["restarts"], "occupied_h": ng["occupied_h"], "people": ng["people"]["count"],
            "every": ng["every_hold"]["count"], "verdict_inputs_gap_h": rec["planning"]["restart_gap_h"],
            "not_counted": [c["reason"] for c in rec["not_counted"]]}

out = {}
den = Den()
den.bulb(T0 + 100, L0, False); den.hold(T0 + 100.01, {L0: OFF})       # a switch: one person action
den.command(T0 + 200, L1, WARM)                                       # den_lighting's own command
den.row(T0 + 200.05, revision=den.revision)                           # pending_fixtures gains L1, same revision
den.row(T0 + 203.5, revision=den.revision)                            # read-back pops it, same revision
den.bulb(T0 + 300, L2, False); den.hold(T0 + 300.01, {L2: OFF})       # another switch
den.row(T0 + 3600)
out["A_equal_revision_rows"] = run(den)

den = Den()
den.row(T0 + 600, occupancy="unknown")                                # e.g. broker lost
den.bulb(T0 + 700, L0, False); den.hold(T0 + 700.01, {L0: OFF})
den.row(T0 + 1200, occupancy="on")
den.row(T0 + 1800)
out["B_occupancy_unknown"] = run(den)

den = Den()
den.status[0]["attributes"]["revision"] = den.revision = 1            # a fresh start: low revision
den.bulb(T0 + 100, L0, False); den.hold(T0 + 100.01, {L0: OFF})       # revision 2
den.unavailable(T0 + 1000)                                            # HA restarts: the placeholder row
den.row(T0 + 1060, revision=3)                                        # den_lighting back; revision not lower
den.row(T0 + 2860)
out["C_restart_unavailable_only"] = run(den)
print(json.dumps(out))
'''


def copy(src, dst, patch=None):
    for rel in ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting"):
        shutil.copytree(src / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))
    if patch:
        path = dst / CAP
        text = path.read_text("utf-8")
        assert text.count(patch[0]) == 1, patch[0]
        path.write_text(text.replace(patch[0], patch[1]), "utf-8")
    proc = subprocess.run([sys.executable, "-B", "-c", SCENARIOS], cwd=dst / "tools/correction-capture",
                          capture_output=True, text=True, timeout=120)
    if proc.returncode:
        return {"error": proc.stderr.strip().splitlines()[-1]}
    return json.loads(proc.stdout)


def main():
    src = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="r0corr-verify-") as tmp:
        tmp = Path(tmp)
        base = copy(src, tmp / "orig")
        for name, result in base.items():
            print("RESULT original %-28s %s" % (name, result))
        for mid, patch in MUTANTS.items():
            got = copy(src, tmp / mid, patch)
            for name in base:
                same = got.get(name) == base[name]
                print("%s: %s on %-28s %s" % ("RESULT equivalent" if same else "RESULT CHANGED", mid, name,
                                              "" if same else got.get(name, got.get("error"))))


if __name__ == "__main__":
    main()
