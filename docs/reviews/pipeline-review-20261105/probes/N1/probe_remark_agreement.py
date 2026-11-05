#!/usr/bin/env python3
"""probe_remark_agreement: recompute the self re-mark agreement independently and compare with n1.agreement.

PROTOCOL-V1 "Re-mark": two intervals match if their overlap in t_s is at least half of each one's
length, paired greedily by the largest overlap; report matches, band and case agreement over
matches, and onset/offset |difference| median and p90 only over ends both passes marked sure and
placed by hand (onsets touch/no_touch, offsets release/no_touch); carried and unsure ends are
counted separately.

This probe re-implements that from the protocol text (its own overlap, greedy pairing, proportions,
and a linear-interpolation percentile written here), runs 3 hand-made cases with values worked out
by hand in the comments, then 3000 random cases, and prints any mismatch with n1.agreement.
Synthetic numbers only; no takes, no network.

Run:
    python3 probe_remark_agreement.py /path/to/den-label-checkout
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import n1  # noqa: E402

PLACED = {"onset": ("touch", "no_touch"), "offset": ("release", "no_touch")}


def iv(on, off, band="fine", case="sorting_mail", ok="touch", fk="release", so=True, sf=True, sb=True):
    return {"onset_t_s": on, "offset_t_s": off, "onset_kind": ok, "offset_kind": fk, "band": band,
            "case": case, "sure": {"onset": so, "offset": sf, "band": sb}}


def lin_pct(values, p):
    v = sorted(values)
    pos = (len(v) - 1) * p / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def mine(first, second):
    cands = []
    for i, a in enumerate(first):
        for j, b in enumerate(second):
            ov = min(a["offset_t_s"], b["offset_t_s"]) - max(a["onset_t_s"], b["onset_t_s"])
            la, lb = a["offset_t_s"] - a["onset_t_s"], b["offset_t_s"] - b["onset_t_s"]
            if ov > 0 and 2 * ov >= la and 2 * ov >= lb:
                cands.append((ov, i, j))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    ui, uj, pairs = set(), set(), []
    for ov, i, j in cands:
        if i in ui or j in uj:
            continue
        ui.add(i)
        uj.add(j)
        pairs.append((first[i], second[j]))
    out = {"matched": len(pairs)}
    out["band"] = round(sum(a["band"] == b["band"] for a, b in pairs) / len(pairs), 6) if pairs else None
    out["case"] = round(sum(a["case"] == b["case"] for a, b in pairs) / len(pairs), 6) if pairs else None
    for end in ("onset", "offset"):
        d, carried, unsure = [], 0, 0
        for a, b in pairs:
            ka, kb = a[f"{end}_kind"], b[f"{end}_kind"]
            if ka == "carried" or kb == "carried":
                carried += 1
            elif a["sure"][end] and b["sure"][end] and ka in PLACED[end] and kb in PLACED[end]:
                d.append(abs(a[f"{end}_t_s"] - b[f"{end}_t_s"]))
            else:
                unsure += 1
        out[end] = (len(d), round(lin_pct(d, 50), 6) if d else None, round(lin_pct(d, 90), 6) if d else None,
                    carried, unsure)
    return out


def theirs(first, second):
    r = n1.agreement(first, second)
    out = {"matched": r["intervals"]["matched"], "band": r["band_agreement"], "case": r["case_agreement"]}
    for end in ("onset", "offset"):
        e = r[end]
        out[end] = (e["n"], e["median_s"], e["p90_s"], e["carried"], e["unsure"])
    return out


def close(x, y):
    if x is None or y is None:
        return x is y
    if isinstance(x, tuple):
        return len(x) == len(y) and all(close(a, b) for a, b in zip(x, y))
    if isinstance(x, (int, float)):
        return abs(x - y) <= 1e-9
    return x == y


def compare(name, first, second, hand=None):
    a, b = mine(first, second), theirs(first, second)
    ok = all(close(a[k], b[k]) for k in a)
    if hand is not None:
        ok = ok and all(close(hand[k], b[k]) for k in hand)
    print(("PASS " if ok else "FAIL ") + f"{name}: tool={b} independent={a}" + (f" hand={hand}" if hand else ""))
    return ok


def main():
    allok = True
    # Case 1 (hand): first [0,10] fine mail; second [1,10] fine mail -> overlap 9 >= 5 and >= 4.5: match.
    # onset |0-1| = 1.0, offset |10-10| = 0.0 -> medians 1.0 and 0.0, p90 the same (n=1).
    allok &= compare("hand-1", [iv(0, 10)], [iv(1, 10)],
                     {"matched": 1, "band": 1.0, "case": 1.0, "onset": (1, 1.0, 1.0, 0, 0), "offset": (1, 0.0, 0.0, 0, 0)})
    # Case 2 (hand): greedy by largest overlap. first A=[0,10], B=[10.5,20]; second X=[2,12], Y=[11,20].
    # A-X ov 8 (>=5, >=5) ok; B-X ov 1.5 no; B-Y ov 9 (>=4.75, >=4.5) ok; A-Y ov<0. Pairs A-X, B-Y.
    # bands: A fine / X casual (disagree), B fine / Y fine -> 0.5. onsets |0-2|=2, |10.5-11|=0.5 -> median 1.25,
    # p90 = 0.5 + (2-0.5)*0.9 = 1.85. offsets |10-12|=2, |20-20|=0 -> median 1.0, p90 1.8.
    allok &= compare("hand-2", [iv(0, 10), iv(10.5, 20)], [iv(2, 12, band="casual"), iv(11, 20)],
                     {"matched": 2, "band": 0.5, "case": 1.0, "onset": (2, 1.25, 1.85, 0, 0),
                      "offset": (2, 1.0, 1.8, 0, 0)})
    # Case 3 (hand): carried and unsure ends. first [0,10] onset carried; second [0,10] onset touch, offset unsure.
    # One match. onset: carried 1, n 0 -> None. offset: unsure 1, n 0 -> None.
    allok &= compare("hand-3", [iv(0, 10, ok="carried")], [iv(0, 10, sf=False)],
                     {"matched": 1, "onset": (0, None, None, 1, 0), "offset": (0, None, None, 0, 1)})
    # Case 4 (hand): perfect agreement gives median 0.0, not None.
    allok &= compare("hand-4", [iv(5, 15), iv(30, 40)], [iv(5, 15), iv(30, 40)],
                     {"matched": 2, "onset": (2, 0.0, 0.0, 0, 0), "offset": (2, 0.0, 0.0, 0, 0)})
    rnd = random.Random(20261105)
    bad = 0
    bands, cases = ("fine", "casual"), ("sorting_mail", "wiping", "still_reader", "other")
    kinds_on, kinds_off = ("touch", "no_touch", "carried"), ("release", "no_touch", "carried")
    for k in range(3000):
        def gen(n):
            out, t = [], rnd.uniform(0, 5)
            for _ in range(n):
                on = round(t + rnd.uniform(0, 8), 2)
                off = round(on + rnd.uniform(0.25, 30), 2)
                out.append(iv(on, off, rnd.choice(bands), rnd.choice(cases), rnd.choice(kinds_on), rnd.choice(kinds_off),
                              rnd.random() > 0.2, rnd.random() > 0.2, rnd.random() > 0.2))
                t = off + rnd.uniform(0, 10)
            return out
        first, second = gen(rnd.randint(0, 8)), gen(rnd.randint(0, 8))
        a, b = mine(first, second), theirs(first, second)
        if not all(close(a[x], b[x]) for x in a):
            bad += 1
            if bad <= 5:
                print(f"FAIL random case {k}: tool={b} independent={a}")
    print(("PASS " if bad == 0 else "FAIL ") + f"random: {bad} mismatches in 3000 cases")
    print("RESULT", "PASS" if allok and bad == 0 else "FAIL")


if __name__ == "__main__":
    main()
