"""probe_hold_before_span: a hold the live den_lighting opened before the replayed span, and still held through
it, is dropped by run.py --corrections, so both replayed arms treat the held bulb as free.

What it checks
  tools/den-core/run.py cuts corrections.jsonl to [start, last] of the other inputs (run.py: "corrections = [item
  for item in items if start <= item[0] <= last]") but carries only occupancy and live_status to the start
  (status_span), never the holds den_lighting's status showed there. Ladder rung 9 needs "each evening's hold set
  ... from rung 0's log, and held channels pinned at read-back in both arms".

  Case A: a switch turns L2 (light.den_rear_left) off 600 s before the first fact line; den_lighting's status shows
          the hold from then on and through the whole evening (the den stays occupied). The replay is run the
          README's way: --facts --status --lights --live-commands --corrections --policies need incumbent decide.
  Case B (control): the same switch 60 s after the first fact line.
  Expected by the contract: in both cases decide's reading-look decisions pin L2 at the held OFF and the
  incumbent's mode lists the hold.

How to run
  python3 -B probe_hold_before_span.py /path/to/den-core-live-checkout
  Synthetic data only; everything is written to a temp folder; nothing contacts a host.
Prints PASS/FAIL/RESULT lines.
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
L2 = LIGHTS[2]
STATUS = "sensor.den_lighting_control"
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid_text(n):
    return "".join(CROCK[(n >> (5 * i)) & 31] for i in reversed(range(26)))


class Exports:
    def __init__(self):
        self.status, self.bulbs, self.commands, self.n, self.rev = [], [], [], 0, 10
        self.now = {"automatic": True, "gaming_session": False, "occupancy": "off", "holds": {}}

    def ctx(self):
        self.n += 1
        return 0x0190AA00000000000000000000000000 + self.n

    def row(self, t, **change):
        self.now.update(change)
        self.rev += 1
        attrs = {"automatic": self.now["automatic"], "gaming_enabled": True,
                 "gaming_session": self.now["gaming_session"], "gaming_signal": "idle",
                 "occupancy": self.now["occupancy"], "revision": self.rev,
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()},
                 "error": None, "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count",
                 "pending_fixtures": [], "provisional_fixtures": [], "friendly_name": "Den lighting control"}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def bulb(self, t, e, on, ctx=None, user=False):
        ctx = self.ctx() if ctx is None else ctx
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off",
                           "context_id": "%032X" % ctx, "context_user_id": "0F" * 16 if user else "",
                           "context_parent_id": ""})
        return ctx

    def command(self, t, e, target):
        ctx = self.bulb(t - 0.3, e, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.rev,
                                                                 "target": target, "context_id": ulid_text(ctx)}})

    def write(self, folder):
        out = []
        for name, rows in (("status", self.status), ("bulbs", self.bulbs), ("commands", self.commands)):
            p = Path(folder) / ("den-%s.jsonl" % name)
            p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="ascii")
            out.append(p)
        return out


def correction_line(t, cid, e, after):
    event = {"schema": "den-event-1", "t": t, "seq": 0, "kind": "correction", "source": "correction_capture",
             "data": {"correction_id": cid, "entity_id": e, "after": after, "source": "switch_or_dimmer"}}
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def build(folder, f0, f1, switch_dt):
    x = Exports()
    for e in LIGHTS:
        x.bulb(f0 - 7200, e, False)
    x.row(f0 - 7000)                              # vacant
    x.row(f0 - 3000, occupancy="on")              # arrival, long before the facts
    for e in LIGHTS:
        x.command(f0 - 2999.5, e, LIT)
    ts = f0 + switch_dt
    x.bulb(ts, L2, False)                         # a switch turns L2 off ...
    x.row(ts + 0.01, holds={L2: OFF})             # ... and den_lighting holds it
    x.row(f1 + 600, occupancy="off")              # the den empties well after the facts end
    paths = x.write(folder)
    corr = Path(folder) / "corrections.jsonl"
    corr.write_text(correction_line(ts + 0.01, "cc-%d" % round((ts + 0.01) * 1000), L2, OFF) + "\n",
                    encoding="ascii")
    return paths, corr


def epoch(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").timestamp() - (
        datetime.now().astimezone().utcoffset().total_seconds() if False else 0)


def main(root):
    root = Path(root).resolve()
    tool = root / "tools" / "den-core"
    tmp = Path(tempfile.mkdtemp(prefix="probe-hold-span-"))
    subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--clean",
                    "--policies", "need", "--out", str(tmp / "syn")], check=True, capture_output=True, text=True)
    facts = sorted((tmp / "syn").glob("la-fact-log-1-*.jsonl"))
    times = [json.loads(line)["observation"]["available_at"] for f in facts
             for line in f.read_text().splitlines() if json.loads(line)["observation"]]
    f0, f1 = min(times), max(times)
    print("RESULT facts span %.1f .. %.1f (%d fact files)" % (f0, f1, len(facts)))
    results = {}
    for case, switch_dt in (("A_before_span", -600.0), ("B_inside_span", 60.0)):
        folder = tmp / case
        folder.mkdir()
        (status, bulbs, commands), corr = build(folder, f0, f1, switch_dt)
        argv = [sys.executable, "-B", str(tool / "run.py"), "--facts", *map(str, facts), "--status", str(status),
                "--lights", str(bulbs), "--live-commands", str(commands), "--corrections", str(corr),
                "--policies", "need", "incumbent", "decide", "--out", str(folder / "out")]
        done = subprocess.run(argv, capture_output=True, text=True)
        if done.returncode:
            print("FAIL %s: run.py exited %d: %s" % (case, done.returncode, done.stderr.strip()[-400:]))
            results[case] = None
            continue
        kept = [l for l in done.stdout.splitlines() if l.startswith("correction_capture:")]
        log = [json.loads(l) for l in (folder / "out" / "log.jsonl").read_text().splitlines()[1:]]
        looks = [e for e in log if e["kind"] == "decision" and e["source"] == "decide"
                 and e["data"]["why"].startswith("reading_look")]
        l2 = sorted({json.dumps(e["data"]["requested_scene"].get(L2), sort_keys=True) for e in looks})
        held_in_mode = any(h["entity_id"] == L2 for e in log if e["kind"] == "mode" for h in e["data"]["holds"])
        skipped_held = all(any(s["channel"] == L2 and s["reason"] == "held" for s in e["data"]["skipped"])
                           for e in looks)
        print("RESULT %s: %s" % (case, " | ".join(kept)))
        print("RESULT %s: %d reading_look decision(s); L2 requested %s; L2 in incumbent mode.holds: %s; "
              "L2 skipped as held in every look decision: %s" % (case, len(looks), l2, held_in_mode, skipped_held))
        ok = bool(looks) and l2 == [json.dumps(OFF, sort_keys=True)] and held_in_mode and skipped_held
        results[case] = ok
        print("%s %s: the live hold on L2 is pinned in both replayed arms" % ("PASS" if ok else "FAIL", case))
    if results.get("B_inside_span") and results.get("A_before_span") is False:
        print("FAIL overall: a hold opened before the replayed span is silently dropped (control B pins it)")
        return 1
    print("PASS overall" if all(results.values()) else "RESULT inconclusive: %s" % results)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
