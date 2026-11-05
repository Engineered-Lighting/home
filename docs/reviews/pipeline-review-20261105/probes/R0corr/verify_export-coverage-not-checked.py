"""Verifier probe for export-coverage-not-checked (R0corr, correction-capture).

What it checks
  The README extends the window by giving each kind of export more than once. This builds two synthetic export
  windows (old: evenings 1-2, new: evenings 3-4). Each evening: den_lighting turns a bulb on (2 s transition), its
  fade report opens a provisional hold that read-back ends, and a late id-less report of its own 7 s after the
  command (at the command's target) that den_lighting took for a touch (the pattern test_a_fade_alone_counts_nothing
  encodes), plus one real HA-app touch. It runs capture.main() (the owner's CLI) three ways:
    full         : --status old new --lights old new --commands old new
    stale-cmds   : --status old new --lights old new --commands old          (newer command export left out)
    empty-cmds   : --status old new --lights old new --commands <empty file>
  and prints the people count, exit status, and whether the printed summary mentions the exports' coverage.

How to run (synthetic data, temp dirs only, no host contacted):
    python3 -B verify_export-coverage-not-checked.py /path/to/correction-capture/checkout
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
MID = {"on": True, "brightness": 60, "kelvin": 2700}
T0 = datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc).timestamp()


def window(days, n0):
    status, bulbs, commands = [], [], []
    st = {"rev": 50 + n0, "n": n0 * 100, "holds": {}, "prov": []}

    def row(t, occ="on"):
        st["rev"] += 1
        status.append({"last_updated_ts": t, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                       "attributes": {"automatic": True, "gaming_session": False, "occupancy": occ,
                                      "revision": st["rev"], "occupancy_source": "raw_person_count",
                                      "holds": {e: {"until": "room_vacant_2_minutes", "target": s}
                                                for e, s in st["holds"].items()},
                                      "provisional_fixtures": list(st["prov"]), "pending_fixtures": []}})

    def bulb(t, e, on, user=False):
        st["n"] += 1
        c = "%032X" % (0x0199000000000000000000000000 + st["n"])
        bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": c,
                      "context_user_id": "0C" * 16 if user else "", "context_parent_id": ""})
        return c

    for d in days:
        s = T0 + d * 86400
        st["holds"], st["prov"] = {}, []
        row(s - 600, occ="off")
        row(s)
        for k, e in enumerate(LIGHTS[:2]):
            c = s + 60 + k * 1800
            ctx = bulb(c - 0.3, e, True)
            commands.append({"time_fired_ts": c, "event_data": {"entity_id": e, "revision": st["rev"],
                                                                "target": WARM, "context_id": sources.ulid(ctx)}})
            bulb(c + 1.0, e, True)            # fade report
            st["holds"][e], st["prov"] = MID, [e]
            row(c + 1.05)
            st["holds"].pop(e)
            st["prov"] = []
            row(c + 6.0)                       # read-back matched: provisional hold ends
            bulb(c + 7.0, e, True)            # late own report at the target, taken for a touch
            st["holds"][e] = WARM
            row(c + 7.05)
            st["holds"].pop(e)
            row(c + 900)
        st["holds"][LIGHTS[3]] = MID          # one real HA-app touch
        row(s + 5400)
        bulb(s + 5400.3, LIGHTS[3], True, user=True)
        st["holds"] = {}
        row(s + 3 * 3600, occ="off")
        row(s + 3 * 3600 + 300, occ="off")
    return status, bulbs, commands


def write(folder, name, rows):
    p = Path(folder) / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
    return str(p)


def main():
    with tempfile.TemporaryDirectory(prefix="vfy-cover-") as tmp:
        old, new = window((0, 1), 0), window((2, 3), 1)
        f = {}
        for tag, w in (("old", old), ("new", new)):
            for i, kind in enumerate(("status", "lights", "commands")):
                f[kind + "-" + tag] = write(tmp, "%s-%s.jsonl" % (kind, tag), w[i])
        f["empty"] = write(tmp, "commands-empty.jsonl", [])
        runs = (("full", [f["commands-old"], f["commands-new"]]), ("stale-cmds", [f["commands-old"]]),
                ("empty-cmds", [f["empty"]]))
        base = None
        for i, (label, cmds) in enumerate(runs):
            argv = ["--status", f["status-old"], f["status-new"], "--lights", f["lights-old"], f["lights-new"],
                    "--commands"] + cmds + ["--out", str(Path(tmp) / ("out-%d" % i))]
            buf = io.StringIO()
            code = 0
            try:
                with redirect_stdout(buf):
                    capture.main(argv)
            except SystemExit as e:
                code = e.code
            except ValueError as e:
                code = "ValueError: %s" % e
            text = buf.getvalue()
            rec = json.loads((Path(tmp) / ("out-%d" % i) / "exposure.json").read_text()) if code == 0 else None
            people = rec["planning"]["strata"]["no_game"]["people"]["count"] if rec else None
            hours = rec["planning"]["strata"]["no_game"]["occupied_h"] if rec else None
            base = base if base is not None else people
            mentions = [w for w in ("command", "bulb", "cover", "warn") if w in text.lower()]
            print("%s: %-10s exit %s, people %s over %s h, by source %s; summary mentions %s" % (
                "PASS" if people == base or code != 0 else "FAIL", label, code, people, hours,
                rec["planning"]["strata"]["no_game"]["actions_by_source"] if rec else None, mentions))
        print("RESULT: the full run counts %s people (the 4 HA-app touches)" % base)


if __name__ == "__main__":
    main()
