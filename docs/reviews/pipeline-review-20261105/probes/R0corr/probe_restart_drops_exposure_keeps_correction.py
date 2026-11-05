"""R0corr probe: an HA restart drops the exposure after the last status row before it, but a correction at that
row is still counted, so the block's rate and phi_hat are computed over mismatched time.

What it checks
  Six identical evenings, 3 occupied hours each, no game. Evenings 1-5 have one switch correction each, 30 minutes
  in. Evening 6 has one app correction 5 minutes in, and HA restarts 3 hours later (den_lighting's status sensor
  shows 'unavailable', then a row with a low revision), the den still occupied. Nothing else changes the status in
  between, as in a steady evening. correction-capture drops the time from the correction's row to the first row
  after the restart (walk(): lost += t - prev_t) yet counts the correction made at that row. The probe runs the
  same six evenings without the restart and compares per_session, the stratum rate and phi_hat.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_restart_drops_exposure_keeps_correction.py /path/to/branch/checkout
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
WARM = {"on": True, "brightness": 100, "kelvin": 2700}


class Ex:
    def __init__(self):
        self.status, self.bulbs, self.rev, self.n = [], [], 300, 0
        self.now = {"occupancy": "off", "holds": {}}

    def ctx(self):
        self.n += 1
        return "%032X" % (0x0195000000000000000000000000 + self.n)

    def row(self, t, revision=None, **change):
        self.now.update(change)
        self.rev = self.rev + 1 if revision is None else revision
        attrs = {"automatic": True, "gaming_enabled": True, "gaming_session": False, "gaming_signal": "idle",
                 "occupancy": self.now["occupancy"], "revision": self.rev, "error": None,
                 "gaming_entity": "sensor.pc_gaming",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()},
                 "occupancy_source": "raw_person_count", "pending_fixtures": [], "provisional_fixtures": []}
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                            "attributes": attrs})

    def unavailable(self, t):
        self.status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "unavailable",
                            "attributes": {"restored": True, "friendly_name": "Den lighting control"}})

    def bulb(self, t, e, on, user=False):
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": self.ctx(), "context_user_id": "0D" * 16 if user else "",
                           "context_parent_id": ""})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", [])):
            p = Path(folder) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            out.append([p])
        return out


def history(restart):
    ex = Ex()
    for day in range(6):
        t = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp() + day * 86400
        ex.row(t - 900)
        ex.row(t, occupancy="on")
        if day < 5:
            ex.bulb(t + 1800, LIGHTS[0], False)
            ex.row(t + 1800.01, holds={LIGHTS[0]: OFF})
        else:
            ex.row(t + 300, holds={LIGHTS[1]: WARM})
            ex.bulb(t + 300.3, LIGHTS[1], True, user=True)
            if restart:
                ex.unavailable(t + 3 * 3600)
                ex.row(t + 3 * 3600 + 30, revision=2)   # den_lighting's revision starts again after the restart
        ex.row(t + 3 * 3600 + 60, occupancy="off", holds={})
        ex.row(t + 3 * 3600 + 300)
    return ex


def run(restart):
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        record, _ = capture.capture(*history(restart).write(tmp))
    ng = record["planning"]["strata"]["no_game"]
    return record, ng


def main():
    base, ng0 = run(False)
    rst, ng1 = run(True)
    last0 = base["exposure"]["per_session"][-1]
    last1 = rst["exposure"]["per_session"][-1]
    print("RESULT: no restart : evening 6 %s h, %s correction; stratum %s h, %s people, %s per h; phi_hat %s" % (
        last0["occupied_h"], last0["corrections"], ng0["occupied_h"], ng0["people"]["count"],
        ng0["people"]["per_occupied_h"], base["exposure"]["phi_hat"]))
    print("RESULT: restart    : evening 6 %s h, %s correction; stratum %s h, %s people, %s per h; phi_hat %s; "
          "restart_gap_h %s" % (last1["occupied_h"], last1["corrections"], ng1["occupied_h"], ng1["people"]["count"],
                                ng1["people"]["per_occupied_h"], rst["exposure"]["phi_hat"],
                                rst["planning"]["restart_gap_h"]))
    counted_with_no_following_time = last1["corrections"] == 1 and last1["occupied_h"] < 0.2
    print("%s: the correction at the row before the restart is counted while the %.2f h after it are dropped" % (
        "FAIL" if counted_with_no_following_time else "PASS", rst["planning"]["restart_gap_h"]))
    ratio = rst["exposure"]["phi_hat"] / base["exposure"]["phi_hat"] if base["exposure"]["phi_hat"] else float("inf")
    print("%s: phi_hat moves from %s to %s (x%.1f) because of one restart" % (
        "FAIL" if ratio > 2 else "PASS", base["exposure"]["phi_hat"], rst["exposure"]["phi_hat"], ratio))


if __name__ == "__main__":
    main()
