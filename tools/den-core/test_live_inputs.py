"""Tests for rung 0's two adapters from Home Assistant exports: --corrections (correction-capture's corrections.jsonl)
and --status (den_lighting's status sensor). From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Every export here is synthetic, written to a temp folder in the formats the READMEs' sqlite3 queries print. The
corrections are made by correction-capture's own capture() from those exports, so the two tools are tested against
each other's file format, never against a copy of it.
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPTURE = HERE.parent / "correction-capture"
for folder in (HERE, CAPTURE):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import capture  # noqa: E402
import check  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import sources  # noqa: E402
from events import LIGHTS  # noqa: E402

L0, L1, L2, L3 = LIGHTS
HAVE_POLICY = incumbent.find_policy() is not None
STATUS = "sensor.den_lighting_control"
USER = "0F" * 16
T0 = 1793764800.0
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
DIM = {"on": True, "brightness": 40, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}


class Export:
    """A synthetic live den_lighting evening in the three recorder export formats correction-capture reads."""

    def __init__(self):
        self.status, self.bulbs, self.commands, self.n, self.revision = [], [], [], 0, 100
        self.now = {"automatic": True, "gaming_session": False, "occupancy": "off", "holds": {}}

    def context(self):
        self.n += 1
        return "%032X" % (0x0190000000000000000000000000 + self.n)

    def row(self, t, revision=None, **change):
        self.now.update(change)
        self.revision = self.revision + 1 if revision is None else revision
        attrs = {**self.now, "gaming_enabled": True, "gaming_signal": "idle", "revision": self.revision, "error": None,
                 "gaming_entity": "sensor.pc_gaming", "occupancy_source": "raw_person_count", "pending_fixtures": [],
                 "provisional_fixtures": [], "friendly_name": "Den lighting control",
                 "holds": {e: {"until": "room_vacant_2_minutes", "target": s} for e, s in self.now["holds"].items()}}
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "occupancy", "attributes": attrs})

    def unavailable(self, t):
        self.status.append({"last_updated_ts": t, "entity_id": STATUS, "state": "unavailable",
                            "attributes": {"restored": True, "friendly_name": "Den lighting control"}})

    def bulb(self, t, e, on, user=False):
        context = self.context()
        self.bulbs.append({"last_updated_ts": t, "entity_id": e, "state": "on" if on else "off", "context_id": context,
                           "context_user_id": USER if user else "", "context_parent_id": ""})
        return context

    def command(self, t, e, target):
        """den_lighting's light call: its own state row during the call, then the event when the call returns."""
        context = self.bulb(t - 0.3, e, target["on"])
        self.commands.append({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": self.revision,
                                                                 "target": target, "context_id": sources.ulid(context)}})

    def write(self, folder):
        paths = []
        for name, rows in (("status", self.status), ("lights", self.bulbs), ("commands", self.commands)):
            path = Path(folder) / ("den-%s.jsonl" % name)
            path.write_bytes("".join(json.dumps(r) + "\r\n" for r in rows).encode("ascii"))  # as a Windows copy may
            paths.append(path)
        return paths


def evening():
    """Vacant, an arrival, a switch turning L2 off and the app dimming L1 (two holds), a short vacancy that ends
    before den_lighting's 120 s (so no arrival, and the holds stay), then vacancy."""
    x = Export()
    for e in LIGHTS:
        x.bulb(T0 - 3600, e, False)
    x.row(T0 - 600)
    x.row(T0, occupancy="on")
    for e in LIGHTS:
        x.command(T0 + 0.5, e, LIT)
    x.bulb(T0 + 300, L2, False)  # a switch: the bulb changes, then den_lighting holds it
    x.row(T0 + 300.01, holds={L2: OFF})
    x.row(T0 + 400, holds={L2: OFF, L1: DIM})  # the app: den_lighting holds at the call, then the bulb reports
    x.bulb(T0 + 400.3, L1, True, user=True)
    x.row(T0 + 500, occupancy="off")
    x.row(T0 + 530, occupancy="on")  # den_lighting re-checks every bulb: held ones stay as the person set them
    x.row(T0 + 1000, occupancy="off")
    for e in (L0, L1, L3):
        x.command(T0 + 1120.5, e, OFF)
    x.row(T0 + 1120.5, holds={})
    return x


