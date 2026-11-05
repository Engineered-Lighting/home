"""probe_live_diff_vacuous_when_all_left_out: the live-vs-replay diff reports 0 intervals when it compared nothing.

What it checks
  With --status, run.live_diff leaves out every instant whose latest live_status is not {automatic: true,
  gaming: false} -- including {automatic: null, gaming: null}, which den_lighting_status emits for every row
  without status attributes. The summary line reports only the interval count and per-bulb command counts;
  it never says how many seconds were compared. So an export in which nothing could be compared prints
  "...: 0 interval(s)" -- the same text as a perfect replay of den_lighting's commands (rung 0's
  "den_lighting's incumbent Decisions replay too"). Two cases:
    A. every status row lacks den_lighting's attributes (a stale/wrong entity, or the sensor unavailable);
    B. den_lighting shows a game for the whole evening;
  each with live commands the replay never reproduces.

How to run (synthetic data only, temp folders, no host contacted):
  python3 -B probe_live_diff_vacuous_when_all_left_out.py /path/to/branch/checkout
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
T0 = 1793764800.0
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
STATUS = "sensor.den_lighting_control"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(hex_bytes):
    n = int.from_bytes(bytes.fromhex(hex_bytes), "big")
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def build(folder, case):
    status, bulbs, commands, n = [], [], [], [0]

    def ctx():
        n[0] += 1
        return "%032X" % (0x0190000000000000000000000000 + n[0])

    def row(t, occupancy, gaming=False, rev=[100]):
        rev[0] += 1
        if case == "A":
            status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "unavailable",
                           "attributes": {"restored": True, "friendly_name": "Den lighting control"}})
            return
        attrs = {"automatic": True, "gaming_session": gaming, "occupancy": occupancy, "holds": {},
                 "gaming_enabled": True, "gaming_signal": "playing" if gaming else "idle", "revision": rev[0],
                 "error": None, "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count",
                 "pending_fixtures": [], "provisional_fixtures": []}
        status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "gaming" if gaming else "occupancy",
                       "attributes": attrs})

    def bulb(t, e, on):
        c = ctx()
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c,
                      "context_user_id": "", "context_parent_id": ""})
        return c

    def command(t, e, target):
        c = bulb(t - 0.3, e, target["on"])
        commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": 7, "target": target,
                                                            "context_id": ulid(c)}})

    for e in LIGHTS:
        bulb(T0 - 3700, e, False)
    row(T0 - 3600, "off", gaming=(case == "B"))
    row(T0, "on", gaming=(case == "B"))
    for e in LIGHTS:  # live commands the replay will not reproduce
        command(T0 + 0.5, e, LIT)
        command(T0 + 900.5, e, OFF)
        command(T0 + 1800.5, e, LIT)
    row(T0 + 2400, "off", gaming=(case == "B"))
    out = []
    for name, rows in (("status", status), ("bulbs", bulbs), ("commands", commands)):
        p = Path(folder) / ("den-%s.jsonl" % name)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        out.append(p)
    return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="probe-r9-vac-"))
    results = []
    try:
        for case in ("A", "B"):
            folder = tmp / case
            folder.mkdir()
            status, bulbs, commands = build(folder, case)
            p = subprocess.run([sys.executable, "-B", str(ROOT / "tools/den-core/run.py"), "--status", str(status),
                                "--lights", str(bulbs), "--live-commands", str(commands), "--policies", "incumbent",
                                "--out", str(folder / "out")], capture_output=True, text=True)
            print("[case %s] exit=%d" % (case, p.returncode))
            for l in p.stdout.splitlines():
                if l.startswith(("den_lighting_status", "diff live", "recorder_commands", "incumbent (")):
                    print("  " + l)
            diff = [l for l in p.stdout.splitlines() if l.startswith("diff live")]
            kept = re.search(r"recorder_commands: kept (\d+) of (\d+)", p.stdout)
            if case == "A":
                # Every row lacks attributes: one 'unknown' and one null live_status, so the span is one instant.
                vac = p.returncode == 0 and not diff and kept and kept.group(1) == "0"
                print("  %s: exit 0, no live diff at all, %s of %s live commands kept (nothing compared, no warning)"
                      % ("PASS" if vac else "FAIL", kept.group(1) if kept else "?", kept.group(2) if kept else "?"))
            else:
                counts = re.findall(r"(\w+) (\d+)/(\d+)", diff[0].split("commands live/replayed:")[1]) if diff else []
                differ = any(a != b for _, a, b in counts)
                vac = bool(diff) and ": 0 interval(s)" in diff[0] and differ and "compared" not in diff[0]
                print("  %s: '0 interval(s)' while live and replayed command counts differ (%s); no compared time "
                      "is reported" % ("PASS" if vac else "FAIL", ", ".join("%s %s/%s" % c for c in counts)))
            results.append(bool(vac))
        print("RESULT: %s" % ("DEFECT REPRODUCED" if all(results) else "PARTLY REPRODUCED" if any(results)
                              else "NOT REPRODUCED"))
        return 0 if any(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
