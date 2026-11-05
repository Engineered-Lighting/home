"""R0corr probe: recompute corrections per occupied hour, Garwood intervals and phi_hat independently.

What it checks
  1. CPH on a constructed multi-session export (3 evenings, game and no-game, app / switch / automation / rowless
     holds, a touch outside exposure): people and every-hold counts, occupied hours per stratum and per 24-hour block,
     corrections per occupied hour, compared with capture.capture() on the same synthetic files.
  2. Garwood 95% intervals from an independent regularized-incomplete-gamma implementation vs capture.garwood().
  3. phi = Pearson chi-square / (n - p) from an independent IRLS Poisson GLM with a log(occupied_h) offset and one
     rate per stratum (p = number of strata with exposure; an all-zero stratum keeps its rows and its parameter, as
     R's glm / statsmodels do) vs capture.phi_hat(), on:
       C  every stratum has corrections                    -> must match
       A  a stratum whose blocks all have 0 corrections     -> tool drops its rows from n and p
       B  a 0-hour block that carries a correction          -> tool keeps its count in the stratum rate but drops
                                                                the block from the Pearson sum
     and end to end through capture() with two game blocks that have no people corrections.
  4. The sample-size arithmetic in cph-budget-v0.json against the ladder's "about 150 h per arm at phi 1, about 300
     at phi 2".

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_phi_hat_glm_mismatch.py /path/to/branch/checkout
Prints PASS / FAIL / RESULT lines.
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

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
L0, L1, L2, L3 = LIGHTS
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
results = []


def check(name, ok, detail):
    results.append(ok)
    print("%s: %s -- %s" % ("PASS" if ok else "FAIL", name, detail))


# ------------------------------------------------------------------ independent statistics

def solve(a, b):
    """Gaussian elimination for a small dense system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col and m[col][col] != 0:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def glm_phi(sessions, by_stratum=True, iters=60):
    """Quasi-Poisson IRLS with offset log(h); rows with h == 0 cannot enter a log-offset model and are dropped.
    Returns (phi, chi2, n, p)."""
    rows = [s for s in sessions if s["occupied_h"] > 0]
    strata = sorted({s["mode_stratum"] for s in rows}) if by_stratum else ["all"]
    x = [[1.0 if (not by_stratum or s["mode_stratum"] == k) else 0.0 for k in strata] for s in rows]
    y = [float(s["corrections"]) for s in rows]
    off = [math.log(s["occupied_h"]) for s in rows]
    mu = [yi + 0.5 for yi in y]
    eta = [math.log(m) for m in mu]
    for _ in range(iters):
        z = [e - o + (yi - m) / m for e, o, yi, m in zip(eta, off, y, mu)]
        p = len(strata)
        xtwx = [[sum(x[i][a] * mu[i] * x[i][b] for i in range(len(rows))) for b in range(p)] for a in range(p)]
        xtwz = [sum(x[i][a] * mu[i] * z[i] for i in range(len(rows))) for a in range(p)]
        beta = solve(xtwx, xtwz)
        eta = [sum(xi[j] * beta[j] for j in range(p)) + o for xi, o in zip(x, off)]
        mu = [math.exp(e) for e in eta]
    chi2 = sum((yi - m) ** 2 / m for yi, m in zip(y, mu))
    n, p = len(rows), len(strata)
    return (chi2 / (n - p) if n > p else None), chi2, n, p


