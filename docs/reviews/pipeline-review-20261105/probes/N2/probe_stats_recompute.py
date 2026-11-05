#!/usr/bin/env python3
"""Independent recomputation of N2's key statistics against tools/den-n2/score.py.

What it checks, on small synthetic sessions built here (no real data, no network):
  1. ECE with 10 equal-width bins on [0, 1]: my own binning (exact decimal
     arithmetic) and formula sum_b (n_b/N)|acc_b - conf_b| against score.py's
     session_stats + m_ece, on directly given units and on units whose
     confidence is a mean of identical per-second values at a decile edge.
  2. false-fine share of non-fine seconds and per-second fine recall (fresh fine
     level only), from my own second grid (k + 0.5 samples, uncertain spans
     removed, tau_on/grace/tau_off windows, carried onset windows from the
     segment start) against score.py's session_stats.
  3. fine-demand recall per EPISODE (the ladder's rung-6 definition, abstain or
     stale = miss) next to score.py's per-second fine_recall.
  4. onset: d_on per timed onset (entered, entered_on, stayed_on, missed,
     entered_from_ending) and the median of max(d_on, 0), against score.py.
  5. session bootstrap: resampling unit = session, 10,000 rounds, percentile
     2.5/97.5 with linear interpolation, seeded from the labels hash; my own
     loop on the same stdlib RNG stream, and the exact (enumerated) bootstrap
     distribution on a tiny case.
  6. sample-gate counts by band, session and day.

Run:  python3 -B probe_stats_recompute.py <root of the claude/den-n2 checkout>
Prints PASS/FAIL/RESULT lines. Writes nothing.
"""
import itertools
import math
import random
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
import score  # noqa: E402

GATE = score.load_gate()
FAILS = []


def check(name, tool, mine, tol=1e-9):
    ok = (tool == mine) if not (isinstance(tool, float) or isinstance(mine, float)) else (
        tool is not None and mine is not None and abs(tool - mine) <= tol)
    print(f"{'PASS' if ok else 'FAIL'} {name}: tool={tool!r} hand={mine!r}")
    if not ok:
        FAILS.append(name)
    return ok


# ---------------------------------------------------------------- 1. ECE ----------------------------------

def my_ece(pairs, bins=10):
    """pairs: [(conf as exact Fraction, acc 0/1)]. Equal-width bins [b/10, (b+1)/10), the last one closed."""
    groups = {}
    for conf, acc in pairs:
        b = min(int(conf * bins), bins - 1)  # conf is a Fraction here: exact floor
        n, c, a = groups.get(b, (0, Fraction(0), 0))
        groups[b] = (n + 1, c + conf, a + acc)
    total = sum(n for n, _, _ in groups.values())
    return float(sum(abs(Fraction(a) - c) for _, c, a in groups.values()) / total) if total else None


def tool_ece(units, field):
    an = score.Analysis(None, [], units, [], [], [], 0, {}, {})
    return score.m_ece(field)([score.session_stats(an, GATE)])


def unit(eng_conf, label_on, gold_on=True, band="fine", gold_band="fine", dem_conf=None):
    return score.Unit("t", "interval", "sorting_mail", gold_band, gold_on, label_on, band, eng_conf,
                      dem_conf, 1.0, False)


direct = [(0.95, 1), (0.95, 1), (0.95, 0), (0.55, 1), (0.05, 0), (0.15, 1), (1.0, 1), (0.1, 0), (0.0, 0)]
units = [unit(c, label_on=bool(a)) for c, a in direct]
check("ECE engagement, 9 units incl. conf 0.0, 0.1 and 1.0", tool_ece(units, "engagement"),
      my_ece([(Fraction(str(c)), a) for c, a in direct]))

# A unit's confidence is the MEAN of its seconds' confidences. When every second carries the same table
# value at a decile edge (0.7), the float mean can land just under the edge and fall one bin low.
edge_hits = []
for c in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
    for n in range(1, 61):
        m = score.mean([c] * n)
        if int(m * 10) != int(Fraction(str(c)) * 10):
            edge_hits.append((c, n, m))
print(f"RESULT mean of n identical decile confidences landing in the wrong ECE bin (n <= 60): "
      f"{len(edge_hits)} cases, e.g. {edge_hits[:4]}")
