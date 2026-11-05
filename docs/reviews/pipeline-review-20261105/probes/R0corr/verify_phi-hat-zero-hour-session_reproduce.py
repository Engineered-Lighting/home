"""verify_phi-hat-zero-hour-session_reproduce (REPRODUCE lens): independent re-check of the finding that
capture.phi_hat sums each stratum's totals over every per_session entry (hours and corrections) but takes the Pearson
sum only over sessions with occupied_h > 0, so a session with corrections and 0.0 hours inflates the fitted rate.

Part 1: phi_hat on hand-made per_session lists, against a one-rate-per-stratum Poisson fit over the sessions that can
carry a log(occupied_h) offset (h > 0), whose MLE rate is sum(c)/sum(h) over those same sessions.
Part 2: end to end through capture.capture: can a real-shaped export yield such a session? Variants:
  (a) the last status row both turns occupancy on and shows a new switch hold;
  (b) the same row, followed by an HA restart (unavailable row, revision reset, occupancy unknown);
  (c) occupancy on in its own row 30 s before the hold row (the usual shape), last row after the hold.
Synthetic data only, temp dirs only, never contacts a host.
Run:  python3 -B verify_phi-hat-zero-hour-session_reproduce.py <root of the claude/correction-capture checkout>
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import events  # noqa: E402

L = events.LIGHTS
STATUS = "sensor.den_lighting_control"
OFF = {"on": False, "brightness": None, "kelvin": None}
T0 = datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc).timestamp()


def reference(per_session):
    """One rate per stratum, fitted and summed over the same sessions (h > 0, stratum with corrections)."""
    rows = [s for s in per_session if s["occupied_h"] > 0]
    tot = {}
    for s in rows:
        h, c = tot.get(s["mode_stratum"], (0.0, 0))
        tot[s["mode_stratum"]] = (h + s["occupied_h"], c + s["corrections"])
    rows = [s for s in rows if tot[s["mode_stratum"]][1] > 0]
    strata = {s["mode_stratum"] for s in rows}
    if len(rows) - len(strata) < 1:
        return None
    x2 = 0.0
    for s in rows:
        h, c = tot[s["mode_stratum"]]
        mu = s["occupied_h"] * c / h
        x2 += (s["corrections"] - mu) ** 2 / mu
    return round(x2 / (len(rows) - len(strata)), 4)


def S(h, c):
    return {"occupied_h": h, "corrections": c, "mode_stratum": "no_game"}


for name, sessions in (("finding's example", [S(1.0, 1), S(1.0, 1), S(0.0, 2)]),
                       ("no zero-hour session", [S(1.0, 1), S(1.0, 1)]),
                       ("zero-hour session, 1 correction", [S(3.0, 2), S(2.0, 1), S(0.0, 1)])):
    tool, ref = capture.phi_hat(sessions)[0], reference(sessions)
    print("%s part1 %-34s tool=%s reference=%s" % ("PASS" if tool == ref else "FAIL", name, tool, ref))

# ---------------------------------------------------------------- part 2
rev = [100]


def srow(rows, t, occ, holds, revision=None):
    rev[0] = rev[0] + 1 if revision is None else revision
    attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
             "occupancy": occ, "revision": rev[0], "error": None, "gaming_entity": "sensor.pc_gaming",
             "holds": {e: {"until": "room_vacant_2_minutes", "target": OFF} for e in holds},
             "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": [],
             "friendly_name": "Den lighting control"}
    rows.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})


def switch(bulbs, t, e):
    bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "off",
                  "context_id": "%032X" % (0x0190000000000000000000000000 + len(bulbs) + 1),
                  "context_user_id": "", "context_parent_id": ""})


def export(variant):
    rev[0] = 100
    status, bulbs = [], []
    for day, (hours, count) in enumerate([(3.0, 2), (2.0, 1)]):  # two ordinary evenings
        start = T0 + day * 86400
        srow(status, start, "on", [])
        for k in range(count):
            t = start + (k + 1) * hours * 3600 / (count + 1)
            switch(bulbs, t, L[k])
            srow(status, t + 0.01, "on", [L[k]])
            srow(status, t + 60, "on", [])
        srow(status, start + hours * 3600, "off", [])
    start = T0 + 2 * 86400  # third evening: the export ends early in it
    srow(status, start, "off", [])
    if variant == "c":
        srow(status, start + 570, "on", [])
    switch(bulbs, start + 600, L[2])
    srow(status, start + 600.01, "on", [L[2]])
    if variant == "b":
        status.append({"last_updated_ts": start + 700, "entity_id": STATUS, "state": "unavailable",
                       "attributes": {"restored": True, "friendly_name": "Den lighting control"}})
        srow(status, start + 760, "unknown", [], revision=1)
        srow(status, start + 900, "off", [])
    return status, bulbs


for variant in ("a", "b", "c"):
    status, bulbs = export(variant)
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, rows in (("s", status), ("b", bulbs), ("c", [])):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        record, _ = capture.capture(*paths)
    per = record["exposure"]["per_session"]
    zero = [s for s in per if s["occupied_h"] == 0 and s["corrections"] > 0]
    tool, ref = record["exposure"]["phi_hat"], reference(per)
    print("RESULT part2 variant %s per_session=%s" % (variant, [(s["session_id"], s["occupied_h"], s["corrections"])
                                                              for s in per]))
    print("%s part2 variant %s zero-hour sessions with corrections=%d phi_hat tool=%s reference=%s" % (
        "FAIL" if tool != ref else "PASS", variant, len(zero), tool, ref))
