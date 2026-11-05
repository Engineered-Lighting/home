"""Verifier probe for within-budget-ignores-phi-hat and measurement-touches-counted-as-people (R0corr).

What it checks
  1) within-budget-ignores-phi-hat: 8 evenings of 5 occupied hours with 40 switch corrections bunched 0,0,10,0,12,0,
     8,10. Runs capture.main() (the owner's CLI) and prints the verdict, phi_hat, hours_per_arm_at_phi_hat from
     exposure.json, and the summary lines the owner sees: does the summary say the verdict keeps phi = 1, and does
     it print the hours at phi_hat?
  2) measurement-touches-counted-as-people: a rung 7/8-style measurement visit with den_lighting automatic and the
     owner in the den (occupancy on): the owner turns one more bulb on at full in the HA app, then dims one with a
     switch ('too bright: dim a little'). Prints how the tool counts them, and whether the CLI takes any exclusion.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B verify_within-budget-and-measurement.py /path/to/correction-capture/checkout
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
FULL = {"on": True, "brightness": 255, "kelvin": 2700}
DIM = {"on": True, "brightness": 150, "kelvin": 2700}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


class X:
    def __init__(self):
        self.status, self.bulbs, self.rev, self.n, self.holds = [], [], 10, 0, {}

    def row(self, t, occ="on"):
        self.rev += 1
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                            "attributes": {"automatic": True, "gaming_session": False, "occupancy": occ,
                                           "revision": self.rev, "occupancy_source": "raw_person_count",
                                           "holds": {e: {"until": "room_vacant_2_minutes", "target": s}
                                                     for e, s in self.holds.items()},
                                           "provisional_fixtures": [], "pending_fixtures": []}})

    def bulb(self, t, e, on, user=False):
        self.n += 1
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": "%032X" % (0x019B000000000000000000000000 + self.n),
                           "context_user_id": "0E" * 16 if user else "", "context_parent_id": ""})

    def run(self, tmp, tag):
        paths = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", [])):
            p = Path(tmp) / ("%s-%s.jsonl" % (tag, name))
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append(str(p))
        out = Path(tmp) / ("out-" + tag)
        buf = io.StringIO()
        with redirect_stdout(buf):
            capture.main(["--status", paths[0], "--lights", paths[1], "--commands", paths[2], "--out", str(out)])
        return json.loads((out / "exposure.json").read_text()), buf.getvalue(), paths


def budget(tmp):
    x = X()
    for d, k in enumerate((0, 0, 10, 0, 12, 0, 8, 10)):
        s = T0 + d * 86400
        x.holds = {}
        x.row(s - 300, "off")
        x.row(s)
        for i in range(k):
            e = LIGHTS[i % 4]
            new = OFF if x.holds.get(e) == WARM else WARM
            x.bulb(s + 600 + i * 600, e, new["on"])
            x.holds[e] = new
            x.row(s + 600.01 + i * 600)
        x.holds = {}
        x.row(s + 5 * 3600, "off")
    x.row(T0 + 8 * 86400, "off")
    rec, text, _ = x.run(tmp, "budget")
    ng = rec["planning"]["strata"]["no_game"]
    print("RESULT: budget: verdict %s; people %s over %s h, interval %s; phi_hat %s; hours_per_arm_at_phi_hat %s"
          % (ng["verdict"], ng["people"]["count"], ng["occupied_h"], ng["people"]["interval"],
             rec["exposure"]["phi_hat"], ng["hours_per_arm_at_phi_hat"]))
    for line in text.splitlines():
        if "verdict" in line or "phi_hat" in line:
            print("RESULT: summary line: %s" % line.strip()[:200])
    shown = str(ng["hours_per_arm_at_phi_hat"]) in text
    print("%s: summary prints hours_per_arm_at_phi_hat (%s): %s; summary says the verdict keeps phi = 1: %s" % (
        "PASS" if shown else "FAIL", ng["hours_per_arm_at_phi_hat"], shown, "keeps phi = 1" in text))


def measurement(tmp):
    x = X()
    s = T0
    x.row(s - 300, "off")
    x.row(s)                                  # the owner walks in: occupancy on, den_lighting automatic
    x.holds[LIGHTS[1]] = FULL                 # 'turn on one more den bulb ... at full in the HA app'
    x.row(s + 120)
    x.bulb(s + 120.3, LIGHTS[1], True, user=True)
    x.bulb(s + 400, LIGHTS[0], True)          # 'too bright: dim a little' with a switch or dimmer
    x.holds[LIGHTS[0]] = DIM
    x.row(s + 400.01)
    x.holds = {}
    x.row(s + 1800, "off")
    x.row(s + 2100, "off")
    rec, _, paths = x.run(tmp, "meas")
    ng = rec["planning"]["strata"]["no_game"]
    print("RESULT: measurement visit: people %s, by source %s, occupied_h %s" % (
        ng["people"]["count"], ng["actions_by_source"], ng["occupied_h"]))
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            capture.main(["--status", paths[0], "--lights", paths[1], "--commands", paths[2],
                          "--out", str(Path(tmp) / "out-ex"), "--exclude", "2026-10-29T02:00Z/02:30Z"])
        took = True
    except SystemExit:
        took = False
    print("%s: measurement settings counted as people: %s; CLI accepts an exclusion span: %s (%s)" % (
        "FAIL" if ng["people"]["count"] == 2 and not took else "PASS", ng["people"]["count"], took,
        err.getvalue().strip().splitlines()[-1] if err.getvalue() else ""))


def main():
    with tempfile.TemporaryDirectory(prefix="vfy-budget-") as tmp:
        budget(tmp)
        measurement(tmp)


if __name__ == "__main__":
    main()