if edge_hits:
    c, n, m = edge_hits[0]
    # five right units whose seconds all say c (true bin int(10c)), five wrong units at c - 0.05 (bin below)
    lo = round(c - 0.05, 2)
    units = [unit(m, True) for _ in range(5)] + [unit(lo, False) for _ in range(5)]
    tool = tool_ece(units, "engagement")
    mine = my_ece([(Fraction(str(c)), 1)] * 5 + [(Fraction(str(lo)), 0)] * 5)
    check(f"ECE with five units whose {n} seconds all say {c} (mean {m!r})", tool, mine, tol=1e-6)

# demand ECE leaves abstained units out
dem = [unit(0.9, True, band="fine", dem_conf=0.8), unit(0.9, True, band=None, dem_conf=None),
       unit(0.9, True, band="casual", gold_band="fine", dem_conf=0.6)]
check("ECE demand, an abstained unit left out", tool_ece(dem, "demand"),
      my_ece([(Fraction("0.8"), 1), (Fraction("0.6"), 0)]))


# --------------------------------------------- 2/3. the grid: false-fine, fine recall ----------------------

def iv(on, off, band, case, seg=0, onset_kind="touch", offset_kind="release", unsure=()):
    return {"on": on, "off": off, "band": band, "case": case, "seg": seg, "onset_kind": onset_kind,
            "offset_kind": offset_kind, "sure": {e: e not in unsure for e in ("onset", "offset", "band")}}


def R(t, state, level, cap, conf=0.9):
    return score.Rec(t, state, conf, level, None if level is None else conf, cap)


SEGMENTS = [(0.0, 300.0), (300.0, 400.0)]
INTERVALS = [iv(10.0, 40.0, "fine", "sorting_mail"), iv(100.0, 130.0, "casual", "wiping"),
             iv(150.25, 160.75, "fine", "still_reader"),
             iv(300.0, 330.0, "fine", "still_reader", seg=1, onset_kind="carried"),
             iv(345.0, 375.0, "fine", "sorting_mail", seg=1)]
SPANS = [{"kind": "quiet", "case": "idle_seated", "start": 41.0, "end": 99.0, "seg": 0},
         {"kind": "quiet", "case": "idle_standing", "start": 230.0, "end": 299.0, "seg": 0},
         {"kind": "uncertain", "case": None, "start": 170.2, "end": 172.9, "seg": 0},
         {"kind": "low_light", "case": None, "start": 0.0, "end": 20.0, "seg": 0}]
LOGS = [[R(0.5, "none", 0.2, 0.0), R(12, "starting", 0.8, 11), R(17, "engaged", 0.8, 16),
         R(30, "engaged", 0.8, 16),  # stale from 26 on: recall misses, false-fine would still count it
         R(41, "ending", 0.7, 40), R(60, "none", 0.65, 59),
         R(105, "starting", 0.62, 104), R(115, "engaged", 0.5, 114), R(135, "ending", 0.3, 134),
         R(152, "starting", 0.6, 151), R(165, "none", 0.6, 164)],  # 0.6 exactly: fine, then left on (stale)
        [R(300.5, "engaged", 0.8, 300), R(320, "engaged", 0.8, 319), R(331, "ending", 0.3, 330),
         R(350, "starting", 0.9, 349), R(358, "engaged", 0.3, 357)]]


