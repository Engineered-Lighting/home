"""probe_status_span_loses_arrival_and_vacancy: run.status_span with --facts drops the evening's edges.

What it checks
  README: "Add --facts ... the status is then cut to the span the facts replay. The latest occupancy and
  live_status before the span are carried to its start, or to 150 s before it when older, so an empty den is
  still empty long enough for den_lighting to take the next arrival as one."
  The perceiver's facts are occupancy-gated (perception-lab la_live_perceiver: frames are taken only while the
  gate is open), so the first fact comes seconds AFTER the arrival. status_span then carries only that arrival's
  'on' and drops the long 'off' before it, so the replayed den_lighting starts unknown -> on: no arrival, no
  last-level ON, normal seeded OFF from the carried bulb rows. Status items after the last fact (the evening's
  final vacancy, which follows the last fact) are dropped too, so the replay never sends the vacancy OFF.
  Control: the same exports replayed with --status alone reproduce den_lighting's commands.

How to run (synthetic data only, temp folders, no host contacted):
  python3 -B probe_status_span_loses_arrival_and_vacancy.py /path/to/branch/checkout
Prints PASS/FAIL per check and RESULT.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import synthetic  # noqa: E402  (fact lines in the real la-fact-log-1 format)
import events  # noqa: E402

T0 = synthetic.T0
LIGHTS = events.LIGHTS
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
STATUS = "sensor.den_lighting_control"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(hex_bytes):
    n = int.from_bytes(bytes.fromhex(hex_bytes), "big")
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


class Export:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.n, self.revision = [], [], [], 0, 100
        self.now = {"automatic": True, "gaming_session": False, "occupancy": "off", "holds": {}}

    def context(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t, **change):
        self.now.update(change)
        self.revision += 1
        attrs = {**self.now, "gaming_enabled": True, "gaming_signal": "idle", "revision": self.revision,
                 "error": None, "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count",
                 "pending_fixtures": [], "provisional_fixtures": [], "friendly_name": "Den lighting control",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()}}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, e, on):
        c = self.context()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c,
                           "context_user_id": "", "context_parent_id": ""})
        return c

    def command(self, t, e, target):
        c = self.bulb(t - 0.3, e, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.revision,
                                                                 "target": target, "context_id": ulid(c)}})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("bulbs", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / ("den-%s.jsonl" % name)
            p.write_bytes("".join(json.dumps(r) + "\n" for r in rows).encode("ascii"))
            out.append(p)
        return out


def run(tmp, name, argv):
    p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/run.py"), *argv, "--policies", "incumbent",
                        "--out", str(tmp / name)], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("run.py failed: %s" % p.stderr)
    header, log = events.read(tmp / name / "log.jsonl")
    return p.stdout, log


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-span-"))
    try:
        _, _, fact_lines = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0)
        facts = tmp / "la-fact-log-1-2026-11-04.jsonl"
        facts.write_text("".join(line + "\n" for line in fact_lines), "ascii")
        first_fact = min(synthetic.fact_time(json.loads(l)["available_at"]) for l in fact_lines)
        last_fact = max(synthetic.fact_time(json.loads(l)["available_at"]) for l in fact_lines)
        print("facts: %d lines, %+.1f s .. %+.1f s from T0" % (len(fact_lines), first_fact - T0, last_fact - T0))

        x = Export()
        for e in LIGHTS:
            x.bulb(T0 - 3700, e, False)        # dark after the last vacancy OFF
        x.row(T0 - 3600)                       # vacant for an hour
        x.row(first_fact - 5, occupancy="on")  # A arrives; the occupancy-gated perceiver's first fact follows
        for e in LIGHTS:
            x.command(first_fact - 4.5, e, LIT)  # den_lighting's arrival (last-level) ON
        x.row(last_fact + 3, occupancy="off")  # A leaves: the gate closes, the last fact was just before
        for e in LIGHTS:
            x.command(last_fact + 123.5, e, OFF)  # den_lighting's vacancy OFF after 120 s
        status, bulbs, commands = x.write(tmp)
        common = ["--status", str(status), "--lights", str(bulbs), "--live-commands", str(commands)]

        out_a, log_a = run(tmp, "status-alone", common)
        out_b, log_b = run(tmp, "with-facts", ["--facts", str(facts)] + common)

        def summary(log):
            dec = [(round(e["t"] - T0, 1), sorted({s["on"] for s in e["data"]["requested_scene"].values()}),
                    len(e["data"]["requested_scene"])) for e in log
                   if e["kind"] == "decision" and e["source"] == "incumbent"]
            occ = [(round(e["t"] - T0, 1), e["data"]["value"]) for e in log if e["kind"] == "occupancy"]
            return dec, occ

        dec_a, occ_a = summary(log_a)
        dec_b, occ_b = summary(log_b)
        print("--status alone: occupancy %s" % occ_a)
        print("  incumbent decisions (t, on-values, bulbs): %s" % dec_a)
        print("  " + [l for l in out_a.splitlines() if l.startswith("diff live")][0])
        print("--facts + --status: occupancy %s" % occ_b)
        print("  incumbent decisions (t, on-values, bulbs): %s" % dec_b)
        for l in out_b.splitlines():
            if l.startswith("diff live") or l.startswith("  front") or l.startswith("  rear") or l.startswith("recorder_commands"):
                print("  " + l)

        on_a = any(True in ons for _, ons, _ in dec_a)
        on_b = any(True in ons for _, ons, _ in dec_b)
        off_b = any(ons == [False] for _, ons, _ in dec_b)
        lost_off = all(v != "off" for _, v in occ_b)
        c1 = on_a and not on_b
        print("%s: the arrival ON den_lighting sent live is replayed with --status alone (%s) but not with --facts (%s)"
              % ("PASS" if c1 else "FAIL", on_a, on_b))
        c2 = lost_off and not off_b
        print("%s: with --facts the final vacancy is dropped (no 'off' occupancy: %s, no vacancy OFF: %s)"
              % ("PASS" if c2 else "FAIL", lost_off, not off_b))
        print("RESULT: %s" % ("DEFECT REPRODUCED" if c1 and c2 else "PARTLY REPRODUCED" if c1 or c2
                              else "NOT REPRODUCED"))
        return 0 if c1 or c2 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