def correction_line(t, cid, entity_id, after, source="switch_or_dimmer"):
    return events.dumps({"schema": events.SCHEMA, "t": t, "seq": 0, "kind": "correction",
                         "source": "correction_capture",
                         "data": {"correction_id": cid, "entity_id": entity_id, "after": after, "source": source}})


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="den-core-live-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, lines, newline="\n", encoding="ascii"):
        path = self.tmp / name
        path.write_bytes("".join(line + newline for line in lines).encode(encoding))
        return path

    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            run.main(list(argv))
        return out.getvalue()

    def captured(self, x):
        """corrections.jsonl, written by correction-capture's own capture() from the exports."""
        status, lights, commands = x.write(self.tmp)
        record, replay = capture.capture([status], [lights], [commands])
        capture.write(self.tmp / "cph", record, replay)
        return status, lights, commands, self.tmp / "cph" / "corrections.jsonl"


class CorrectionAdapterTest(Temp):
    def test_reads_what_correction_capture_writes(self):
        *_, corrections = self.captured(evening())
        items = sources.correction_capture([corrections])
        self.assertEqual([(round(t - T0, 2), data["entity_id"], data["after"], data["source"]) for t, _, data in items],
                         [(300.01, L2, OFF, "switch_or_dimmer"), (400, L1, DIM, "app")])
        self.assertEqual({kind for _, kind, _ in items}, {"correction"})

    def test_repeats_powershell_and_refusals(self):
        line = correction_line(T0, "cc-1", L0, DIM)
        a = self.write("a.jsonl", [line, ""], newline="\r\n")
        b = self.write("b.jsonl", [line], encoding="utf-8-sig")  # PowerShell 5.1's Out-File -Encoding utf8: a byte-order mark
        self.assertEqual(sources.correction_capture([a, b]), [(T0, "correction", json.loads(line)["data"])])
        with self.assertRaisesRegex(events.BadFile, "is UTF-16"):  # its `>`
            sources.correction_capture([self.write("c.jsonl", [line], encoding="utf-16")])
        for bad in (correction_line(T0, "cc-1", L0, LIT),  # the same correction and bulb, other values
                    correction_line(T0, "cc-2", "light.kitchen", DIM),
                    correction_line(T0, "cc-3", L0, {"on": True}),
                    line.replace('"correction_capture"', '"synthetic"'),
                    line.replace('"kind":"correction"', '"kind":"tick"'),
                    "not json"):
            with self.subTest(bad=bad[:60]), self.assertRaises(ValueError):
                sources.correction_capture([a, self.write("bad.jsonl", [bad])])

    def test_one_action_on_two_bulbs_is_two_lines(self):
        path = self.write("c.jsonl", [correction_line(T0, "cc-1", L0, DIM), correction_line(T0, "cc-1", L1, OFF)])
        self.assertEqual([data["entity_id"] for _, _, data in sources.correction_capture([path])], [L0, L1])


