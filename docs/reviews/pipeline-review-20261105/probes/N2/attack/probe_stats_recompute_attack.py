"""Probe (attack lens): recompute N2's key statistics independently on a tiny synthetic case and compare.

Three in-memory sessions (two on day 0, one on day 1), one 320 s segment each, with gold
fine/casual/fine intervals and two quiet spans; hand-made person_state records:
  s1 follows the gold (starting 1 s after each touch, fresh level of the gold band);
  s2 reads the casual task as fine (false-fine and a wrong casual unit) and starts 4 s late;
  s3 lets the second fine task's level go stale after 10 s (fine-recall misses) and turns on in the last
     quiet span (false_on).
Recomputed here without score.py's helpers: the per-second grid and windows, false-fine share of non-fine
seconds, fine recall (abstain/stale = miss), ECE over 10 equal-width bins (both fields), onset median
(misses = +inf), the session bootstrap (resampling unit = session, 10,000 rounds, two-sided 95%,
percentile type 7, seeded from the labels hash as GATE-V1 says), Clopper-Pearson by exact bisection,
and the sample-gate counts by band, session and day.

Run:  python3 -B probe_stats_recompute_attack.py <root of the den-n2 checkout>
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import score  # noqa: E402

GATE = score.load_gate()
SEED = "7f" * 32
BASE = 1_793_016_000.0
SEG = (0.0, 320.0)
IVS = [(20.0, 50.0, "fine", "sorting_mail"), (120.0, 150.0, "casual", "wiping"), (200.0, 230.0, "fine", "still_reader")]
QUIET = [(0.0, 15.0, "idle_seated"), (291.0, 318.0, "idle_standing")]
LEVEL = {"fine": 0.8, "casual": 0.5, "none": 0.2}


def records(kind):
    """(t, state, eng_conf, level, dem_conf, cap) records on the take clock."""
    out = [(0.5, "none", 0.7, 0.2, 0.6, 0.0)]
    for i, (on, off, band, _) in enumerate(IVS):
        delay = 4.0 if kind == "s2" else 1.0
        lvl = LEVEL["fine"] if (kind == "s2" and band == "casual") else LEVEL[band]
        stale = kind == "s3" and i == 2
        t = on + delay
        while t < off + 1:
            state = "starting" if t < on + delay + 5 else "engaged"
            cap = (on + delay - 1) if stale else t - 1
            out.append((t, state, 0.9, lvl, 0.85 if band == "fine" else 0.75, cap))
            t += 2.0
        out.append((off + 1, "ending", 0.6, 0.2, 0.6, off))
        out.append((off + 21, "none", 0.8, 0.2, 0.7, off + 20))
        t = off + 23
        nxt = IVS[i + 1][0] if i + 1 < len(IVS) else SEG[1]
        while t < nxt:
            out.append((t, "none", 0.8, 0.2, 0.7, t - 1))
            t += 2.0
    if kind == "s3":
        out = [r for r in out if r[0] < 300] + [(300.0, "starting", 0.55, 0.2, 0.7, 299.0),
                                                 (302.0, "starting", 0.55, 0.2, 0.7, 301.0)]
    return sorted(out)


def make_session(take, day, kind):
    s = score.Session(take, "held_out", BASE + day * 86400, f"day{day}", [SEG],
                      [{"on": on, "off": off, "band": band, "case": case, "seg": 0, "onset_kind": "touch",
                        "offset_kind": "release", "sure": {"onset": True, "offset": True, "band": True}}
                       for on, off, band, case in IVS],
                      [{"kind": "quiet", "case": c, "start": a, "end": b, "seg": 0} for a, b, c in QUIET])
    s.logs = [[score.Rec(t, st, ec, lv, dc, cap) for t, st, ec, lv, dc, cap in records(kind)]]
    return s


def in_force(recs, x):
    best = None
    for r in recs:
        if r[0] <= x:
            best = r
    return best


def band(level):
    if level is None:
        return None
    return "fine" if level >= 0.6 else "casual" if level >= 0.4 else "none"


def per_session(kind):
    recs = records(kind)
    offs = [off for _, off, _, _ in IVS]
    ff, fr, fo = [0, 0], [0, 0], [0, 0]
    per_iv = {i: [] for i in range(len(IVS))}
    per_q = {j: [] for j in range(len(QUIET))}
    for k in range(math.ceil(SEG[0]), math.floor(SEG[1])):
        x = k + 0.5
        r = in_force(recs, x)
        on = r is not None and r[1] in ("starting", "engaged")
        lvl = r[3] if r else None
        fresh = lvl is not None and x < r[5] + 10
        hit = [i for i, (a, b, _, _) in enumerate(IVS) if a <= x <= b]
        sec = dict(x=x, on=on, lvl=lvl, fresh=fresh, ec=r[2] if r else None, dc=r[4] if r else None)
        if hit:
            i = hit[0]
            a, b, gb, _ = IVS[i]
            sec.update(w_on=x < a + 3, grace=x < a + 6)
            per_iv[i].append(sec)
            if not sec["grace"]:
                if gb == "fine":
                    fr[1] += 1
                    fr[0] += fresh and band(lvl) == "fine"
                else:
                    ff[1] += 1
                    ff[0] += band(lvl) == "fine"
        else:
            if any(o < x < o + 60 for o in offs):
                continue
            ff[1] += 1
            ff[0] += band(lvl) == "fine"
            q = [j for j, (a, b, _) in enumerate(QUIET) if a <= x <= b]
            if q:
                fo[1] += 1
                fo[0] += on
                per_q[q[0]].append(sec)

    def unit(eng, dem, gold_band, gold_on):
        n_on = sum(1 for s in eng if s["on"])
        label_on = n_on > len(eng) - n_on
        ec = [s["ec"] for s in eng if s["on"] == label_on and s["ec"] is not None]
        fresh = sorted(s["lvl"] for s in dem if s["fresh"])
        ub = None
        if fresh:
            m = len(fresh)
            med = fresh[m // 2] if m % 2 else (fresh[m // 2 - 1] + fresh[m // 2]) / 2
            ub = band(med)
        dc = [s["dc"] for s in dem if s["fresh"] and band(s["lvl"]) == ub and s["dc"] is not None]
        return (gold_band, gold_on, label_on, sum(ec) / len(ec) if ec else None, ub,
                sum(dc) / len(dc) if (dc and ub) else None)
    units = [unit([s for s in secs if not s["w_on"]], [s for s in secs if not s["grace"]], IVS[i][2], True)
             for i, secs in per_iv.items()]
    units += [unit(secs, secs, "none", False) for secs in per_q.values() if secs]
    onsets, prev = [], None
    for on_t, off_t, _, _ in IVS:
        r = in_force(recs, on_t)
        if r and r[1] in ("starting", "engaged"):
            idx = recs.index(r)
            while idx > 0 and recs[idx - 1][1] in ("starting", "engaged"):
                idx -= 1
            d = 0.0 if (prev is None or recs[idx][0] > prev) else math.inf
        else:
            later = [q for q in recs if on_t < q[0] < off_t and q[1] in ("starting", "engaged")]
            d = later[0][0] - on_t if later else math.inf
        onsets.append(max(d, 0.0))
        prev = off_t
    return dict(ff=ff, fr=fr, fo=fo, units=units, onsets=onsets)


def ece(units, field):
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    for gb, go, lo, ec, ub, dc in units:
        conf, acc = (ec, lo == go) if field == "engagement" else (dc, ub == gb)
        if conf is None or (field == "demand" and ub is None):
            continue
        b = bins[min(int(math.floor(conf * 10)), 9)]
        b[0] += 1
        b[1] += conf
        b[2] += acc
    n = sum(b[0] for b in bins)
    return sum(abs(b[2] - b[1]) for b in bins) / n if n else None


def type7(vals, q):
    h = (len(vals) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(vals) - 1)
    a, b = vals[lo], vals[hi]
    if a == b or h == lo:
        return a
    if math.isinf(b):
        return math.inf
    return a + (h - lo) * (b - a)


def metric(parts, name):
    if name in ("ff", "fr", "fo"):
        num, den = sum(p[name][0] for p in parts), sum(p[name][1] for p in parts)
        return num / den if den else None
    if name.startswith("ece."):
        return ece([u for p in parts for u in p["units"]], name[4:])
    vals = sorted(v for p in parts for v in p["onsets"])
    if not vals:
        return None
    m = len(vals)
    if m % 2:
        return vals[m // 2]
    a, b = vals[m // 2 - 1], vals[m // 2]
    return math.inf if math.isinf(b) else (a + b) / 2


def cp(k, n, level=0.95):
    def cdf(kk, p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(kk + 1))
    t = (1 - level) / 2

    def bis(f):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2
    return (0.0 if k == 0 else bis(lambda p: 1 - cdf(k - 1, p) < t)), (1.0 if k == n else bis(lambda p: cdf(k, p) > t))


def main():
    kinds = [("t1", 0, "s1"), ("t2", 0, "s2"), ("t3", 1, "s3")]
    analyses = [score.analyse(make_session(*k), GATE, 4) for k in kinds]
    metrics, stats = score.score_metrics(analyses, GATE, SEED)
    mine = [per_session(k[2]) for k in kinds]
    rng = random.Random(int(SEED[:16], 16))
    draws = [[rng.randrange(3) for _ in range(3)] for _ in range(10000)]
    ok = True
    for tool_name, my_name in {"false_fine": "ff", "fine_recall": "fr", "false_on": "fo", "ece.demand": "ece.demand",
                               "ece.engagement": "ece.engagement", "onset": "onset"}.items():
        tv = metrics[tool_name]["value"]
        hv = metric(mine, my_name)
        hv_r = None if hv is None else ("inf" if math.isinf(hv) else round(hv, 6))
        vals = sorted(v for v in (metric([mine[i] for i in d], my_name) for d in draws) if v is not None)
        iv = [("inf" if math.isinf(v) else round(v, 6)) for v in (type7(vals, 0.025), type7(vals, 0.975))]
        good = tv == hv_r and metrics[tool_name]["interval"] == iv
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} {tool_name}: tool={tv} {metrics[tool_name]['interval']} hand={hv_r} {iv}")
    for b in ("fine", "casual"):
        units = [u for p in mine for u in p["units"] if u[0] == b]
        right = sum(1 for u in units if u[2] == u[1] and u[4] == u[0])
        lo, hi = cp(right, len(units))
        t = metrics[f"competence.{b}"]
        good = (t["value"] == round(right / len(units), 6) and abs(t["interval"][0] - lo) < 1e-5
                and abs(t["interval"][1] - hi) < 1e-5)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} competence.{b}: tool={t['value']} {t['interval']} hand={right}/{len(units)} "
              f"[{lo:.6f}, {hi:.6f}]")
    for k, n in ((27, 30), (0, 30), (30, 30), (14, 31)):
        a, b = score.clopper_pearson(k, n), cp(k, n)
        good = abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} clopper_pearson({k},{n}): tool={[round(v, 6) for v in a]} "
              f"hand={[round(v, 6) for v in b]}")
    gates = {g["name"]: g for g in score.sample_gates(analyses, stats, GATE, True)}
    for b in ("fine", "casual"):
        g = gates[f"units.{b}"]
        n = sum(1 for p in mine for u in p["units"] if u[0] == b)
        good = (g["n"], g["sessions"], g["days"], g["met"]) == (n, 3, 2, False)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} sample gate units.{b}: tool={g} hand=n {n}, 3 sessions, 2 days, unmet")
    g = gates["onsets"]
    good = (g["n"], g["sessions"], g["days"]) == (9, 3, 2)
    ok &= good
    print(f"{'PASS' if good else 'FAIL'} sample gate onsets: tool={g} hand=n 9, 3 sessions, 2 days")
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
