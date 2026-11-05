#!/usr/bin/env python3
"""Recompute den-take's clock statistics by hand and compare with check.py.

    python3 -B probe_clock_stats.py <checkout root>     # e.g. .../wt/rung-r

Independent implementations (from CHECK-V1.md's text, not check.py's code) of:
  A. B4 two-clock rule: least-squares slope of d = t_host - t_device against
     t_host (exact Fractions), the largest residual and the largest step, with
     accept/reject at each boundary (step 5 ms, line 20 ms, slope +-1000 ppm).
  B. The slate fit: a phone pattern rendered from the CHECK-V1 text, the set of
     consistent offsets on the 1 ms grid over [-5, 5) with the epsilon skip, the
     window (wrapping at +-5 s), its width and midpoint c, for known true offsets.
  C. The elapsed bound |wrap(c_end - c_start)| + (w_start + w_end)/2 <= 0.20 s for
     known drifts, and the slate separation (first frames <= 1800 s apart).
Synthetic frames only, built in memory; nothing is written. Prints PASS/FAIL
lines per comparison and a RESULT line.
"""
import math
import random
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402

M = check.load_method()
PERIOD, FLASH, STEP, MARKER = 10, Fraction(1, 2), Fraction(1, 50), 0
EPS = Fraction(25, 1000)
GRID = Fraction(1, 1000)
failures = []