class StatusAdapterTest(Temp):
    def test_occupancy_changes_restarts_and_live_status(self):
        x = Export()
        x.row(T0)                                   # off
        x.row(T0 + 10, occupancy="on")
        x.row(T0 + 20, holds={L0: DIM})             # no occupancy change: nothing
        x.row(T0 + 30, gaming_session=True)
        x.unavailable(T0 + 40)                      # HA restarts: den_lighting is not running
        x.row(T0 + 50, revision=3, gaming_session=False)
        x.row(T0 + 60, revision=1)                  # the revision goes down: a restart with no unavailable row
        x.row(T0 + 70, occupancy="off", automatic=False)
        status, *_ = x.write(self.tmp)
        items, restarts = sources.den_lighting_status([status, status])  # a repeated export counts once
        self.assertEqual(restarts, 2)
        self.assertEqual([(t - T0, data["value"]) for t, kind, data in items if kind == "occupancy"],
                         [(0, "off"), (10, "on"), (40, "unknown"), (50, "on"), (60, "unknown"), (60, "on"),
                          (70, "off")])
        self.assertEqual({data["via"] for _, kind, data in items if kind == "occupancy"}, {"den_lighting_status"})
        self.assertEqual([(t - T0, data) for t, kind, data in items if kind == "live_status"],
                         [(0, {"automatic": True, "gaming": False}), (30, {"automatic": True, "gaming": True}),
                          (40, {"automatic": None, "gaming": None}), (50, {"automatic": True, "gaming": False}),
                          (70, {"automatic": False, "gaming": False})])

    def test_powershell_redirect_and_refusals(self):
        x = Export()
        x.row(T0)
        lines = [json.dumps(r) for r in x.status]
        bom = self.write("s8.jsonl", [lines[0]], newline="\r\n", encoding="utf-8-sig")
        self.assertEqual(sources.den_lighting_status([bom])[0][0], (T0, "occupancy", {"value": "off",
                                                                                       "via": "den_lighting_status"}))
        with self.assertRaisesRegex(events.BadFile, "is UTF-16"):
            sources.den_lighting_status([self.write("s16.jsonl", [lines[0]], encoding="utf-16")])
        other = json.loads(lines[0])
        other["entity_id"] = "sensor.den_lighting_control_2"
        for bad in ([lines[0], json.dumps(other)], ['{"last_updated_ts": 1}'],
                    [lines[0].replace('"occupancy": "off"', '"occupancy": "maybe"')]):
            with self.subTest(bad=bad[-1][:60]), self.assertRaises(ValueError):
                sources.den_lighting_status([self.write("bad.jsonl", bad)])

    def test_status_span_carries_the_state_to_the_start(self):
        items = [(T0 - 3600, "occupancy", {"value": "off", "via": "den_lighting_status"}),
                 (T0 - 3600, "live_status", {"automatic": True, "gaming": False}),
                 (T0 - 60, "occupancy", {"value": "on", "via": "den_lighting_status"}),
                 (T0 + 100, "occupancy", {"value": "off", "via": "den_lighting_status"}),
                 (T0 + 900, "occupancy", {"value": "on", "via": "den_lighting_status"})]
        kept = run.status_span(items, T0, T0 + 500)
        # A vacancy older than runner.TAIL_S starts there, so den_lighting still takes the next on as an arrival.
        self.assertEqual([(t - T0, kind, data.get("value")) for t, kind, data in kept],
                         [(-150, "live_status", None), (-60, "occupancy", "on"), (100, "occupancy", "off")])
        self.assertEqual(run.status_span(items[:2], T0, T0 + 500)[0], (T0 - 150, "occupancy",
                                                                       {"value": "off", "via": "den_lighting_status"}))


