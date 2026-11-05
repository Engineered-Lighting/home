"""probe_stats_recompute: independent recomputation of every worked number and formula in the N2 plan.

What it checks (all on small constructed cases, standard library only)
  - ECE, 10 equal-width bins on [0, 1], top label, per unit (PLAN 4.4 row "ECE").
  - false-fine as a share of scored non-fine (none + casual) seconds, level in force (4.3, 4.4).
  - fine recall: the plan's per-second form vs a per-episode form with abstain / stale = miss.
  - onset median with misses as +inf (4.2), and max(d_on, 0).
  - session bootstrap: distinct resamples of 4 sessions (4.6 says 35), 10,000 rounds, 95% percentile.
  - worked numbers: check (a) (2 s, 3.25 s, 3 - P/2), check (b) (19..24 s, /0.033, recall bound),
    the onset-gap counts (15/20, 14/20), scored quiet units (5, 6, 22; 7, 8, 30), requests per take,
    GPU-host minutes, requests per 4 sessions, build-size total, and the pacing rule on S = 1.25 s.
Run
  python3 probe_stats_recompute.py <root of claude/plan-n2>
Prints PASS / FAIL / RESULT lines. Synthetic numbers only.
"""
import math
import random
import re
import sys
from itertools import combinations_with_replacement
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from n2_scripts import intervals_and_spans, scored_from  # noqa: E402

INF = math.inf
OK = []


def check(name, got, want, tol=1e-9):
    good = (got == want) if (not isinstance(want, float) or math.isinf(want)) else abs(got - want) <= tol
    OK.append(good)
    print(("PASS" if good else "FAIL") + f": {name}: got {got}, plan/hand {want}")


def ece(units, bins=10):
    """units: [(confidence, correct)] -> sum_b n_b/N |acc_b - conf_b|; conf 1.0 goes to the top bin."""
    groups = {}
    for conf, ok in units:
        b = min(int(conf * bins), bins - 1)
        groups.setdefault(b, []).append((conf, ok))
    n = len(units)
    return sum(len(g) / n * abs(sum(o for _, o in g) / len(g) - sum(c for c, _ in g) / len(g))
               for g in groups.values())