def my_grid():
    tau_on, tau_off, grace, max_age = 3, 60, 6, 10
    unc = [(s["start"], s["end"]) for s in SPANS if s["kind"] == "uncertain"]
    ff = [0, 0]
    fr = [0, 0]
    episodes = []
    for j, (a, b) in enumerate(SEGMENTS):
        ivs = [x for x in INTERVALS if x["seg"] == j]
        recs = sorted(LOGS[j], key=lambda r: r.t)
        offs = [x["off"] for x in ivs if x["offset_kind"] != "carried"]
        per_iv = {id(x): [] for x in ivs}
        k = math.ceil(a)
        while k + 1 <= b:
            if any(k < e and k + 1 > s for s, e in unc):
                k += 1
                continue
            x = k + 0.5
            held = [v for v in ivs if v["on"] <= x <= v["off"]]
            force = [r for r in recs if r.t <= x]
            r = force[-1] if force else None
            lvl = r.level if r else None
            fine = lvl is not None and lvl >= 0.6
            fresh = lvl is not None and x < r.cap + max_age
            if held:
                v = held[0]
                start = a if v["onset_kind"] == "carried" else v["on"]
                if v["sure"]["band"] and x >= start + grace:
                    if v["band"] == "fine":
                        fr[0] += fresh and fine
                        fr[1] += 1
                        per_iv[id(v)].append((fresh, lvl))
                    else:
                        ff[0] += fine
                        ff[1] += 1
            elif not any(o < x < o + tau_off for o in offs):
                ff[0] += fine
                ff[1] += 1
            k += 1
        for v in ivs:  # per episode: a fine unit is a hit when the median of its fresh levels is fine
            if v["band"] != "fine" or v["onset_kind"] == "carried" or v["offset_kind"] == "carried":
                continue
            fresh_levels = sorted(l for f, l in per_iv[id(v)] if f)
            if fresh_levels:
                n = len(fresh_levels)
                med = (fresh_levels[(n - 1) // 2] + fresh_levels[n // 2]) / 2
                episodes.append(med >= 0.6)
            else:
                episodes.append(False)  # abstained: a miss
    return ff, fr, episodes


session = score.Session("g1", "development", 1_793_016_000.0, "2026-10-26", SEGMENTS, INTERVALS, SPANS)
session.logs = LOGS
an = score.analyse(session, GATE, 4)
st = score.session_stats(an, GATE)
ff, fr, episodes = my_grid()
check("false-fine [hits, non-fine seconds]", st["ratio"]["false_fine"], ff)
check("fine recall per second [fresh fine, fine seconds]", st["ratio"]["fine_recall"], fr)
per_episode = sum(episodes) / len(episodes)
print(f"RESULT fine recall per second (score.py) = {fr[0]}/{fr[1]} = {fr[0] / fr[1]:.3f}; "
      f"per episode (ladder rung 6, abstain/stale = miss) = {sum(episodes)}/{len(episodes)} = {per_episode:.3f}")

# --------------------------------------------- 4. onset ---------------------------------------------------

def my_d_on(v, recs, prev_off):
    force = [r for r in recs if r.t <= v["on"]]
    r = force[-1] if force else None
    on_states = ("starting", "engaged")
    if r is not None and r.state in on_states:
        i = recs.index(r)
        while i > 0 and recs[i - 1].state in on_states:
            i -= 1
        return (0.0, "entered_on") if prev_off is None or recs[i].t > prev_off else (math.inf, "stayed_on")
    later = [x for x in recs if x.t > v["on"] and x.t < v["off"] and x.state in on_states]
    if not later:
        return math.inf, "missed"
    first = later[0]
    before = recs[recs.index(first) - 1].state if recs.index(first) > 0 else "none"
    return first.t - v["on"], ("entered_from_ending" if before == "ending" else "entered")


O_IVS = [iv(10.0, 40.0, "fine", "sorting_mail"),     # entered at 12.5: 2.5 s
         iv(80.0, 110.0, "casual", "wiping"),        # re-entry from ending at 83: 3 s
         iv(150.0, 180.0, "fine", "still_reader"),   # on since 140 (after 110): entered_on, 0
         iv(220.0, 250.0, "fine", "sorting_mail"),   # on since 140, through 180: stayed_on, inf
         iv(300.0, 330.0, "casual", "wiping")]       # never on: missed, inf
O_LOG = [R(0.5, "none", 0.2, 0), R(12.5, "starting", 0.8, 12), R(17.5, "engaged", 0.8, 17),
         R(45, "ending", 0.2, 44), R(83, "engaged", 0.5, 82), R(115, "ending", 0.2, 114),
         R(140, "starting", 0.8, 139), R(145, "engaged", 0.8, 144), R(281, "none", 0.2, 280)]
osess = score.Session("o1", "development", 1_793_016_000.0, "2026-10-26", [(0.0, 400.0)], O_IVS, [])
osess.logs = [O_LOG]
oan = score.analyse(osess, GATE, 4)
mine = []
for v in O_IVS:
    prev = max((x["off"] for x in O_IVS if x["off"] < v["on"]), default=None)
    mine.append(my_d_on(v, O_LOG, prev))
check("onset tags", [o["tag"] for o in oan.onsets], [t for _, t in mine])
check("onset d_on", [o["d_on"] for o in oan.onsets], [d for d, _ in mine])
vals = sorted(max(d, 0.0) for d, _ in mine)
check("onset median of max(d_on, 0)", score.m_list("onset", score.median)([score.session_stats(oan, GATE)]),
      vals[len(vals) // 2])

# --------------------------------------------- 5. session bootstrap -----------------------------------------

def my_percentile(xs, q):  # linear interpolation between order statistics (the 'linear' / type-7 rule)
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


parts = [{"ratio": {"false_fine": [k, d]}} for k, d in ((0, 120), (3, 100), (9, 80), (1, 150), (5, 90))]
seed = "3f" * 32
tool = score.bootstrap(parts, score.m_ratio("false_fine"), score.resamples(len(parts), 10000, seed), 0.95)
rng = random.Random(int(seed[:16], 16))
values = []
for _ in range(10000):
    draw = [rng.randrange(len(parts)) for _ in range(len(parts))]
    num = sum(parts[i]["ratio"]["false_fine"][0] for i in draw)
    den = sum(parts[i]["ratio"]["false_fine"][1] for i in draw)
    values.append(num / den)
values.sort()
check("bootstrap 95% two-sided, 10,000 rounds, same seed: lower", tool[0], my_percentile(values, 0.025))
check("bootstrap 95% two-sided, 10,000 rounds, same seed: upper", tool[1], my_percentile(values, 0.975))
exact = []
for draw in itertools.product(range(len(parts)), repeat=len(parts)):
    num = sum(parts[i]["ratio"]["false_fine"][0] for i in draw)
    den = sum(parts[i]["ratio"]["false_fine"][1] for i in draw)
    exact.append(num / den)
exact.sort()
lo_x, hi_x = exact[int(0.025 * len(exact))], exact[int(0.975 * len(exact)) - 1]
ok = abs(tool[0] - lo_x) < 0.006 and abs(tool[1] - hi_x) < 0.006
print(f"{'PASS' if ok else 'FAIL'} bootstrap vs the exact enumerated bootstrap over {len(exact)} resamples: "
      f"tool {[round(v, 4) for v in tool]} exact quantiles {[round(lo_x, 4), round(hi_x, 4)]}")
if not ok:
    FAILS.append("bootstrap exact")
three = parts[:3]
t3 = score.bootstrap(three, score.m_ratio("false_fine"), score.resamples(3, 10000, seed), 0.95)
singles = sorted(p["ratio"]["false_fine"][0] / p["ratio"]["false_fine"][1] for p in three)
check("3 sessions: the 95% interval is [min, max] of the single-session rates", [round(v, 9) for v in t3],
      [round(singles[0], 9), round(singles[-1], 9)])

# --------------------------------------------- 6. sample gates ----------------------------------------------

def an_with(take, day, n_fine, n_casual, n_onsets):
    s = score.Session(take, "held_out", 0.0, day, [(0.0, 10.0)], [], [])
    us = [score.Unit(take, "interval", "sorting_mail", "fine", True, True, "fine", 0.9, 0.9, 1.0, False)
          for _ in range(n_fine)]
    us += [score.Unit(take, "interval", "wiping", "casual", True, True, "casual", 0.9, 0.9, 1.0, False)
           for _ in range(n_casual)]
    us += [score.Unit(take, "quiet", "idle_seated", "none", False, False, "none", 0.9, 0.9, None, False)]
    ons = [{"take": take, "low": False, "d_on": 1.0, "w": 0.5, "dwell": None} for _ in range(n_onsets)]
    return score.Analysis(s, [], us, ons, [], [], 0, {}, {})


ans = [an_with("s1", "2026-10-26", 12, 10, 11), an_with("s2", "2026-10-26", 10, 10, 10),
       an_with("s3", "2026-10-27", 8, 10, 9)]
stats3 = [score.session_stats(a, GATE) for a in ans]
g = {x["name"]: x for x in score.sample_gates(ans, stats3, GATE, True)}
check("units.fine n/sessions/days/met", [g["units.fine"][k] for k in ("n", "sessions", "days", "met")],
      [30, 3, 2, True])
check("units.casual n/sessions/days/met", [g["units.casual"][k] for k in ("n", "sessions", "days", "met")],
      [30, 3, 2, True])
check("onsets n/sessions/days/met", [g["onsets"][k] for k in ("n", "sessions", "days", "met")], [30, 3, 2, True])
one_day = [an_with(t, "2026-10-26", 10, 10, 10) for t in ("s1", "s2", "s3")]
g1 = {x["name"]: x for x in score.sample_gates(one_day, [score.session_stats(a, GATE) for a in one_day], GATE,
                                               True)}
check("3 sessions on one day: units.fine unmet", g1["units.fine"]["met"], False)
print(f"RESULT band gates present: {sorted(n for n in g if n.startswith('units.'))}; "
      f"quiet (none-band) units in this case: {sum(1 for a in ans for u in a.units if u.gold_band == 'none')}")

print(f"RESULT {len(FAILS)} mismatch(es): {FAILS}")
sys.exit(1 if FAILS else 0)