def gammainc_lower_reg(a, x):
    """Regularized lower incomplete gamma P(a, x): series for x < a + 1, continued fraction otherwise."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(10000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return total * math.exp(-x + a * math.log(x) - gln)
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return 1 - math.exp(-x + a * math.log(x) - gln) * h


def exact_poisson_ci(k, level=0.95):
    """Garwood via the gamma relation: lower solves P(k, mu) = a/2, upper solves P(k + 1, mu) = 1 - a/2."""
    a = 1 - level

    def inv(shape, target):
        lo, hi = 0.0, 10.0 * (shape + 10)
        for _ in range(300):
            mid = (lo + hi) / 2
            if gammainc_lower_reg(shape, mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    return (0.0 if k == 0 else inv(k, a / 2)), inv(k + 1, 1 - a / 2)


# ------------------------------------------------------------------ an independent synthetic export writer

class Export:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.rev, self.n = [], [], [], 500, 0
        self.now = {"automatic": True, "game": False, "occupancy": "off", "holds": {}, "provisional": []}

    def ctx(self):
        self.n += 1
        return "%032X" % (0x0193000000000000000000000000 + self.n)

    def row(self, t, **change):
        self.now.update(change)
        self.rev += 1
        n = self.now
        attrs = {"automatic": n["automatic"], "gaming_enabled": True, "gaming_session": n["game"],
                 "gaming_signal": "playing" if n["game"] else "idle", "occupancy": n["occupancy"],
                 "revision": self.rev, "error": None, "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in n["holds"].items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [],
                 "provisional_fixtures": list(n["provisional"])}
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                            "attributes": attrs})

    def bulb(self, t, e, on, user=False, parent=False):
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": self.ctx(), "context_user_id": "0E" * 16 if user else "",
                           "context_parent_id": "AC" * 16 if parent else ""})

    def touch(self, t, e, scene, how):
        """A hold opened or replaced by: 'switch' (id-less row), 'app' (user row), 'automation' (parent row),
        'rowless' (no bulb row)."""
        if how == "app":
            self.row(t, holds={**self.now["holds"], e: scene})
            self.bulb(t + 0.3, e, scene["on"], user=True)
        elif how == "automation":
            self.row(t, holds={**self.now["holds"], e: scene})
            self.bulb(t + 0.2, e, scene["on"], parent=True)
        elif how == "switch":
            self.bulb(t, e, scene["on"])
            self.row(t + 0.01, holds={**self.now["holds"], e: scene})
        else:
            self.row(t, holds={**self.now["holds"], e: scene})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append([p])
        return out


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


def three_evenings(game_people=True):
    ex = Export()
    # Evening 1, block 2026-10-28 (02:00-05:00 UTC on Oct 29): 3 h no game; switch, app, automation, rowless.
    t = utc(2026, 10, 29, 2)
    ex.row(t - 600)                                    # first row: automatic, occupancy off
    ex.row(t, occupancy="on")
    ex.touch(t + 1800, L0, OFF, "switch")              # people
    ex.touch(t + 3600, L1, WARM, "app")                # people
    ex.touch(t + 5400, L2, DIM, "automation")          # every hold only
    ex.touch(t + 7200, L3, DIM, "rowless")             # every hold only (unknown)
    ex.row(t + 10800, occupancy="off", holds={})
    # A touch while vacant: outside exposure.
    ex.touch(t + 11400, L0, WARM, "switch")
    ex.row(t + 11600, holds={})
    # Evening 2, block 2026-10-29: 2 h no game, then 1 h game.
    t = utc(2026, 10, 30, 1)
    ex.row(t, occupancy="on")
    ex.touch(t + 3000, L2, OFF, "switch")              # people, no_game
    ex.row(t + 7200, game=True, holds={})
    if game_people:
        ex.touch(t + 9000, L1, DIM, "app")             # people, game
    else:
        ex.touch(t + 9000, L1, DIM, "automation")      # game, every hold only
    ex.row(t + 10800, game=False, occupancy="off", holds={})
    # Evening 3, block 2026-10-30 (21:00-23:00 UTC Oct 30 is after the 20:00 anchor): 2 h, nothing touched.
    t = utc(2026, 10, 30, 21)
    ex.row(t, occupancy="on")
    if not game_people:
        ex.row(t + 3600, game=True)                    # a second game block-stratum with no people correction
        ex.row(t + 5400, game=False)
    ex.row(t + 7200, occupancy="off")
    ex.row(t + 8000)
    return ex


def main():
    # ---- 1. CPH on a constructed multi-session case
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record, _ = capture.capture(*three_evenings().write(tmp))
    st = record["planning"]["strata"]
    hand = {"no_game": {"h": 7.0, "people": 3, "every": 5}, "game": {"h": 1.0, "people": 1, "every": 1}}
    for s, e in hand.items():
        got = st[s]
        ok = (abs(got["occupied_h"] - e["h"]) < 1e-9 and got["people"]["count"] == e["people"]
              and got["every_hold"]["count"] == e["every"]
              and abs(got["people"]["per_occupied_h"] - round(e["people"] / e["h"], 4)) < 1e-9)
        check("cph %s" % s, ok, "tool h=%s people=%s every=%s cph=%s | hand h=%s people=%s every=%s cph=%.4f" % (
            got["occupied_h"], got["people"]["count"], got["every_hold"]["count"], got["people"]["per_occupied_h"],
            e["h"], e["people"], e["every"], e["people"] / e["h"]))
    want_sessions = [("2026-10-28/no_game", 3.0, 2), ("2026-10-29/no_game", 2.0, 1), ("2026-10-29/game", 1.0, 1),
                     ("2026-10-30/no_game", 2.0, 0)]
    got_sessions = [(s["session_id"], s["occupied_h"], s["corrections"]) for s in record["exposure"]["per_session"]]
    check("per_session blocks", got_sessions == want_sessions, "tool %s | hand %s" % (got_sessions, want_sessions))
    check("not counted outside exposure", record["planning"]["not_counted_by_reason"] == {"outside_exposure": 1},
          "tool %s" % record["planning"]["not_counted_by_reason"])
    phi_glm = glm_phi(record["exposure"]["per_session"])
    check("phi_hat, every stratum has corrections", record["exposure"]["phi_hat"] == round(phi_glm[0], 4),
          "tool %s | GLM %.4f (chi2 %.4f, n %d, p %d)" % ((record["exposure"]["phi_hat"],) + phi_glm))

    # ---- 2. Garwood
    worst = 0.0
    for k in (0, 1, 2, 5, 10, 30, 50, 120):
        lo, hi = capture.garwood(k)
        a, b = exact_poisson_ci(k)
        worst = max(worst, abs(lo - a), abs(hi - b))
    check("garwood vs incomplete gamma", worst < 1e-6, "max abs difference %.2e over k in 0..120" % worst)

    # ---- 3. phi on constructed per-session lists
    C = [{"occupied_h": h, "mode_stratum": m, "corrections": c} for h, m, c in (
        (1.0, "no_game", 1), (2.0, "no_game", 3), (3.0, "no_game", 2), (1.0, "game", 1), (1.0, "game", 0))]
    A = [{"occupied_h": h, "mode_stratum": m, "corrections": c} for h, m, c in (
        (3.5, "no_game", 3), (2.0, "no_game", 0), (4.0, "no_game", 5), (1.5, "no_game", 1),
        (1.0, "game", 0), (2.0, "game", 0), (0.5, "game", 0))]
    B = [{"occupied_h": h, "mode_stratum": m, "corrections": c} for h, m, c in (
        (0.0, "no_game", 1), (1.0, "no_game", 0), (1.0, "no_game", 2))]
    for name, case in (("C all strata with corrections", C), ("A zero-count game stratum (3 blocks)", A),
                       ("B zero-hour block with 1 correction", B)):
        tool = capture.phi_hat(case)[0]
        phi, chi2, n, p = glm_phi(case)
        same = tool is not None and phi is not None and abs(tool - phi) < 1e-3
        check("phi %s" % name, same, "tool %s | GLM %.4f = chi2 %.4f / (n %d - p %d)" % (tool, phi, chi2, n, p))
    # B by hand: excluding the 0-hour block, rate = 2/2 = 1, chi2 = 1 + 1 = 2, df 1 -> 2.0; the tool uses rate 3/2.
    print("RESULT: case B by hand: rate over the fitted blocks 2/2=1 -> phi 2.0000; tool's stratum rate uses 3 "
          "corrections over 2 h (1.5/h) -> (0-1.5)^2/1.5 + (2-1.5)^2/1.5 = 1.6667")

    # ---- 3b. end to end: two game block-strata with no people correction
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record2, _ = capture.capture(*three_evenings(game_people=False).write(tmp))
    ps = record2["exposure"]["per_session"]
    phi, chi2, n, p = glm_phi(ps)
    check("phi end to end, game blocks without people corrections", record2["exposure"]["phi_hat"] == round(phi, 4),
          "per_session %s | tool phi_hat %s (%s) | GLM %.4f = %.4f / (%d - %d)" % (
              [(s["session_id"], s["occupied_h"], s["corrections"]) for s in ps], record2["exposure"]["phi_hat"],
              record2["exposure"]["phi_hat_status"], phi, chi2, n, p))

    # ---- 4. sample-size arithmetic
    rule = json.loads((ROOT / "tools" / "correction-capture" / "cph-budget-v0.json").read_text("utf-8"))
    k = (1.959964 + 0.841621) ** 2 * (1 + 0.7) / 0.3 ** 2
    c = rule["constants"]
    check("148 constant", abs(k - c["hours_constant"]) < 0.5, "(z.975 + z.80)^2 * 1.7 / 0.09 = %.2f vs %s" % (
        k, c["hours_constant"]))
    h1 = capture.hours_per_arm(1.0, c)
    h2 = capture.hours_per_arm(1.0, dict(c, phi=2))
    check("ladder 150 h at phi 1, 300 h at phi 2 (rate 1/h)", abs(h1 - 150) < 5 and abs(h2 - 300) < 10,
          "hours_per_arm(1/h) = %s at phi 1, %s at phi 2" % (h1, h2))
    print("RESULT: %d checks, %d pass, %d fail" % (len(results), sum(results), len(results) - sum(results)))


if __name__ == "__main__":
    main()
