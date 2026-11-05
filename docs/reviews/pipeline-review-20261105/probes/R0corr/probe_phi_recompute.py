"""probe_phi_recompute: recompute corrections per occupied hour and phi independently and compare with capture.py.

phi = Pearson chi-square / (n_sessions - p) of a quasi-Poisson GLM with a log(occupied_h) offset, fitted here by
IRLS (pure Python), once with one rate per stratum (what capture.phi_hat says it fits) and once intercept-only (the
ladder's wording: an offset, no strata). Cases: several sessions in one stratum; two strata; a stratum with no
corrections; a session with corrections but zero occupied hours; a session whose hours round to 0 at 4 places.
Also runs capture.capture end to end on a constructed 4-evening export and checks per-session hours, counts and
corrections per occupied hour against a hand count.

Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 probe_phi_recompute.py <root of the claude/correction-capture checkout>
"""
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

L0, L1, L2, L3 = events.LIGHTS


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        for r in range(n):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i])]
    return [m[i][n] / m[i][i] for i in range(n)]


def glm_phi(sessions, by_stratum):
    """IRLS Poisson fit, log link, offset log(h); Pearson X2 / (n - p). Sessions with h <= 0 cannot carry an offset
    and are dropped; a stratum whose counts are all zero has an MLE rate of 0 (Pearson undefined), dropped too."""
    rows = [s for s in sessions if s["occupied_h"] > 0]
    if by_stratum:
        keep = {st for st in {s["mode_stratum"] for s in rows} if sum(s["corrections"] for s in rows
                                                                       if s["mode_stratum"] == st) > 0}
        rows = [s for s in rows if s["mode_stratum"] in keep]
        levels = sorted(keep)
        x = [[1.0 if s["mode_stratum"] == lv else 0.0 for lv in levels] for s in rows]
    else:
        if sum(s["corrections"] for s in rows) == 0:
            return None
        x = [[1.0] for _ in rows]
    p = len(x[0]) if x else 0
    if len(rows) - p < 1:
        return None
    y = [s["corrections"] for s in rows]
    off = [math.log(s["occupied_h"]) for s in rows]
    beta = [0.0] * p
    for _ in range(100):
        eta = [sum(b * xi for b, xi in zip(beta, xr)) + o for xr, o in zip(x, off)]
        mu = [math.exp(e) for e in eta]
        z = [e - o + (yi - m) / m for e, o, yi, m in zip(eta, off, y, mu)]
        xtwx = [[sum(m * xr[i] * xr[j] for m, xr in zip(mu, x)) for j in range(p)] for i in range(p)]
        xtwz = [sum(m * xr[i] * zi for m, xr, zi in zip(mu, x, z)) for i in range(p)]
        new = solve(xtwx, xtwz)
        if max(abs(a - b) for a, b in zip(new, beta)) < 1e-12:
            beta = new
            break
        beta = new
    mu = [math.exp(sum(b * xi for b, xi in zip(beta, xr)) + o) for xr, o in zip(x, off)]
    return sum((yi - m) ** 2 / m for yi, m in zip(y, mu)) / (len(rows) - p)


def S(h, c, st="no_game"):
    return {"occupied_h": h, "corrections": c, "mode_stratum": st}


CASES = {
    "one stratum, 5 sessions": [S(2.0, 1), S(3.5, 6), S(1.0, 0), S(4.0, 3), S(2.5, 0)],
    "two strata": [S(2.0, 1), S(3.5, 6), S(1.0, 0), S(1.5, 2, "game"), S(0.5, 0, "game"), S(2.0, 1, "game")],
    "game stratum all zero": [S(2.0, 1), S(3.5, 6), S(1.0, 0), S(1.5, 0, "game"), S(0.5, 0, "game")],
    "zero-hour session with corrections": [S(1.0, 1), S(1.0, 1), S(0.0, 2)],
    "hours that round to 0.0": [S(2.0, 3), S(2.0, 1), S(0.0, 1)],
    "single session": [S(3.0, 4)],
    "all zero counts": [S(3.0, 0), S(2.0, 0)],
}
bad = 0
for name, sessions in CASES.items():
    tool = capture.phi_hat(sessions)[0]
    strat = glm_phi(sessions, True)
    flat = glm_phi(sessions, False)
    ok = (tool is None and strat is None) or (tool is not None and strat is not None and abs(tool - strat) < 1e-3)
    bad += not ok
    print("%s %-36s tool=%s irls_by_stratum=%s irls_intercept_only=%s" % (
        "PASS" if ok else "FAIL", name, tool, None if strat is None else round(strat, 4),
        None if flat is None else round(flat, 4)))

# End to end: 4 evenings (24-hour blocks), switch corrections, hand-counted.
STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()  # 02:00 UTC, inside the block that starts 20:00
plan = [(3.0, 2), (1.5, 0), (4.0, 5), (2.5, 1)]  # (occupied hours, switch corrections) per evening
status, bulbs, rev, n = [], [], 100, 0


def srow(t, occ, holds):
    global rev
    rev += 1
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev, "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


for day, (hours, count) in enumerate(plan):
    start = T0 + day * 86400
    srow(start, "on", [])
    for k in range(count):
        n += 1
        t = start + (k + 1) * hours * 3600 / (count + 1)
        e = events.LIGHTS[k % 4]
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                      "context_id": "%032X" % (0x0190000000000000000000000000 + n), "context_user_id": "",
                      "context_parent_id": ""})
        srow(t + 0.01, "on", [e])
        srow(t + 60, "on", [])
    srow(start + hours * 3600, "off", [])
with tempfile.TemporaryDirectory() as tmp:
    paths = []
    for name, rows in (("s", status), ("b", bulbs), ("c", [])):
        p = Path(tmp) / (name + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        paths.append([p])
    record, _ = capture.capture(*paths)
per = record["exposure"]["per_session"]
got = [(s["occupied_h"], s["corrections"]) for s in per]
want = [(h, c) for h, c in plan]
print("%s per-session (occupied_h, corrections): tool=%s hand=%s" % ("PASS" if got == want else "FAIL", got, want))
bad += got != want
rate_tool = record["planning"]["strata"]["no_game"]["people"]["per_occupied_h"]
rate_hand = round(sum(c for _, c in plan) / sum(h for h, _ in plan), 4)
print("%s corrections per occupied hour: tool=%s hand=%s" % ("PASS" if rate_tool == rate_hand else "FAIL",
                                                             rate_tool, rate_hand))
bad += rate_tool != rate_hand
phi_tool = record["exposure"]["phi_hat"]
phi_hand = glm_phi([S(h, c) for h, c in plan], True)
print("%s phi_hat end to end: tool=%s irls=%.4f" % ("PASS" if abs(phi_tool - phi_hand) < 1e-3 else "FAIL",
                                                    phi_tool, phi_hand))
bad += abs(phi_tool - phi_hand) >= 1e-3
print("RESULT %d mismatch(es)" % bad)
