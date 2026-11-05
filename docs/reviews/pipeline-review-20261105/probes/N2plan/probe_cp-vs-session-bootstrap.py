"""probe_cp-vs-session-bootstrap: N2 plan bounds band competence (its main burden-of-proof metric)
with a pooled Clopper-Pearson interval that treats units as independent, not the owner's
"two-sided 95% intervals from 10,000 session-bootstrap rounds"; with one weak held-out session the
two disagree on met vs straddled, i.e. feasible vs insufficient.

What it checks
  Constructed held-out set: 4 sessions x 10 fine units, right = 5, 10, 10, 10 (one bad session,
  for example a session where the hands were often out of view). Floor 0.70 (any owner floor in
  roughly 0.63 to 0.73 shows the same split).
  - pooled exact binomial (independent implementation, bisection on the binomial tail);
  - session bootstrap, 10,000 rounds, percentile 2.5 / 97.5, resampling whole sessions;
  - the plan's met / failed / straddled rule (section 4.6) on each.

Run
  python3 probe_cp-vs-session-bootstrap.py <root of claude/plan-n2>
Synthetic numbers only.
"""
import math
import random
import re
import sys
from pathlib import Path


def binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(k, n, level=0.95):
    a = (1 - level) / 2

    def bisect(f):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    lower = 0.0 if k == 0 else bisect(lambda p: 1 - binom_cdf(k - 1, n, p) < a)
    upper = 1.0 if k == n else bisect(lambda p: binom_cdf(k, n, p) > a)
    return lower, upper


def percentile(sorted_vals, q):
    pos = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def session_bootstrap(sessions, rounds=10000, seed=20261105):
    rng = random.Random(seed)
    vals = []
    for _ in range(rounds):
        draw = [sessions[rng.randrange(len(sessions))] for _ in sessions]
        vals.append(sum(r for r, n in draw) / sum(n for r, n in draw))
    vals.sort()
    return percentile(vals, 0.025), percentile(vals, 0.975)


def judge_floor(floor, lo, hi):
    return "met" if lo >= floor else "failed" if hi < floor else "straddled"


def main(root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    for phrase in ("**Exact binomial** (Clopper–Pearson) for per-unit proportions (band competence, case criteria), "
                   "with units pooled across held-out sessions and treated as independent",
                   "A floor is **met** when the lower bound is at or above it"):
        print(("PASS" if phrase in plan else "FAIL") + f": PLAN.md says {phrase[:90]!r}...")
    sessions = [(5, 10), (10, 10), (10, 10), (10, 10)]
    k, n = sum(r for r, _ in sessions), sum(m for _, m in sessions)
    cp = clopper_pearson(k, n)
    bs = session_bootstrap(sessions)
    floor = 0.70
    print(f"RESULT pooled competence {k}/{n} = {k / n:.4f}")
    print(f"RESULT Clopper-Pearson 95% [{cp[0]:.4f}, {cp[1]:.4f}] -> {judge_floor(floor, *cp)} at floor {floor}")
    print(f"RESULT session bootstrap 10,000 rounds 95% [{bs[0]:.4f}, {bs[1]:.4f}] -> "
          f"{judge_floor(floor, *bs)} at floor {floor}")
    # exact bootstrap distribution: number of bad-session draws ~ Binomial(4, 1/4)
    probs = {j: math.comb(4, j) * 0.25 ** j * 0.75 ** (4 - j) for j in range(5)}
    print("RESULT exact resample distribution: " + ", ".join(
        f"rate {1 - j / 8:.3f} p={probs[j]:.4f}" for j in range(5)))
    if judge_floor(floor, *cp) == "met" and judge_floor(floor, *bs) != "met":
        print("FAIL: the plan's interval says met (feasible if all else is met) where the owner's session-bootstrap "
              "interval says straddled (insufficient)")
    else:
        print("PASS: both interval methods agree here")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
