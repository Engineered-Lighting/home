"""R0corr verifier probe for findings tests-miss-phi-and-session-numerator and tests-miss-settle-and-window-bounds.

What it checks: whether the mutants that survive the suite are NON-equivalent on realistic inputs.
  M06 phi without the log(occupied_h) offset, M07 phi with one global df, M09 per_session.corrections = every hold
  (finding tests-miss-phi-and-session-numerator), and M14 settle '<' instead of '<=', M22 WINDOW_S 3 s instead of 5 s
  (finding tests-miss-settle-and-window-bounds).
Scenarios: phi_hat on blocks of unequal hours in one stratum, and on two strata that both have corrections, checked
against an independent hand computation of the quasi-Poisson Pearson dispersion (offset log h, one rate per stratum,
df = blocks - strata); the suite's own evening() fixture for per_session corrections (2 people, 4 every-hold);
an app hold whose bulb row lands 4 s after it; two switch holds exactly 3.0 s apart.
It copies the tool, den-core and den_lighting to a temp dir, applies each mutant to a copy, and prints what changes.

How to run (stdlib only, synthetic data, temp dirs only, no host contacted):
    python3 -B verify_tests-miss-phi-and-session-numerator.py /path/to/branch/checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CAP = "tools/correction-capture/capture.py"
MUTANTS = {
    "M06": ('mu = s["occupied_h"] * c / h', "mu = c / h"),
    "M07": ("_r(pearson / (len(used) - len(strata)))", "_r(pearson / (len(used) - 1))"),
    "M09": ('"corrections": sessions.get((block, stratum), (0, 0))[0]}',
            '"corrections": sessions.get((block, stratum), (0, 0))[1]}'),
    "M14": ('c["t"] - actions[-1][-1]["t"] <= SETTLE_S', 'c["t"] - actions[-1][-1]["t"] < SETTLE_S'),
    "M22": ("WINDOW_S = 5.0", "WINDOW_S = 3.0"),
}

BLOCKS_ONE = [(1.0, 1), (4.0, 2), (2.0, 3), (5.0, 2)]
BLOCKS_GAME = [(1.0, 0), (2.0, 3)]

SCENARIOS = r'''
import json, tempfile
import capture
from test_capture import Den, T0, L0, L1, evening, WARM, OFF
ONE, GAME = %r, %r

def run(den):
    with tempfile.TemporaryDirectory() as d:
        rec, _ = capture.capture(*den.write(d))
    return rec

out = {}
one = [{"occupied_h": h, "mode_stratum": "no_game", "corrections": c} for h, c in ONE]
two = one + [{"occupied_h": h, "mode_stratum": "game", "corrections": c} for h, c in GAME]
out["phi_unequal_hours"] = capture.phi_hat(one)[0]
out["phi_two_strata"] = capture.phi_hat(two)[0]
rec = run(evening())
out["evening_per_session_corrections"] = [s["corrections"] for s in rec["exposure"]["per_session"]]
out["evening_corrections_total"] = rec["exposure"]["corrections_total"]
den = Den()
den.hold(T0 + 200, {L1: WARM}); den.bulb(T0 + 204, L1, True, user=True); den.row(T0 + 3600)
rec = run(den)
out["app_row_4s_after_hold"] = [(c["source"], c["people"]) for c in rec["corrections"]]
den = Den()
den.bulb(T0 + 100, L0, False); den.hold(T0 + 100.0, {L0: OFF})
den.bulb(T0 + 103, L1, False); den.hold(T0 + 103.0, {L1: OFF}); den.row(T0 + 3600)
rec = run(den)
out["switch_holds_exactly_3s_apart"] = [c["action"]["n_changes"] for c in rec["corrections"]]
print(json.dumps(out))
''' % (BLOCKS_ONE, BLOCKS_GAME)


def hand_phi(groups):
    """Independent quasi-Poisson Pearson dispersion: offset log(h), one rate per group, df = n - groups."""
    pearson, n, k = 0.0, 0, 0
    for blocks in groups:
        h, c = sum(b[0] for b in blocks), sum(b[1] for b in blocks)
        if c == 0:
            continue
        k += 1
        for hours, count in blocks:
            mu = hours * c / h
            pearson += (count - mu) ** 2 / mu
            n += 1
    return round(pearson / (n - k), 4)


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
    print("RESULT hand phi, one stratum unequal hours: %s; two strata: %s" % (
        hand_phi([BLOCKS_ONE]), hand_phi([BLOCKS_ONE, BLOCKS_GAME])))
    with tempfile.TemporaryDirectory(prefix="r0corr-verify-") as tmp:
        tmp = Path(tmp)
        base = copy(src, tmp / "orig")
        for name, result in base.items():
            print("RESULT original %-34s %s" % (name, result))
        print("%s: original phi matches the hand computation" % (
            "PASS" if (base["phi_unequal_hours"], base["phi_two_strata"])
            == (hand_phi([BLOCKS_ONE]), hand_phi([BLOCKS_ONE, BLOCKS_GAME])) else "FAIL"))
        for mid, patch in MUTANTS.items():
            got = copy(src, tmp / mid, patch)
            for name in base:
                if got.get(name) != base[name]:
                    print("RESULT CHANGED: %s on %-34s %s" % (mid, name, got.get(name, got.get("error"))))


if __name__ == "__main__":
    main()
