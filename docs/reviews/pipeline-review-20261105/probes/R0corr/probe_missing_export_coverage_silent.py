"""R0corr probe: an empty or shorter bulb or command export changes who made each hold, silently.

What it checks
  correction-capture attributes each hold from the bulbs' rows and sets aside den_lighting's own rows by the
  command export's context ids and times. Nothing checks that the bulb and command exports cover the status
  export's span. In PowerShell 5.1 a failed `$sql | ssh ... | Set-Content` (for example sqlite3 not found, or a
  wrong database path, printed on the console) still leaves an empty file, and the README tells the owner to extend
  the window later by giving "each kind of file more than once", which makes it easy to add a newer status export
  without the matching bulb or command export. The probe runs one synthetic evening four ways:
    full      : all three exports
    no-cmds   : the command export empty
    no-bulbs  : the bulb export empty
    half-cmds : the command export stops after den_lighting's third write (an older export)
  and prints people / every-hold counts, the verdict inputs, and whether the tool refused or warned.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B probe_missing_export_coverage_silent.py /path/to/branch/checkout
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "correction-capture"))
import capture  # noqa: E402
import sources  # noqa: E402

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
WARM = {"on": True, "brightness": 100, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def history():
    status, bulbs, commands, rev, holds, n = [], [], [], [20], {}, [0]

    def row(t):
        rev[0] += 1
        attrs = {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": rev[0],
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in holds.items()},
                 "provisional_fixtures": [], "occupancy_source": "raw_person_count", "pending_fixtures": []}
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": attrs})

    def bulb(t, e, on, user=False):
        n[0] += 1
        context = "%032X" % (0x0198 << 100 | n[0])
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": context,
                      "context_user_id": "0B" * 16 if user else "", "context_parent_id": ""})
        return context

    row(T0)
    # Six times in the evening den_lighting writes a bulb and then takes its own (late) write for a touch:
    # its own state row (its context) and a hold, with no person anywhere near.
    for i in range(6):
        t = T0 + 600 + i * 1200
        e = LIGHTS[i % 4]
        ctx = bulb(t - 0.3, e, True)
        commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": rev[0], "target": WARM,
                                                            "context_id": sources.ulid(ctx)}})
        holds[e] = DIM
        row(t + 0.05)
        holds.pop(e)
        row(t + 300)
    # Two real app touches.
    for i, t in enumerate((T0 + 9000, T0 + 12000)):
        holds[LIGHTS[i]] = WARM if i else DIM
        row(t)
        bulb(t + 0.3, LIGHTS[i], True, user=True)
    row(T0 + 6 * 3600)
    return status, bulbs, commands


def run(status, bulbs, commands):
    with tempfile.TemporaryDirectory(prefix="r0corr-") as tmp:
        paths = []
        for name, rows in (("status", status), ("lights", bulbs), ("commands", commands)):
            p = Path(tmp) / (name + ".jsonl")
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
            paths.append([p])
        try:
            record, _ = capture.capture(*paths)
        except ValueError as error:
            return None, "refused: %s" % error
        with redirect_stdout(io.StringIO()) as out:
            print(capture.summary(record))
        return record, out.getvalue()


def main():
    status, bulbs, commands = history()
    half = T0 + 4200  # after den_lighting's third write: a command export that stops early
    variants = (("full", bulbs, commands), ("no-cmds", bulbs, []), ("no-bulbs", [], commands),
                ("half-cmds", bulbs, [c for c in commands if c["time_fired_ts"] < half]))
    base = None
    for name, b, c in variants:
        record, text = run(status, b, c)
        if record is None:
            print("PASS: %s -- %s" % (name, text))
            continue
        ng = record["planning"]["strata"]["no_game"]
        got = (ng["people"]["count"], ng["every_hold"]["count"])
        base = base or got
        warned = any(w in text.lower() for w in ("warn", "missing", "empty", "cover"))
        ok = got == base or warned
        print("%s: %s -- people %d, every hold %d, by source %s; span bulb_rows %d commands %d; warned=%s" % (
            "PASS" if ok else "FAIL", name, got[0], got[1], ng["actions_by_source"], record["span"]["bulb_rows"],
            record["span"]["commands"], warned))
    print("RESULT: full run counts 2 people; any other count printed above came with no refusal or warning")


if __name__ == "__main__":
    main()