def report(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    if not ok:
        failures.append(text)


# ---- A. two-clock rule -------------------------------------------------------

def my_two_clock(xs, ds):
    X = [Fraction(x) for x in xs]
    D = [Fraction(d) for d in ds]
    n = len(X)
    mx, md = sum(X) / n, sum(D) / n
    sxx = sum((x - mx) ** 2 for x in X)
    slope = sum((x - mx) * (d - md) for x, d in zip(X, D)) / sxx
    resid = max(abs(d - (md + slope * (x - mx))) for x, d in zip(X, D))
    step = max(abs(b - a) for a, b in zip(D, D[1:]))
    return float(slope), float(resid), float(step)


def tool_b4(t_host, t_device):
    frames = [{"session": "s", "rgb_seq": i, "t_host": h, "t_device": d, "n": i + 1, "recv": h,
               "y": None, "stale": False} for i, (h, d) in enumerate(zip(t_host, t_device))]
    take = {"complete": True, "slates": [], "health_before": {"body": {"session": "s"}},
            "health_after": {"body": {"session": "s"}}}
    return check.check_rules(take, [{}], frames, [], M)["B4_two_clock"]


def b4_case(name, t_host, d, expect):
    t_device = [h - x for h, x in zip(t_host, d)]
    # the tool computes d from the floats it is given: recompute from the same floats
    d_float = [h - dv for h, dv in zip(t_host, t_device)]
    slope, resid, step = my_two_clock(t_host, d_float)
    rule = tool_b4(t_host, t_device)
    hand_pass = (step <= M["max_two_clock_step_s"] and abs(slope) * 1e6 <= M["max_two_clock_slope_ppm"]
                 and resid <= M["max_two_clock_line_s"])
    same = (math.isclose(rule["slope_ppm"], slope * 1e6, rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(rule["max_residual_s"], resid, abs_tol=1e-9)
            and math.isclose(rule["max_step_s"], step, abs_tol=1e-9))
    report(same and rule["pass"] == hand_pass and rule["pass"] == expect,
           f"B4 {name}: tool slope {rule['slope_ppm']:.4f} ppm resid {rule['max_residual_s']:.6f} "
           f"step {rule['max_step_s']:.6f} pass={rule['pass']} | hand slope {slope * 1e6:.4f} ppm "
           f"resid {resid:.6f} step {step:.6f} pass={hand_pass} | expected {expect}")


xs = [5000.0 + 0.25 * i for i in range(7200)]  # 30 min at 4 frames/s
b4_case("20 ppm drift", xs, [1200.0 + 20e-6 * (x - 5000) for x in xs], True)
b4_case("+999 ppm", xs, [1200.0 + 999e-6 * (x - 5000) for x in xs], True)
b4_case("+1001 ppm", xs, [1200.0 + 1001e-6 * (x - 5000) for x in xs], False)
b4_case("-1001 ppm", xs, [1200.0 - 1001e-6 * (x - 5000) for x in xs], False)
b4_case("step 4.9 ms", xs, [1200.0 + (0.0049 if x >= 5900 else 0.0) for x in xs], True)
b4_case("step 5.1 ms", xs, [1200.0 + (0.0051 if x >= 5900 else 0.0) for x in xs], False)


def bump(height):
    out = []
    for x in xs:
        u = x - 5800
        out.append(1200.0 + max(0.0, min(height, 0.004 * u, 0.004 * (2 * height / 0.004 + 20 - u))))
    return out


# a ramped bump: steps stay 1 ms, only the line rule can catch it
b4_case("bump 0.019 s", xs, bump(0.019), True)
b4_case("bump 0.023 s", xs, bump(0.023), False)

# ---- B. slate fit ------------------------------------------------------------


US = 1_000_000
STEP_US, FLASH_US, EPS_US = 20_000, 500_000, 25_000


def us(x):
    return round(Fraction(x) * US)


def my_lit(tau_us):
    """CHECK-V1 'Pattern' in integer microseconds of phone time."""
    s = tau_us // US
    k = s % PERIOD
    if k == MARKER:
        return False
    f = tau_us - s * US
    return STEP_US * k <= f < STEP_US * k + FLASH_US


def my_change_distance(tau_us):
    best = None
    s0 = tau_us // US
    for s in range(s0 - 2, s0 + 3):
        k = s % PERIOD
        if k == MARKER:
            continue
        for edge in (s * US + STEP_US * k, s * US + STEP_US * k + FLASH_US):
            dist = abs(tau_us - edge)
            best = dist if best is None or dist < best else best
    return best


def render(times, c_true):
    """y planes (160x90): a 20x20 phone screen in a flat room, lit per my_lit at phone time t - c."""
    planes = []
    c_us = us(c_true)
    for t in times:
        value = 220 if my_lit(us(t) - c_us) else 20
        rows = []
        for r in range(90):
            row = bytearray([60] * 160)
            if 30 <= r < 50:
                row[70:90] = bytes([value]) * 20
            rows.append(bytes(row))
        planes.append(b"".join(rows))
    return planes


def my_fit(times, c_true):
    c_us = us(c_true)
    data = [(us(t), my_lit(us(t) - c_us)) for t in times]
    steps = PERIOD * 1000
    good = []
    for j in range(steps):
        c = (j - steps // 2) * 1000
        ok = True
        for t, lit in data:
            tau = t - c
            if my_change_distance(tau) >= EPS_US and my_lit(tau) != lit:
                ok = False
                break
        if ok:
            good.append(j)
    runs = []
    for j in good:
        if runs and j == runs[-1][1] + 1:
            runs[-1][1] = j
        else:
            runs.append([j, j])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == steps - 1:
        first = runs.pop(0)
        runs[-1][1] = first[1] + steps
    windows = [(Fraction(a - steps // 2, 1000), Fraction(b - steps // 2, 1000)) for a, b in runs]
    if len(windows) != 1:
        return None, windows
    lo, hi = windows[0]
    mid = (lo + hi) / 2
    mid = (mid + 5) % 10 - 5
    return (float(mid), float(hi - lo)), windows


def frame_times(start, seconds, seed):
    rng, t, out = random.Random(seed), start, []
    while t < start + seconds:
        out.append(round(t, 6))
        t += rng.choice((0.2, 4 / 15, 0.25))
    return out


fits = {}
for seed, c_true in enumerate((0.0, 1.2345, 3.2137, -4.99, 4.995, 4.9996, -2.5005)):
    times = frame_times(7000.0 + 40 * seed, 20.0, seed + 11)
    tool = check.fit_slate(list(zip(times, render(times, c_true))), M)
    mine, windows = my_fit(times, c_true)
    fits[c_true] = (tool, mine)
    if mine is None:
        report(not tool["readable"], f"fit c={c_true}: hand windows {len(windows)} (unreadable); tool readable="
                                     f"{tool['readable']} reason={tool['reason']}")
        continue
    c_hand, w_hand = mine
    inside = abs(check.wrap(c_true - c_hand, 10)) <= w_hand / 2 + 1e-9
    report(tool["readable"] and abs(check.wrap(tool["c_s"] - c_hand, 10)) <= 0.0015
           and abs(tool["width_s"] - w_hand) <= 0.0015 and inside,
           f"fit c_true={c_true}: tool c={tool['c_s']} w={tool['width_s']} windows={tool['windows']} | "
           f"hand c={c_hand:.4f} w={w_hand:.4f}; true offset inside hand window: {inside}")

# ---- C. elapsed bound and separation ----------------------------------------


def slate_frames(t0, c_true, seed):
    times = frame_times(t0, 20.0, seed)
    return [{"session": "s", "rgb_seq": 0, "t_host": t, "t_device": t - 9, "n": 0, "recv": t, "y": y,
             "stale": False} for t, y in zip(times, render(times, c_true))], times


def tool_a(c_start, c_end, gap_s=200.0, m=M):
    s_frames, s_times = slate_frames(8000.0, c_start, 5)
    e_frames, e_times = slate_frames(8000.0 + gap_s, c_end, 6)
    frames = s_frames + e_frames
    for i, f in enumerate(frames):
        f["rgb_seq"], f["n"] = i, i + 1
    take = {"complete": True, "health_before": {"body": {"session": "s"}},
            "health_after": {"body": {"session": "s"}},
            "slates": [{"slate": "start", "attempt": 1, "pc_start_s": s_times[0], "pc_end_s": s_times[-1]},
                       {"slate": "end", "attempt": 1, "pc_start_s": e_times[0], "pc_end_s": e_times[-1]}]}
    rules = check.check_rules(take, [{}], frames, [], m)
    return rules, s_times, e_times


for c_start, drift in ((1.0, 0.0), (1.0, 0.10), (1.0, 0.14), (1.0, 0.149), (1.0, 0.152), (1.0, 0.17),
                       (4.95, 0.10), (-4.97, -0.06), (2.0, -0.149), (2.0, -0.153)):
    c_end = c_start + drift
    rules, s_times, e_times = tool_a(c_start, check.wrap(c_end, 10))
    a, _ = my_fit(s_times, c_start)
    b, _ = my_fit(e_times, check.wrap(c_end, 10))
    if a is None or b is None:
        report(not rules["A_elapsed_bound"]["pass"], f"elapsed c_start={c_start} drift={drift}: hand unreadable")
        continue
    hand = abs(check.wrap(b[0] - a[0], 10)) + (a[1] + b[1]) / 2
    hand_pass = round(hand, 9) <= M["elapsed_bound_s"]
    tool = rules["A_elapsed_bound"]
    report(tool["elapsed_bound_s"] is not None and abs(tool["elapsed_bound_s"] - hand) <= 0.002
           and tool["pass"] == hand_pass,
           f"elapsed c_start={c_start} drift={drift:+.3f}: tool {tool['elapsed_bound_s']} pass={tool['pass']} | "
           f"hand {hand:.4f} pass={hand_pass} (widths {a[1]:.3f}, {b[1]:.3f})")

for gap, expect in ((1800.0 - 0.0, True), (1800.2, False)):
    rules, s_times, e_times = tool_a(1.0, 1.0, gap_s=gap)
    hand = e_times[0] - s_times[0]
    tool = rules["A_separation"]
    report(abs(tool["separation_s"] - hand) < 1e-9 and tool["pass"] == (hand <= 1800) == expect,
           f"separation {gap}: tool {tool['separation_s']} pass={tool['pass']} | hand {hand} pass={hand <= 1800}")

print(f"RESULT {'PASS' if not failures else 'FAIL'}: {len(failures)} mismatches")
for text in failures:
    print("  mismatch:", text)
