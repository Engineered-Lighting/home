"""Verifier probe for phi-hat-not-glm-pearson (R0corr, correction-capture).

What it checks
  Part A: phi_hat leaves out strata with no corrections. A quasi-Poisson GLM with one rate per stratum fits rate 0
  for such a stratum; its rows have Pearson residual 0 (mu -> 0) whatever the true dispersion, yet a naive GLM still
  counts them in n - p. The probe shows how that naive df behaves as all-zero game blocks are added (it drives phi
  toward 0), to judge whether the tool's exclusion is a defect or a defensible convention.
  Part B: end to end through capture.capture(), a block whose only in-charge row is a correction followed by the
  export's end gets occupied_h 0 but corrections 1. phi_hat() drops the block from the Pearson sum but keeps its
  correction in the stratum's rate. The probe compares that with the same fit after dropping the block entirely.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B verify_phi-hat-not-glm-pearson.py /path/to/correction-capture/checkout
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

L0, L1 = "light.den_front_left", "light.front_right"
OFF = {"on": False, "brightness": None, "kelvin": None}
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def pearson(blocks):
    """Pearson dispersion with one rate per stratum, all strata with h > 0 counted in n - p (naive GLM df)."""
    tot = {}
    for s, h, c in blocks:
        H, C = tot.get(s, (0.0, 0))
        tot[s] = (H + h, C + c)
    chi2 = 0.0
    for s, h, c in blocks:
        H, C = tot[s]
        mu = h * C / H
        if mu > 0:
            chi2 += (c - mu) ** 2 / mu
    return chi2, len(blocks), len(tot)


def part_a():
    ng = [("no_game", 2.0, c) for c in (0, 3, 1, 4)]
    tool = capture.phi_hat([{"mode_stratum": s, "occupied_h": h, "corrections": c} for s, h, c in ng])[0]
    for k in (0, 1, 3, 10, 50):
        blocks = ng + [("game", 1.0, 0)] * k
        chi2, n, p = pearson(blocks)
        t = capture.phi_hat([{"mode_stratum": s, "occupied_h": h, "corrections": c} for s, h, c in blocks])[0]
        print("RESULT: part A, %2d all-zero game blocks -- tool %.4f | naive GLM df %.4f = %.4f / (%d - %d)"
              % (k, t, chi2 / (n - p), chi2, n, p))
    return tool


def part_b():
    status, bulbs = [], []
    st = {"rev": 10, "n": 0}

    def row(t, occ, holds):
        st["rev"] += 1
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": {"automatic": True, "gaming_session": False, "occupancy": occ,
                                      "revision": st["rev"], "occupancy_source": "raw_person_count",
                                      "holds": {e: {"until": "room_vacant_2_minutes", "target": s}
                                                for e, s in holds.items()},
                                      "provisional_fixtures": [], "pending_fixtures": []}})

    def bulb(t, e, on):
        st["n"] += 1
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                      "context_id": "%032X" % (0x019A000000000000000000000000 + st["n"]),
                      "context_user_id": "", "context_parent_id": ""})

    # Days 1-3: 2 h occupied each, corrections 0, 2, 1 (switch, id-less rows).
    for d, n in enumerate((0, 2, 1)):
        s = T0 + d * 86400
        row(s - 300, "off", {})
        row(s, "on", {})
        holds = {}
        for i in range(n):
            e = (L0, L1)[i]
            bulb(s + 600 + i * 1200, e, False)
            holds[e] = OFF
            row(s + 600.01 + i * 1200, "on", dict(holds))
        row(s + 7200, "off", {})
    # Day 4: occupancy turns on in the same status write that shows a new switch hold, and the export ends there.
    s = T0 + 3 * 86400
    row(s - 300, "off", {})
    bulb(s - 0.5, L0, False)
    row(s, "on", {L0: OFF})
    with tempfile.TemporaryDirectory(prefix="vfy-phi-") as tmp:
        paths = []
        for name, rows in (("status", status), ("lights", bulbs), ("commands", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    ps = record["exposure"]["per_session"]
    print("RESULT: part B per_session %s" % [(x["session_id"], x["occupied_h"], x["corrections"]) for x in ps])
    kept = [("no_game", x["occupied_h"], x["corrections"]) for x in ps if x["occupied_h"] > 0]
    chi2, n, p = pearson(kept)
    ok = abs(record["exposure"]["phi_hat"] - round(chi2 / (n - p), 4)) < 1e-4
    print("%s: part B tool phi_hat %s (%s) | fit on the blocks with hours only %.4f = %.4f / (%d - %d); "
          "corrections_total %s" % ("PASS" if ok else "FAIL", record["exposure"]["phi_hat"],
                                    record["exposure"]["phi_hat_status"].split(";")[0], chi2 / (n - p), chi2, n, p,
                                    record["exposure"]["corrections_total"]))


def main():
    part_a()
    part_b()


if __name__ == "__main__":
    main()