class PairTest(unittest.TestCase):
    @staticmethod
    def row(t, e, on, context="unknown"):
        return (t, "light_observed", {"entity_id": e, "on": on, "brightness": None, "kelvin": None, "context": context})

    @staticmethod
    def corr(t, e, after):
        return (t, "correction", {"correction_id": "cc-%d" % t, "entity_id": e, "after": after, "source": "app"})

    def test_rows_before_and_after_own_far_and_wrong_way(self):
        states = [self.row(T0, L0, True), self.row(T0 + 10, L1, False), self.row(T0 + 20, L2, True, "own"),
                  self.row(T0 + 30, L3, True), self.row(T0 + 50, L0, True)]
        corrections = [self.corr(T0 + 1, L0, DIM),     # its row 1 s before: moved ahead of it
                       self.corr(T0 + 9.5, L1, OFF),   # its row 0.5 s after (the app): stays, ahead anyway
                       self.corr(T0 + 20, L2, DIM),    # only den_lighting's own row: unpaired
                       self.corr(T0 + 30, L3, OFF),    # the row turns the bulb on, the hold says off: unpaired
                       self.corr(T0 + 60, L0, DIM)]    # 10 s after the nearest row: unpaired
        tagged, rest, paired = run.pair(states, corrections)
        self.assertEqual(paired, 2)
        self.assertEqual([(t - T0, kind, source) for t, kind, _, source in tagged],
                         [(0, "correction", "correction_capture"), (0, "light_observed", "recorder_lights"),
                          (10, "light_observed", "recorder_lights"), (20, "light_observed", "recorder_lights"),
                          (30, "light_observed", "recorder_lights"), (50, "light_observed", "recorder_lights")])
        self.assertEqual((tagged[1][2]["brightness"], tagged[1][2]["kelvin"]), (40, 2700))  # the hold's levels
        self.assertEqual([t - T0 for t, *_ in rest], [9.5, 20, 30, 60])
        self.assertEqual(tagged[4][2]["brightness"], None)


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ReplayTest(Temp):
    """The whole evening from the Home Assistant exports alone, with and without --corrections."""

    def replay(self, name, *extra):
        status, lights, commands, corrections = self.captured(evening())
        argv = ["--status", str(status), "--lights", str(lights), "--live-commands", str(commands)]
        argv += ["--corrections", str(corrections)] if "corrections" in extra else []
        text = self.main(*argv, "--policies", "incumbent", "--out", str(self.tmp / name))
        header, log = events.read(self.tmp / name / "log.jsonl")
        return text, header, log

    def commands(self, log):
        return [(round(e["t"] - T0, 2), sorted(e["data"]["requested_scene"]))
                for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]

    def test_without_corrections_the_replay_sets_a_held_bulb_back(self):
        text, header, log = self.replay("plain")
        self.assertIn("commands live/replayed: front_left 2/2, front_right 2/2, rear_left 1/2, rear_right 2/2", text)
        self.assertIn((530, [L2]), self.commands(log))
        self.assertTrue(check.check(self.tmp / "plain" / "log.jsonl")["ok"])

    def test_with_corrections_it_holds_them_as_the_live_one_did(self):
        text, header, log = self.replay("held", "corrections")
        self.assertIn("correction_capture: kept 2 of 2 correction(s) inside the replayed span", text)
        self.assertIn("correction_capture: 2 correction(s) paired with the bulb row each explains, 1 of them moved "
                      "ahead of it", text)
        self.assertIn("den_lighting_status: 5 occupancy change(s) (on 2, off 3, unknown 0); 0 HA restart(s)", text)
        self.assertIn("diff live den_lighting vs incumbent replay (a null level matches any; live game and disabled "
                      "time left out): 7 interval(s); commands live/replayed: "
                      "front_left 2/2, front_right 2/2, rear_left 1/1, rear_right 2/2", text)
        self.assertEqual(self.commands(log), [(0, sorted(LIGHTS)), (1120, sorted([L0, L1, L3]))])
        held = [(round(e["t"] - T0, 2), e["data"]["entity_id"], e["data"]["after"])
                for e in log if e["kind"] == "correction"]
        self.assertEqual(held, [(300, L2, OFF), (400, L1, DIM)])
        modes = [e["data"]["holds"] for e in log if e["kind"] == "mode"]
        self.assertIn([{"entity_id": L2, "pinned": OFF, "since": T0 + 300, "lifetime": "room_vacant_2_minutes"},
                       {"entity_id": L1, "pinned": DIM, "since": T0 + 400, "lifetime": "room_vacant_2_minutes"}],
                      modes)
        # Only the usual lag of the live command behind the replayed one is left, 0.5 s each.
        self.assertEqual(sorted({round(s["end"] - s["start"], 3) for s in run.live_diff(header, log)}), [0.5])
        self.assertEqual([s["name"] for s in header["sources"]],
                         ["den-status.jsonl", "corrections.jsonl", "den-lights.jsonl", "den-commands.jsonl"])
        self.assertTrue(check.check(self.tmp / "held" / "log.jsonl")["ok"])

    def test_refusals(self):
        status, lights, commands, corrections = self.captured(evening())
        journal = self.write("edges.txt", ["occupancy edge: entity=binary_sensor.den_oak_person_occupancy "
                                           "from=unknown to=on observed_at=%.3f" % T0])
        for argv in (["--status", str(status), "--journal", str(journal)],
                     ["--status", str(status), "--synthetic", "evening"],
                     ["--status", str(status), "--assume-occupied"],
                     ["--status", str(status), "--corrections", str(corrections), "--policies", "need"]):
            with self.subTest(argv=argv[2:]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.main(*argv, "--out", str(self.tmp / "refused"))
        self.assertFalse((self.tmp / "refused").exists())


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class LiveStatusTest(unittest.TestCase):
    """live_status is a reference: it changes no decision, and live_diff leaves out the live game and disabled time."""

    def log(self, with_status):
        seen = [(T0, "light_observed", {"entity_id": e, **LIT, "context": "unknown"}, "recorder_lights")
                for e in LIGHTS]
        inputs = seen + [(T0, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")]
        # Both sides find the bulbs lit. The live den_lighting shows a game from +300 to +600 and is switched off
        # from +700 to +800; meanwhile its commands disagree with the replay, which saw neither.
        inputs += [(T0 + 400, "live_command", {"entity_id": L0, "revision": 2, "target": OFF, "context_id": "g"},
                    "recorder_commands"),
                   (T0 + 601, "live_command", {"entity_id": L0, "revision": 3, "target": LIT, "context_id": "h"},
                    "recorder_commands"),
                   (T0 + 750, "live_command", {"entity_id": L1, "revision": 4, "target": OFF, "context_id": "i"},
                    "recorder_commands"),
                   (T0 + 800, "live_command", {"entity_id": L1, "revision": 5, "target": LIT, "context_id": "j"},
                    "recorder_commands"),
                   (T0 + 900, "occupancy", {"value": "on", "via": "den_lighting_status"}, "den_lighting_status")]
        if with_status:
            inputs += [(T0 + dt, "live_status", {"automatic": a, "gaming": g}, "den_lighting_status")
                       for dt, a, g in ((0, True, False), (300, True, True), (600, True, False), (700, False, False),
                                        (800, True, False))]
        return runner.run(inputs, [incumbent.make_policy()], plant=False)

    def test_changes_no_decision_and_leaves_the_time_out(self):
        header, plain = self.log(False)
        _, status = self.log(True)
        decisions = [(e["t"], e["data"]) for e in status if e["kind"] in ("decision", "mode")]
        self.assertEqual(decisions, [(e["t"], e["data"]) for e in plain if e["kind"] in ("decision", "mode")])
        self.assertEqual([(s["entity_id"], s["start"] - T0, s["end"] - T0) for s in run.live_diff(header, plain)],
                         [(L0, 400, 601), (L1, 750, 800)])
        # Left out: the game and the disabled time. The live restore command lands 1 s after the status shows the
        # game over, the same short lag every agreeing command shows.
        self.assertEqual([(s["entity_id"], s["start"] - T0, s["end"] - T0) for s in run.live_diff(header, status)],
                         [(L0, 600, 601)])


class PurityTest(Temp):
    def test_no_clock_no_network(self):
        from unittest import mock
        forbidden = mock.Mock(side_effect=AssertionError("clock or network used"))
        x = evening()
        status, *_ = x.write(self.tmp)
        corrections = self.write("c.jsonl", [correction_line(T0, "cc-1", L0, DIM)])
        with mock.patch("socket.socket", forbidden), mock.patch("time.time", forbidden), \
                mock.patch("time.monotonic", forbidden):
            self.assertEqual(len(sources.den_lighting_status([status])[0]), 6)
            self.assertEqual(len(sources.correction_capture([corrections])), 1)


if __name__ == "__main__":
    unittest.main()
