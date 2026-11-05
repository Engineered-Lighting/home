"""R0corr verify (lens reproduce): finding phi-hat-below-one-zero-hours.

What it checks
  capture.py writes planning.strata.<s>.hours_per_arm_at_phi_hat = people hours_per_arm * phi_hat with no floor at 1.
  The rule file (cph-budget-v0.json notes) reasons that phi = 1 is the optimistic case ("phi above 1 only raises the
  hours"). This probe runs the unmodified tool on two synthetic exports:
    A. three no_game evenings of 3 occupied hours with 2, 3, 2 switch corrections (ordinary Poisson-looking counts);
    B. the reporter's case: 2 h / 1, 4 h / 2, 6 h / 3 (counts exactly proportional to hours).
  It prints phi_hat, the phi = 1 hours per arm, and hours_per_arm_at_phi_hat, and FAILs when the latter is below the
  phi = 1 value. It also gives the chance that a Pearson estimate on 2 df falls below 1 for truly Poisson counts.

How to run (stdlib only, synthetic data only, temp dirs only, no host contacted):
    python3 -B verify_phi-hat-below-one-zero-hours.py /path/to/branch/checkout
"""
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import test_capture as tc  # noqa: E402  (synthetic Den fixture from the suite)


def export(evenings):
    den = tc.Den(tc.T0 - 600, occupancy="off")
    for day, (hours, k) in enumerate(evenings):
        t = tc.T0 + day * 86400
        den.row(t, occupancy="on")
        for i in range(k):
            tt = t + 600 + i * 1800
            den.bulb(tt, tc.events.LIGHTS[i], False)
            den.hold(tt + 0.01, {tc.events.LIGHTS[i]: tc.OFF})
        den.row(t + hours * 3600, occupancy="off", holds={})
    den.row(t + hours * 3600 + 600)
    return den


def main():
    bad = False
    for name, evenings in (("A", [(3, 2), (3, 3), (3, 2)]), ("B", [(2, 1), (4, 2), (6, 3)])):
        with tempfile.TemporaryDirectory(prefix="r0corr-phi-") as tmp:
            record, _ = capture.capture(*export(evenings).write(tmp))
        ng = record["planning"]["strata"]["no_game"]
        sessions = [(s["occupied_h"], s["corrections"]) for s in record["exposure"]["per_session"]]
        at_phi = ng["hours_per_arm_at_phi_hat"]
        print("RESULT: case %s per_session %s phi_hat %s hours_per_arm(phi 1) %s hours_per_arm_at_phi_hat %s verdict %s"
              % (name, sessions, record["exposure"]["phi_hat"], ng["people"]["hours_per_arm"], at_phi, ng["verdict"]))
        bad |= at_phi is not None and at_phi < ng["people"]["hours_per_arm"]
    # Pearson X2 / df with df = 2 is chi2_2 / 2 under a true Poisson model: P(< 1) = 1 - exp(-1).
    print("RESULT: P(phi_hat < 1 | Poisson counts, 3 blocks, 1 stratum) ~= %.2f" % (1 - math.exp(-1)))
    print("%s: hours_per_arm_at_phi_hat %s below the phi = 1 planning hours" % (
        "FAIL" if bad else "PASS", "falls" if bad else "never falls"))


if __name__ == "__main__":
    main()
