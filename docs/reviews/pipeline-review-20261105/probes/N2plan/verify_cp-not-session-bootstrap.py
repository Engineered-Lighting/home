"""verify_cp-not-session-bootstrap: independent recomputation of the two intervals the finding compares.

What it checks
  PLAN.md 4.6 bounds band competence with a pooled Clopper-Pearson interval; owner-choices item 6 says
  "two-sided 95% intervals from 10,000 session-bootstrap rounds". For 4 held-out sessions with fine
  competence 5/10, 10/10, 10/10, 10/10 and a floor of 0.70 it computes:
    - Clopper-Pearson 95% two-sided for 35/40 (bisection on the binomial tail, stdlib only);
    - the exact session-bootstrap distribution (multinomial over 4 sessions) and a seeded
      10,000-round percentile interval;
  and applies PLAN.md's met / failed / straddled rule to both. Also a reverse case (sessions alike)
  where the bootstrap is the narrower one, to show neither method dominates.

Run
  python3 -B verify_cp-not-session-bootstrap.py <root of claude/plan-n2 checkout>
Synthetic numbers only; no network; no files written.
"""
import math
import random
import re
import sys
from pathlib import Path


def binom_tail_ge(x, n, p):
    return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(x, n + 1))


def cp(x, n, alpha=0.05):
    def solve(f, target):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if f(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    lower = 0.0 if x == 0 else solve(lambda p: binom_tail_ge(x, n, p), alpha / 2)
    # upper U: P(X <= x | U) = alpha/2, i.e. P(X >= x+1 | U) = 1 - alpha/2 (increasing in p)
    upper = 1.0 if x == n else solve(lambda p: binom_tail_ge(x + 1, n, p), 1 - alpha / 2)
    return lower, upper


def boot(sessions, rounds=10000, seed=1):
    rng = random.Random(seed)
    vals = []
    for _ in range(rounds):
        pick = [rng.choice(sessions) for _ in sessions]
        vals.append(sum(r for r, _ in pick) / sum(n for _, n in pick))
    vals.sort()
    return vals[int(0.025 * rounds)], vals[int(0.975 * rounds) - 1]


def status(lo, hi, floor):
    return "met" if lo >= floor else ("failed" if hi < floor else "straddled")


def main(root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    for phrase in ("**Exact binomial** (Clopper–Pearson) for per-unit proportions (band competence, case criteria)",
                   "A floor is **met** when the lower bound is at or above it"):
        print(("PASS" if phrase in plan else "FAIL") + f": PLAN.md text present: {phrase[:70]!r}")
    floor = 0.70
    for label, sessions in (("finding case", [(5, 10), (10, 10), (10, 10), (10, 10)]),
                            ("sessions alike", [(7, 10), (8, 10), (8, 10), (7, 10)])):
        x, n = sum(r for r, _ in sessions), sum(k for _, k in sessions)
        lo, hi = cp(x, n)
        blo, bhi = boot(sessions)
        print(f"RESULT {label}: {x}/{n}; Clopper-Pearson [{lo:.4f}, {hi:.4f}] -> {status(lo, hi, floor)}; "
              f"session bootstrap 10,000 [{blo:.4f}, {bhi:.4f}] -> {status(blo, bhi, floor)} (floor {floor})")
    # exact distribution for the finding case
    probs = {}
    for k in range(5):
        probs[1 - k / 8] = math.comb(4, k) * 0.25 ** k * 0.75 ** (4 - k)
    print("RESULT exact resample distribution (finding case):",
          ", ".join(f"{r:.3f}: {p:.4f}" for r, p in sorted(probs.items())))
    lo, hi = cp(35, 40)
    blo, bhi = boot([(5, 10), (10, 10), (10, 10), (10, 10)])
    if status(lo, hi, floor) != status(blo, bhi, floor):
        print("FAIL: the plan's pooled interval and the owner's session bootstrap give different floor statuses")
    else:
        print("PASS: both interval methods agree")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