def median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def main(root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))

    # ECE: 6 units. bin [0.9,1.0]: conf .95,.95,.95 correct 1,1,0 -> acc .667 |.667-.95| = .2833 ; weight 3/6
    #                bin [0.6,0.7): conf .65,.65 correct 1,1 -> |1-.65| = .35 ; weight 2/6
    #                bin [0.3,0.4): conf .35 correct 0 -> .35 ; weight 1/6
    units = [(.95, 1), (.95, 1), (.95, 0), (.65, 1), (.65, 1), (.35, 0)]
    hand = 3 / 6 * abs(2 / 3 - .95) + 2 / 6 * .35 + 1 / 6 * .35
    check("ECE 10 bins on 6 constructed units", round(ece(units), 12), round(hand, 12))
    check("ECE formula in PLAN", "Σ_b (n_b/N)·\\|acc_b − conf_b\\|, top label, per unit, 10 equal-width bins on [0, 1]" in plan, True)

    # false-fine: scored seconds (gold band, band of the level in force, fresh?)
    secs = [("none", "fine", True)] * 2 + [("none", "none", True)] * 28 + [("casual", "fine", False)] * 1 \
        + [("casual", "casual", True)] * 29 + [("fine", "fine", True)] * 20
    nonfine = [s for s in secs if s[0] != "fine"]
    ff = sum(1 for s in nonfine if s[1] == "fine") / len(nonfine)
    check("false-fine share of non-fine seconds (3/60, stale counts)", ff, 0.05)
    check("false-fine 0.05 > cap 0.033 -> fails", ff > 0.033, True)

    # fine recall: two fine episodes, 60 s hit (fresh fine) and 10 s missed (stale fine level)
    ep = [[("fine", True)] * 60, [("fine", False)] * 10]
    per_second = sum(1 for e in ep for b, fresh in e if b == "fine" and fresh) / sum(len(e) for e in ep)
    def ep_band(e):
        fresh = [b for b, f in e if f]
        return None if not fresh else sorted(fresh)[len(fresh) // 2]
    per_episode = sum(1 for e in ep if ep_band(e) == "fine") / len(ep)
    print(f"RESULT fine recall on the same data: per second {per_second:.3f} (plan, 4.4), per episode "
          f"{per_episode:.3f} (abstain/stale = miss); a floor of 0.8 passes one and fails the other")

    # onset median with misses
    d = [0.0, 1.2, 2.0, 2.4, INF, INF, 5.0]
    check("onset median with two misses as +inf", median([max(x, 0) for x in d]), 2.4)
    d2 = [1.0, INF, INF]
    check("onset median when most onsets are missed is +inf (fails)", median(d2), INF)

    # session bootstrap
    check("distinct multiset resamples of 4 sessions", len(list(combinations_with_replacement(range(4), 4))), 35)
    check("PLAN says 35 distinct resamples", "only 35 distinct resamples" in plan, True)
    rng = random.Random(1)
    vals = sorted(sum(rng.choice([0.9, 0.8, 0.85, 0.7]) for _ in range(4)) / 4 for _ in range(10000))
    print(f"RESULT session bootstrap 10,000 rounds on [0.9, 0.8, 0.85, 0.7]: 95% [{vals[249]:.4f}, {vals[9749]:.4f}]")

    # check (a)
    check("median of w ~ U[0, 4)", 4 / 2, 2.0)
    check("median d_on with S = 1.25 s whole-room receipt", 2 + 1.25, 3.25)
    check("3 s - P/2 at P = 4", 3 - 4 / 2, 1.0)
    # the plan's own rule on S = 1.25, m = 0: D_dev = 1.25; P/2 + D <= 3 fails at P = 4; largest P that fits
    D = 1.25
    fits4 = 4 / 2 + D <= 3
    largest = max(p / 10 for p in range(5, 41) if p / 10 / 2 + D <= 3)
    print(f"RESULT pacing rule with S = 1.25 s: P = 4 fits? {fits4}; largest P fitting = {largest} s "
          "(the plan would then score a cadence other than the ladder's one request per 4 s)")

    # check (b)
    check("wiping 25 s minus 6 s grace", 25 - 6, 19)
    check("wiping 30 s minus 6 s grace", 30 - 6, 24)
    check("19 / 0.033 ~ 580", round(19 / 0.033, -1), 580.0)
    check("24 / 0.033 ~ 730", round(24 / 0.033, -1), 730.0)
    rec_a = 1 - 24 / (24 + 24)       # hard-a: still_reader 30, mail 30 -> 24 + 24 scored
    rec_b = 1 - 19 / (19 + 29)       # hard-b: mail 25, still_reader 35 -> 19 + 29 scored
    print(f"RESULT fine recall bound with every mail second missed: hard-a {rec_a:.3f}, hard-b {rec_b:.3f} "
          "(plan: 'at most about 0.5 to 0.6')")
    OK.append(0.49 < rec_a < 0.61 and 0.49 < rec_b < 0.61)

    # gap counts and quiet units from N1's tables
    for script, want in (("hard-a", 15), ("hard-b", 14)):
        ivs, _ = intervals_and_spans(script)
        inside = sum(1 for p, q in zip(ivs, ivs[1:]) if q["on"] - p["off"] < 60)
        check(f"{script}: onsets after a gap wholly inside the previous tau_off", inside, want)
    for phone, want_a, want_b in (("casual", 5, 6), ("none", 7, 8)):
        got = []
        for script in ("hard-a", "hard-b"):
            ivs, spans = intervals_and_spans(script, phone)
            got.append(sum(1 for sp in spans if math.ceil(scored_from(sp["start"], ivs)) + 1 <= sp["end"]))
        check(f"scored quiet/no-need units per take, phone {phone} (hard-a, hard-b)", tuple(got), (want_a, want_b))
        check(f"held-out set of 2+2 takes, phone {phone}", 2 * got[0] + 2 * got[1], 2 * want_a + 2 * want_b)

    # requests and time
    check("requests per take at P = 4 s (1,500 s)", 1500 // 4, 375)
    check("requests per take at P = 4 s (1,610 s)", 1610 // 4, 402)
    check("GPU-host minutes at >= 4 s per request (1,500 s)", 1500 / 60, 25.0)
    check("requests for 4 sessions", (4 * 375, 4 * 400), (1500, 1600))
    check("build-size total, option 2 with prompt-2 (lines)", 1300 + 550 + 750 + 260, 2860)
    print("RESULT summary: " + ("all checks PASS" if all(OK) else f"{OK.count(False)} check(s) FAIL"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
