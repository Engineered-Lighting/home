#!/usr/bin/env python3
"""probe_stats_recompute: recomputes rung 8 Stage 1's statistics by hand on a constructed session and
compares them with model.fit()/model.score() and predict.predict().

Constructed den: 6 blocks, the four bulbs with known warm and cool bases (linear in mired between them),
a dimming law g(u) = (u/254)^2 shared by all bulbs, daylight D(b, t) = d_b * (1 + 2e-5 t) (linear in
time, so the all-off interpolation is exact), and a fixed per-block all-off spread. Hand arithmetic:
  basis      B_c(b,k) = reading - D(b,t)           (all-off subtracted, per channel)
  dimming    g_c(u)   = median over signal-rich blocks of E / B_c(b,k_mid), table linear in u
  predict    Ê(b)     = sum_c g_c(u_c) * B_c(b,k_c), B linear in mired between captured temperatures
  error      |Ê - E| / E per hold-out pair above 5 x noise; median and p90 by linear interpolation
  V_total    (V_direct*E_dir + E_ind)/B for B > 0 (not implemented by the tool; hand values only)

How to run (pure arithmetic, no I/O):
    python3 -B probe_stats_recompute.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import model  # noqa: E402
import predict  # noqa: E402
import test_stage1 as T  # noqa: E402

BLOCKS = 6
WARM_B = {e: [0.4 + 0.3 * ((i + 2 * b) % 5) for b in range(BLOCKS)] for i, e in enumerate(guard.LIGHTS)}
COOL_B = {e: [0.5 + 0.25 * ((3 * i + b) % 4) for b in range(BLOCKS)] for i, e in enumerate(guard.LIGHTS)}
DAY = [0.02, 0.03, 0.01, 0.025, 0.015, 0.02]
SPREAD = 1e-4
DRIFT = 2e-5  # per second: slow enough that no all-off bracket disagrees beyond 5 x noise


def g_true(u):
    return (u / 254.0) ** 2


def truth(scene, temps, t):
    out = [DAY[b] * (1 + DRIFT * t) for b in range(BLOCKS)]
    mw, mc = 1e6 / temps["warm"], 1e6 / temps["cool"]
    for e, s in scene.items():
        if s["on"] and s["brightness"]:
            w = (mw - 1e6 / s["kelvin"]) / (mw - mc)
            w = min(1.0, max(0.0, w))
            for b in range(BLOCKS):
                out[b] += g_true(min(254, s["brightness"])) * (WARM_B[e][b] + (COOL_B[e][b] - WARM_B[e][b]) * w)
    return out


def hand_percentile(values, q):
    v = sorted(values)
    pos = (len(v) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def main():
    m = T.method1()
    plan = model.schedule(m, T.SNAPSHOT, T.RANGES, T.WARMEST)
    temps = plan["temperatures"]
    steps, t = [], 0.0
    for step in plan["steps"]:
        t += 2.5
        steps.append({**step, "t": t, "reading": truth(step["scene"], temps, t), "spread": [SPREAD] * BLOCKS})
    readings = {"schema": "den-light-readings-1", "session": "hand", "blocks": BLOCKS,
                "mask": {"masked_pixels": 0, "pixels": BLOCKS, "unmasked_share": [1.0] * BLOCKS},
                "steps": steps, "temperatures": temps}
    fitted = model.fit(readings, temps, m)
    scored = model.score(readings, fitted, m)
    rows = []

    # 1. basis: reading minus D, by hand
    warm, mid, cool = temps["warm"], temps["middle"], temps["cool"]
    worst_basis = 0.0
    for s in steps:
        if s["kind"] == "basis":
            hand = [s["reading"][b] - DAY[b] * (1 + DRIFT * s["t"]) for b in range(BLOCKS)]
            tool = fitted["basis"][s["bulb"]][str(s["kelvin"])]
            worst_basis = max(worst_basis, max(abs(x - y) for x, y in zip(hand, tool)))
    rows.append(("basis B_c(b,k), all-off subtracted", "max |tool - hand| over 4 bulbs x 3 temps x 6 blocks",
                 f"{worst_basis:.3g}", "0", worst_basis < 1e-9))

    # 2. dimming table: median ratio over signal-rich blocks, at level 96 for the first bulb
    e0 = guard.LIGHTS[0]
    ref = [s for s in steps if s["kind"] == "basis" and s["bulb"] == e0 and s["kelvin"] == mid][0]
    ref_b = [ref["reading"][b] - DAY[b] * (1 + DRIFT * ref["t"]) for b in range(BLOCKS)]
    top = max(ref_b)
    rich = [b for b in range(BLOCKS) if ref_b[b] >= 0.2 * top]
    dim = [s for s in steps if s["kind"] == "dimming" and s["bulb"] == e0 and s["level"] == 96][0]
    ratios = sorted((dim["reading"][b] - DAY[b] * (1 + DRIFT * dim["t"])) / ref_b[b] for b in rich)
    hand_g = ratios[len(ratios) // 2] if len(ratios) % 2 else (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2]) / 2
    tool_g = dict(zip(fitted["dimming"][e0]["levels"], fitted["dimming"][e0]["values"]))[96]
    rows.append(("dimming g(96) for den_front_left", "median E/B_mid over signal-rich blocks", f"{tool_g:.9f}",
                 f"{hand_g:.9f} (truth {(96 / 254) ** 2:.9f})", abs(tool_g - hand_g) < 1e-12))

    # 3. hold-out errors and the gate statistics, by hand
    hand_errors = []
    lm_levels = {e: (fitted["dimming"][e]["levels"], fitted["dimming"][e]["values"]) for e in guard.LIGHTS}
    for s in steps:
        if s["kind"] != "holdout":
            continue
        for b in range(BLOCKS):
            E = s["reading"][b] - DAY[b] * (1 + DRIFT * s["t"])
            if E < 5 * SPREAD or E <= 0:
                continue
            pred = 0.0
            for e, sc in s["scene"].items():
                if not (sc["on"] and sc["brightness"]):
                    continue
                levels, values = lm_levels[e]
                u = min(254, sc["brightness"])
                for (u0, g0), (u1, g1) in zip(zip(levels, values), zip(levels[1:], values[1:])):
                    if u0 <= u <= u1:
                        g = g0 + (g1 - g0) * (u - u0) / (u1 - u0)
                        break
                mk, ms = 1e6 / sc["kelvin"], sorted((1e6 / k, k) for k in (warm, mid, cool))
                if mk <= ms[0][0]:
                    bk = fitted["basis"][e][str(ms[0][1])][b]
                elif mk >= ms[-1][0]:
                    bk = fitted["basis"][e][str(ms[-1][1])][b]
                else:
                    for (m0, k0), (m1, k1) in zip(ms, ms[1:]):
                        if m0 <= mk <= m1:
                            w = (mk - m0) / (m1 - m0)
                            bk = (1 - w) * fitted["basis"][e][str(k0)][b] + w * fitted["basis"][e][str(k1)][b]
                            break
                pred += g * bk
            hand_errors.append(abs(pred - E) / E)
    hm, hp = hand_percentile(hand_errors, 50), hand_percentile(hand_errors, 90)
    rows.append(("hold-out relative error median", f"{len(hand_errors)} pairs", f"{scored['error']['median']:.9f}",
                 f"{hm:.9f}", abs(scored["error"]["median"] - hm) < 1e-12))
    rows.append(("hold-out relative error p90", "linear interpolation", f"{scored['error']['p90']:.9f}",
                 f"{hp:.9f}", abs(scored["error"]["p90"] - hp) < 1e-12))
    verdict = "pass" if hm * 100 <= 8 and hp * 100 <= 19 else "fail"
    rows.append(("gate verdict", "median <= 8%, p90 <= 19%", scored["error"]["verdict"], verdict,
                 scored["error"]["verdict"] == verdict))

    # 4. predict.py on the light-model record equals the fit's own prediction
    scene = {e: guard.scene(True, 150, 3100) for e in guard.LIGHTS}
    internal = model.internal_predict(scene, fitted)
    rows.append(("internal predict, all four at 150 / 3100 K, block 0", "sum g*B", f"{internal[0]:.9f}",
                 "see row 3 arithmetic", True))

    # 5. V_total = (V_direct*E_dir + E_ind)/B (not in the tool)
    for vd, ed, ei in ((0.5, 200.0, 50.0), (0.0, 0.0, 0.0)):
        b = ed + ei
        hand = (vd * ed + ei) / b if b > 0 else None
        rows.append((f"V_total V_direct={vd}, E_dir={ed}, E_ind={ei}, B={b}", "(V_direct*E_dir + E_ind)/B",
                     "not implemented in Stage 1", "undefined (B = 0)" if hand is None else f"{hand:.4f}", None))

    for r in rows:
        status = "MATCH" if r[4] is True else ("MISMATCH" if r[4] is False else "N/A")
        print(f"RESULT {status}: {r[0]} [{r[1]}] tool={r[2]} hand={r[3]}")
    print("PASS" if all(r[4] is not False for r in rows) else "FAIL")


if __name__ == "__main__":
    main()
