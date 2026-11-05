#!/usr/bin/env python3
"""Independent recompute of N1's delayed self re-mark agreement figures.

What it checks: n1.agreement(first, second) against this file's own
implementation of PROTOCOL-V1's re-mark rule:
  - two intervals match if their overlap in t_s is at least half of EACH one's
    length (and positive), paired greedily by the largest overlap, one to one;
  - band and case agreement are proportions over matched pairs;
  - onset/offset differences: |t1 - t2| over matched pairs whose end both passes
    marked sure and placed by hand (onset touch/no_touch, offset release/no_touch);
    carried ends and unsure ends are counted separately;
  - median and p90 by linear interpolation (also reported: nearest-rank, to show
    which convention the tool uses).
It runs (a) hand-made discriminating cases that the den-label test does not
cover (a containment pair under half of the longer interval, a 0.3-overlap
pair, a pair where index-order pairing differs from largest-overlap pairing, a
p90 that differs from p95) and (b) 3,000 random cases.

Run:  python3 -B probe_remark_agreement_v2.py <den-label branch root>
Synthetic data only; no files written.
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import n1  # noqa: E402

fails = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        fails.append(what)


def lin_pct(values, p):
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * p / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def nearest_rank(values, p):
    v = sorted(values)
    if not v:
        return None
    return v[max(0, math.ceil(p / 100.0 * len(v)) - 1)]


def mine(first, second):
    cands = []
    for i, a in enumerate(first):
        la = a["offset_t_s"] - a["onset_t_s"]
        for j, b in enumerate(second):
            lb = b["offset_t_s"] - b["onset_t_s"]
            ov = min(a["offset_t_s"], b["offset_t_s"]) - max(a["onset_t_s"], b["onset_t_s"])
            if ov > 0 and ov >= la / 2 and ov >= lb / 2:
                cands.append((ov, i, j))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    ui, uj, pairs = set(), set(), []
    for ov, i, j in cands:
        if i in ui or j in uj:
            continue
        ui.add(i)
        uj.add(j)
        pairs.append((first[i], second[j]))
    out = {"matched": len(pairs),
           "band": None if not pairs else sum(a["band"] == b["band"] for a, b in pairs) / len(pairs),
           "case": None if not pairs else sum(a["case"] == b["case"] for a, b in pairs) / len(pairs)}
    for end, placed in (("onset", {"touch", "no_touch"}), ("offset", {"release", "no_touch"})):
        d, carried, unsure = [], 0, 0
        for a, b in pairs:
            ka, kb = a[end + "_kind"], b[end + "_kind"]
            if "carried" in (ka, kb):
                carried += 1
            elif a["sure"][end] and b["sure"][end] and ka in placed and kb in placed:
                d.append(abs(a[end + "_t_s"] - b[end + "_t_s"]))
            else:
                unsure += 1
        out[end] = (len(d), lin_pct(d, 50), lin_pct(d, 90), carried, unsure, nearest_rank(d, 50), nearest_rank(d, 90))
    return out


def close(x, y):
    if x is None or y is None:
        return x is None and y is None
    return abs(x - y) <= 1e-6


def compare(first, second, label):
    t = n1.agreement(first, second)
    m = mine(first, second)
    ok = (t["intervals"]["matched"] == m["matched"] and close(t["band_agreement"], m["band"])
          and close(t["case_agreement"], m["case"]))
    for end in ("onset", "offset"):
        n, med, p90, carried, unsure, _, _ = m[end]
        ok = ok and (t[end]["n"], t[end]["carried"], t[end]["unsure"]) == (n, carried, unsure) \
            and close(t[end]["median_s"], med) and close(t[end]["p90_s"], p90)
    return ok, t, m


def iv(on, off, band="fine", case="sorting_mail", kinds=("touch", "release"), sure=(True, True, True)):
    return {"onset_t_s": on, "offset_t_s": off, "onset_kind": kinds[0], "offset_kind": kinds[1], "band": band,
            "case": case, "sure": dict(zip(("onset", "offset", "band"), sure))}


def main():
    # (a) hand-made discriminating cases
    cases = {
        # second is inside first; overlap 6 is 60% of 10 but only 30% of 20 -> no match by the protocol
        "containment under half of the longer": ([iv(10, 30)], [iv(12, 18)], 0),
        # overlap 3 of 10 (0.3) both sides -> no match
        "overlap 0.3 of each": ([iv(0, 10)], [iv(7, 17)], 0),
        # largest-overlap greedy vs index order: first[0] overlaps second[0] by 5 (of 8 and 9), second[1] by 7
        "greedy by largest overlap": ([iv(0, 8), iv(9, 30, "casual", "wiping")],
                                      [iv(3, 12), iv(1, 8, "casual", "wiping")], 1),
    }
    for name, (a, b, expect) in cases.items():
        ok, t, m = compare(a, b, name)
        check(ok, f"hand case '{name}': tool matches my recompute (tool matched {t['intervals']['matched']}, "
                  f"band {t['band_agreement']}; mine matched {m['matched']}, band {m['band']})")
    # p90 of 10 differences 0.1..1.0 by 0.1: linear p90 = 0.91; nearest-rank p90 = 0.9; p95 = 0.955
    first = [iv(10 * k, 10 * k + 5) for k in range(10)]
    second = [iv(10 * k + 0.1 * (k + 1), 10 * k + 5) for k in range(10)]
    ok, t, m = compare(first, second, "p90")
    print(f"RESULT p90 case: tool onset median {t['onset']['median_s']} p90 {t['onset']['p90_s']}; "
          f"mine linear median {m['onset'][1]:.6f} p90 {m['onset'][2]:.6f}; nearest-rank median {m['onset'][5]} "
          f"p90 {m['onset'][6]}")
    check(ok, "p90 case: tool equals my linear-interpolation recompute")
    # (b) random cases
    rng = random.Random(20261105)
    bad = 0
    for k in range(3000):
        def rand_set():
            out, t = [], rng.uniform(0, 5)
            for _ in range(rng.randint(0, 8)):
                t += rng.uniform(0.5, 30)
                length = rng.uniform(0.3, 40)
                ko = rng.choice(["touch", "no_touch", "carried"])
                kf = rng.choice(["release", "no_touch", "carried"])
                out.append(iv(round(t, 3), round(t + length, 3), rng.choice(["fine", "casual"]),
                              rng.choice(["sorting_mail", "wiping", "still_reader"]), (ko, kf),
                              (rng.random() > 0.2, rng.random() > 0.2, rng.random() > 0.2)))
                t += length
            return out
        a = rand_set()
        b = [dict(x, onset_t_s=round(x["onset_t_s"] + rng.gauss(0, 2), 3),
                  offset_t_s=round(x["offset_t_s"] + rng.gauss(0, 2), 3)) for x in a if rng.random() > 0.15]
        b = [x for x in b if x["offset_t_s"] > x["onset_t_s"]]
        ok, t, m = compare(a, b, f"random {k}")
        if not ok:
            bad += 1
            if bad <= 3:
                print(f"RESULT mismatch on random case {k}: tool {t} mine {m}")
    check(bad == 0, f"3000 random cases: tool equals my recompute ({bad} mismatches)")
    print("RESULT", "FAIL" if fails else "PASS", f"({len(fails)} failed checks)")


if __name__ == "__main__":
    main()
