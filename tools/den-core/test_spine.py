"""den-core spine tests. From the worktree root:
python -B -m unittest discover -s tools/den-core -p "test_*.py"

Incumbent tests skip only when den_lighting's policy.py is not found above this
folder; the real poll-log test skips only when those local files are absent.
"""
import io
import json
import math
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import events  # noqa: E402
import incumbent  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import sources  # noqa: E402
import synthetic  # noqa: E402
from events import LIGHTS  # noqa: E402
from synthetic import READING, T0, fact_line  # noqa: E402

FIXTURES = HERE.parents[1] / "stack" / "services" / "lighting-brain-bridges" / "victoria-ha" / "tests" / "fixtures" / "mqtt"
HAVE_POLICY = incumbent.find_policy() is not None
POLLS = ("den-desk-observe-20261028T173532Z.jsonl", "den-desk-observe-20261028T180446Z.jsonl")
REAL_POLLS = next(([folder / ".tmp" / name for name in POLLS] for folder in HERE.parents
                   if all((folder / ".tmp" / name).is_file() for name in POLLS)), None)
SCENE = {"on": True, "brightness": 100, "kelvin": 3000}
OFF = {"on": False, "brightness": None, "kelvin": None}
DEN = "binary_sensor.den_oak_person_occupancy"


def edge(t, a, b, entity=DEN):
    """One invented journal line in the occupancy reader's format (journalctl -o cat)."""
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (entity, a, b, t)


def context(n):
    """(hex, ULID text) of an invented HA Context id, as the recorder stores it and as den_lighting reports it."""
    return "%032X" % n, sources.ulid("%032X" % n)


def light_row(t, entity_id, state, context_id="", user="", parent=""):
    """One invented bulb state export row, as the README's sqlite3 query prints it."""
    return json.dumps({"last_updated_ts": t, "entity_id": entity_id, "state": state, "context_id": context_id,
                       "context_user_id": user, "context_parent_id": parent})


def command(t, entity_id, target, context_id, revision=7):
    """One invented recorder export row, as the README's sqlite3 query prints it."""
    return json.dumps({"time_fired_ts": t, "event_data": {"entity_id": entity_id, "revision": revision,
                                                          "target": target, "context_id": context_id}})


def ev(kind, data, t=1.0, seq=0, source="synthetic"):
    return {"schema": events.SCHEMA, "t": t, "seq": seq, "kind": kind, "source": source, "data": data}


def evening_log(**options):
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, **options)
    return runner.run(runner.tagged(items, "synthetic"), [incumbent.make_policy()], synthetic=True,
                      sources=[{"name": "synthetic:evening", "params": options}])


def of(log, kind, source="incumbent"):
    return [e for e in log if e["kind"] == kind and e["source"] == source]


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, lines, tail="\n"):
        path = self.tmp / name
        path.write_bytes(("\n".join(lines) + tail).encode("ascii"))
        return path


class EventsTest(Temp):
    def test_validate_refuses(self):
        good = ev("occupancy", {"value": "on", "via": "test"})
        events.validate(good)
        for bad in ({**good, "schema": "x"}, {**good, "kind": "nope"}, {**good, "extra": 1}, {**good, "t": math.nan},
                    ev("occupancy", {"value": "on"}), ev("mode", {"a": [{"image": "x"}]}),
                    ev("mode", {"a": ["data:image/jpeg;base64,AA"]})):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                events.validate(bad)

    def test_accepts_recipe_facts(self):
        events.validate(ev("facts", json.loads(fact_line(1, T0, T0 + 1.2, facts=READING))))

    def test_write_read_round_trip(self):
        header = {"schema": events.LOG_SCHEMA, "note": "test"}
        logged = [ev("tick", {}, 2.5, 0, "runner"), ev("correction", {"correction_id": "c", "entity_id": LIGHTS[0],
                                                                      "after": SCENE, "source": "t"}, 3.0, 1)]
        events.write(self.tmp / "a.jsonl", header, logged)
        events.write(self.tmp / "b.jsonl", *events.read(self.tmp / "a.jsonl"))
        self.assertEqual((self.tmp / "a.jsonl").read_bytes(), (self.tmp / "b.jsonl").read_bytes())
        with self.assertRaises(FileExistsError):
            events.write(self.tmp / "a.jsonl", header, logged)

    def test_write_refuses_checkout(self):
        with self.assertRaises(ValueError):
            events.write(HERE / "refused.jsonl", {"schema": events.LOG_SCHEMA}, [])
        self.assertFalse((HERE / "refused.jsonl").exists())


class FactLogTest(Temp):
    LINES = [fact_line(1, T0, T0 + 1.2, facts=READING),
             fact_line(2, T0 + 4, T0 + 5.1, facts={**READING, "activity_hypothesis": "unclear"}),
             fact_line(3, T0 + 8, T0 + 9.3, text="not json"),
             fact_line(4, T0 + 12, T0 + 12.9, outcome="timeout")]

    def test_reads_recipe_lines(self):
        items, torn = sources.fact_log([self.write("la-fact-log-1-2026-11-04.jsonl", self.LINES[::-1])])
        self.assertEqual(torn, 0)
        self.assertEqual([d for _, _, d in items], [json.loads(line) for line in self.LINES])
        self.assertEqual([d["observation"] and d["observation"]["status"] for _, _, d in items],
                         ["valid", "valid", "invalid", None])
        self.assertAlmostEqual(items[0][0], T0 + 1.2, places=3)
        for t, kind, data in items:
            self.assertEqual((kind, t), ("facts", sources.fact_time(data["available_at"])))

    def test_torn_last_line_is_skipped_and_counted(self):
        items, torn = sources.fact_log([self.write("a.jsonl", self.LINES[:3] + [self.LINES[3][:60]], tail="")])
        self.assertEqual((len(items), torn), (3, 1))
        with self.assertRaises(ValueError):
            sources.fact_log([self.write("b.jsonl", [self.LINES[3][:60]] + self.LINES[:3])])

    def test_duplicate_request_raises(self):
        with self.assertRaises(ValueError):
            sources.fact_log([self.write("a.jsonl", self.LINES[:1]), self.write("b.jsonl", self.LINES[:1])])


class AdapterTest(Temp):
    def test_mqtt_fixtures(self):
        expected = {"enter_leave": [(5, "on"), (200, "off")], "frigate_restart": [(5, "on"), (100, "unknown"), (165, "on")],
                    "frigate_silent": [(5, "on")], "two_people": [(10, "on")]}
        for name, want in expected.items():
            items = sources.mqtt_timeline(FIXTURES / (name + ".json"), t0=1000.0)
            self.assertEqual([(t - 1000, d["value"]) for t, _, d in items], want, name)
            self.assertTrue(all(kind == "occupancy" and d["via"] == "mqtt" for _, kind, d in items))

    def test_poll_log_emits_changes_only(self):
        def row(s, occ, front_left="on"):
            lights = {e: "on" for e in LIGHTS}
            lights[LIGHTS[0]] = front_left
            return json.dumps({"t": "2026-10-28T17:35:%02d.000000+00:00" % s, "occ": occ, "count": "1",
                               "active_count": "0", "lights": lights})
        path = self.write("poll.jsonl", [row(30, "on"), row(32, "on"), row(34, "on", "off"), row(36, "off", "off"),
                                         row(38, "off"), row(40, "off")])
        got = [(round(t % 60), kind, d.get("entity_id"), d.get("on", d.get("value"))) for t, kind, d in sources.poll_log(path)]
        self.assertEqual(got, [(30, "light_observed", e, True) for e in LIGHTS] + [
            (30, "occupancy", None, "on"), (34, "light_observed", LIGHTS[0], False), (36, "occupancy", None, "off"),
            (38, "light_observed", LIGHTS[0], True)])

    def test_occupancy_journal_keeps_the_den(self):
        first = self.write("a.txt", ["some other warning", edge(T0 + 30, "on", "off"), edge(T0, "unknown", "on"),
                                     edge(T0 + 10, "unknown", "on", "binary_sensor.elsewhere_person_occupancy")])
        second = self.write("b.txt", [edge(T0 + 30, "on", "off"), edge(T0 + 40, "off", "unavailable"),
                                      "occupancy edge: entity=%s from=off to=on" % DEN])
        items, unlogged = sources.occupancy_journal([first, second])
        self.assertEqual(items, [(T0, "occupancy", {"value": "on", "via": "jetson_journal"}),
                                 (T0 + 30, "occupancy", {"value": "off", "via": "jetson_journal"}),
                                 (T0 + 40, "occupancy", {"value": "unknown", "via": "jetson_journal"})])
        self.assertEqual(unlogged, [])

    def test_occupancy_journal_reports_an_unlogged_off(self):
        # A dropout that ends on logs from=unknown; one that ends off logs nothing until the next from=off.
        path = self.write("a.txt", [edge(T0, "unknown", "on"), edge(T0 + 600, "unknown", "on"),
                                    edge(T0 + 4000, "off", "on"), edge(T0 + 4100, "on", "off"),
                                    edge(T0 + 4200, "off", "on")])
        items, unlogged = sources.occupancy_journal([path])
        self.assertEqual([d["value"] for _, _, d in items], ["on", "on", "on", "off", "on"])
        self.assertEqual(unlogged, [(T0 + 600, T0 + 4000)])

    def test_occupancy_journal_reads_a_powershell_redirect(self):
        lines = [edge(T0, "unknown", "on"), edge(T0 + 30, "on", "off")]
        utf16 = self.tmp / "ps.txt"
        utf16.write_bytes("\r\n".join(lines).encode("utf-16"))  # what PowerShell 5.1's > writes
        self.assertEqual(sources.occupancy_journal([utf16]), sources.occupancy_journal([self.write("a.txt", lines)]))

    def test_recorder_commands(self):
        path = self.write("commands.jsonl", [command(T0 + 5, LIGHTS[1], OFF, "ctx-2"), "",
                                             command(T0 + 1, LIGHTS[0], SCENE, "ctx-1", revision=6)])
        self.assertEqual(sources.recorder_commands([path]), [
            (T0 + 1, "live_command", {"entity_id": LIGHTS[0], "revision": 6, "target": SCENE, "context_id": "ctx-1"}),
            (T0 + 5, "live_command", {"entity_id": LIGHTS[1], "revision": 7, "target": OFF, "context_id": "ctx-2"})])
        for name, line in (("bad-entity", command(T0, "light.elsewhere", OFF, "c")),
                           ("bad-target", command(T0, LIGHTS[0], {"on": "yes", "brightness": None, "kelvin": None}, "c")),
                           ("bad-time", command(None, LIGHTS[0], OFF, "c")), ("not-json", "{")):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "line 2 is not a den_lighting_command"):
                sources.recorder_commands([self.write(name + ".jsonl", [command(T0, LIGHTS[0], OFF, "ok"), line])])
        with self.assertRaisesRegex(ValueError, "repeats context ctx-1"):
            sources.recorder_commands([path, self.write("again.jsonl", [command(T0 + 9, LIGHTS[0], OFF, "ctx-1")])])

    def test_recorder_lights(self):
        (own_hex, own_ulid), (other, _), (person, _) = context(1), context(2), context(3)
        path = self.write("lights.jsonl", [
            light_row(T0 + 9, LIGHTS[3], "on", other, parent=own_hex),     # a report whose parent is own
            light_row(T0 + 1, LIGHTS[0], "off", own_hex), "",
            light_row(T0 + 2, LIGHTS[1], "on", other, user=person),
            light_row(T0 + 3, LIGHTS[2], "unavailable", other),            # skipped, as den_lighting skips it
            light_row(T0 + 4, LIGHTS[2], "on", other)])
        items = sources.recorder_lights([path], own=[own_ulid.lower()])
        self.assertEqual([(t - T0, d["entity_id"], d["on"], d["context"]) for t, _, d in items],
                         [(1, LIGHTS[0], False, "own"), (2, LIGHTS[1], True, "person"), (4, LIGHTS[2], True, "unknown"),
                          (9, LIGHTS[3], True, "own")])
        self.assertTrue(all(k == "light_observed" and d["brightness"] is None and d["kelvin"] is None
                            for _, k, d in items))
        self.assertEqual(sources.ulid("01563E3AB5D3D6764C61EFB99302BD5B"), "01ARZ3NDEKTSV4RRFFQ69G5FAV")  # ULID spec
        for name, line in (("other-entity", light_row(T0, "light.elsewhere", "on")),
                           ("bad-context", light_row(T0, LIGHTS[0], "on", "ABC")),
                           ("extra-key", json.dumps({**json.loads(light_row(T0, LIGHTS[0], "on")), "x": 1}))):
            with self.subTest(name), self.assertRaisesRegex(ValueError, "line 1 is not a den bulb state"):
                sources.recorder_lights([self.write(name + ".jsonl", [line])])
        with self.assertRaisesRegex(ValueError, "repeats light.den_front_left"):
            sources.recorder_lights([path, self.write("again.jsonl", [light_row(T0 + 1, LIGHTS[0], "off")])])

    def test_bulb_scene_draft_covers_the_four_bulbs(self):
        text = (HERE / "victoria_den_bulb_scenes.yaml").read_text("ascii")
        for bulb in LIGHTS:
            with self.subTest(bulb):
                self.assertEqual(text.count("'%s'" % bulb), 4)  # the state and den_lighting's three attributes
                self.assertIn("states('%s')" % bulb, text)
                for attribute in ("brightness", "color_temp_kelvin", "min_color_temp_kelvin"):
                    self.assertIn("state_attr('%s', '%s')" % (bulb, attribute), text)
        self.assertEqual(text.count("unique_id: den_core_scene_"), 4)

    def test_from_start_carries_each_bulb_to_the_start(self):
        rows = [(1.0, "light_observed", {"entity_id": LIGHTS[1], "n": 1}), (2.0, "light_observed", {"entity_id": LIGHTS[1], "n": 2}),
                (3.0, "light_observed", {"entity_id": LIGHTS[0], "n": 3}), (5.0, "light_observed", {"entity_id": LIGHTS[0], "n": 4}),
                (9.0, "light_observed", {"entity_id": LIGHTS[2], "n": 5})]
        self.assertEqual([(t, d["n"]) for t, _, d in run.from_start(rows, 4.0, 8.0)], [(4.0, 3), (4.0, 2), (5.0, 4)])

    @unittest.skipUnless(REAL_POLLS and HAVE_POLICY, "real den-desk-observe files or policy.py absent")
    def test_real_poll_logs_replay(self):
        for path in REAL_POLLS:
            header, log = runner.run(runner.tagged(sources.poll_log(path), "poll_log"), [incumbent.make_policy()],
                                     sources=[{"name": path.name, "sha256": events.sha256_file(path)}])
            out = self.tmp / (path.stem + ".jsonl")
            events.write(out, header, log)
            self.assertTrue(check.check(out)["ok"], path.name)


class Toy:
    def __init__(self, id, reply=lambda event: []):
        self.id, self.version, self.config, self.code_path = id, "toy-1", {"toy": id}, __file__
        self.reply, self.seen = reply, []

    def handle(self, event):
        self.seen.append((event["kind"], event["source"]))
        return self.reply(event)


class RunnerTest(unittest.TestCase):
    INPUTS = [(0.0, "occupancy", {"value": "on", "via": "t"}, "synthetic"),
              (10.0, "occupancy", {"value": "off", "via": "t"}, "synthetic"),
              (7.0, "correction", {"correction_id": "c", "entity_id": LIGHTS[2], "after": SCENE, "source": "t"}, "synthetic")]

    def toys(self):
        decide = {"kind": "decision", "data": {"requested_scene": {LIGHTS[2]: SCENE}, "transitions": {LIGHTS[2]: 3}}}
        return [Toy("decide"), Toy("incumbent", lambda e: [decide] if e["kind"] == "correction" else []),
                Toy("need", lambda e: [{"kind": "person_state", "data": {}}] if e["kind"] == "tick" else [])]

    def test_ticks_order_routing_and_echoes(self):
        toys = self.toys()
        header, log = runner.run(self.INPUTS, toys, tail_s=6.0)
        self.assertEqual([p["id"] for p in header["policies"]], ["need", "incumbent", "decide"])
        self.assertEqual([e["t"] for e in log if e["kind"] == "tick"], [2.0 * k for k in range(1, 9)])
        self.assertEqual([e["seq"] for e in log], list(range(len(log))))
        self.assertEqual([(e["kind"], e["source"]) for e in log if e["t"] == 10.0][:3],
                         [("occupancy", "synthetic"), ("light_observed", "plant"), ("tick", "runner")])
        echo = next(e for e in log if e["source"] == "plant")
        self.assertEqual((echo["t"], echo["data"]["context"], echo["data"]["brightness"]), (10.0, "own", 100))
        decide, inc, need = toys
        self.assertNotIn("person_state", {k for k, _ in need.seen})
        self.assertIn("person_state", {k for k, _ in inc.seen})
        self.assertNotIn("decision", {k for k, _ in inc.seen})
        self.assertTrue({"person_state", "decision"} <= {k for k, _ in decide.seen})
        self.assertEqual(of(log, "decision")[0]["data"]["actuation"], "replay")

    def test_runs_are_byte_identical(self):
        lines = [[events.dumps(v) for h, log in [runner.run(self.INPUTS, self.toys())] for v in [h, *log]]
                 for _ in range(2)]
        self.assertEqual(lines[0], lines[1])

    def test_missing_policy_module_is_named(self):
        with self.assertRaisesRegex(ValueError, "no_such_policy.py is not in tools/den-core yet"):
            runner.load_policies(["no_such_policy"])

    def test_reference_kind_is_logged_never_handed_to_a_policy(self):
        live = {"entity_id": LIGHTS[2], "revision": 3, "target": SCENE, "context_id": "ctx-1"}
        with_live = self.INPUTS + [(-5.0, "live_command", live, "recorder_commands"),
                                   (7.0, "live_command", {**live, "context_id": "ctx-2"}, "recorder_commands")]
        plain_toys, live_toys = self.toys(), self.toys()
        _, plain = runner.run(self.INPUTS, plain_toys)
        _, logged = runner.run(with_live, live_toys)
        self.assertEqual([(e["t"], e["source"]) for e in logged if e["kind"] == "live_command"],
                         [(-5.0, "recorder_commands"), (7.0, "recorder_commands")])

        def rest(log):
            return [(e["t"], e["kind"], e["source"], e["data"]) for e in log if e["kind"] != "live_command"]
        self.assertEqual(rest(logged), rest(plain))  # same ticks, same outputs
        self.assertEqual([toy.seen for toy in live_toys], [toy.seen for toy in plain_toys])
        with self.assertRaisesRegex(ValueError, "no inputs"):
            runner.run(with_live[-2:], self.toys())



def scripted(script):
    """A stand-in decide: at its first event at or after each script time, it requests that time's scenes."""
    script = sorted(script, key=lambda step: step[0])

    def reply(event):
        due = [scenes for t, scenes in script if t <= event["t"]]
        del script[:len(due)]
        return [{"kind": "decision", "data": {"requested_scene": scenes, "transitions": {}}} for scenes in due]
    return Toy("decide", reply)


def seen(t, bulb, scene, context="unknown"):
    return (t, "light_observed", {"entity_id": bulb, **scene, "context": context}, "synthetic")


def occupied(t, value):
    return (t, "occupancy", {"value": value, "via": "t"}, "synthetic")


def spans(found):
    return [(s["entity_id"], s["start"], s["end"]) for s in found]


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class DiffTest(unittest.TestCase):
    """run.diff and run.live_diff against den_lighting re-run beside the log (incumbent.Shadow)."""

    def diff(self, inputs, script, **options):
        header, log = runner.run(inputs, [incumbent.make_policy(), scripted(script)], **options)
        return spans(run.diff(header, log)), log

    def test_leaves_out_gaming(self):
        def game(t, status):
            return (t, "gaming_sample", {"status": status, "sampled_at": t}, "synthetic")
        inputs = [seen(0.0, LIGHTS[0], SCENE), occupied(0.0, "on"), game(10.0, "playing"), game(20.0, "playing")]
        inputs += [game(t, "idle") for t in (30.0, 40.0, 50.0, 60.0)]
        script = [(0, {LIGHTS[0]: SCENE}), (2, {LIGHTS[0]: {**SCENE, "brightness": 150}}),
                  (6, {LIGHTS[0]: {**SCENE, "brightness": 101}}),        # within den_lighting's own tolerance
                  (24, {LIGHTS[0]: {**SCENE, "brightness": 30}}),        # during the game: left out
                  (64, {LIGHTS[0]: SCENE})]
        found, log = self.diff(inputs, script)
        self.assertEqual([m["t"] for m in of(log, "mode") if m["data"]["gaming"]][:1], [20.0])
        self.assertEqual(found, [(LIGHTS[0], 2.0, 6.0), (LIGHTS[0], 60.0, 64.0)])

    def test_a_level_counts_against_a_last_level_on(self):
        # Nothing observed: the vacancy OFF's echo seeds normal with no level, so the arrival is ON with no level.
        level_less = {"on": True, "brightness": None, "kelvin": None}
        look = {"on": True, "brightness": 255, "kelvin": 2700}
        inputs = [occupied(0.0, "on"), occupied(1.0, "off"), occupied(300.0, "on")]
        script = [(300, {LIGHTS[0]: level_less}), (310, {LIGHTS[0]: look}), (320, {LIGHTS[0]: level_less})]
        found, log = self.diff(inputs, script)
        self.assertEqual([d["data"]["requested_scene"][LIGHTS[0]] for d in of(log, "decision") if d["t"] == 300.0],
                         [level_less])
        self.assertTrue(events.scene_matches(level_less, look))  # one way, the look would hide
        self.assertEqual(found, [(LIGHTS[0], 310.0, 320.0)])

    def test_recorded_bulbs_and_den_lightings_own_instants_open_nothing(self):
        # A live bulb recorded off before the replay's vacancy runs out, and den_lighting turning another off
        # on an input decide does not plan on, both before decide's next tick.
        inputs = [seen(0.0, LIGHTS[0], SCENE), seen(0.0, LIGHTS[1], SCENE), occupied(0.0, "on"), occupied(11.0, "off"),
                  seen(130.5, LIGHTS[0], OFF, "own"), (131.5, "gaming_sample", {"status": "idle", "sampled_at": 131.5},
                                                      "synthetic")]
        script = [(0, {LIGHTS[0]: SCENE, LIGHTS[1]: SCENE}), (132, {LIGHTS[0]: OFF, LIGHTS[1]: OFF})]
        found, log = self.diff(inputs, script, plant=False)
        self.assertEqual([(d["t"], sorted(d["data"]["requested_scene"])) for d in of(log, "decision")],
                         [(131.5, sorted(LIGHTS[1:]))])  # front_left was found already off
        self.assertEqual(found, [])

    def test_a_hold_is_what_den_lighting_wants(self):
        held = {"on": True, "brightness": 80, "kelvin": 2202}
        inputs = [seen(0.0, LIGHTS[0], SCENE), occupied(0.0, "on"),
                  (3.0, "correction", {"correction_id": "c1", "entity_id": LIGHTS[0], "after": held, "source": "t"},
                   "synthetic")]
        found, _ = self.diff(inputs, [(0, {LIGHTS[0]: SCENE}), (3, {LIGHTS[0]: held})])
        self.assertEqual(found, [])
        found, log = self.diff(inputs, [(0, {LIGHTS[0]: SCENE})])
        self.assertEqual(found, [(LIGHTS[0], 3.0, log[-1]["t"])])

    def test_live_diff_counts_a_bulb_found_already_there(self):
        lit = {"on": True, "brightness": 180, "kelvin": 2700}

        def live(t, target):
            return (t, "live_command", {"entity_id": LIGHTS[0], "revision": 1, "target": target, "context_id": "c%g" % t},
                    "recorder_commands")
        # The live OFF and its recorded state come before the replay's own vacancy tick, so the replay finds the
        # bulb already off and sends nothing; then the replay's arrival ON has other levels than the live one.
        inputs = [seen(0.0, LIGHTS[0], SCENE), occupied(0.0, "on"), occupied(10.0, "off"), live(129.0, OFF),
                  seen(129.1, LIGHTS[0], OFF, "own"), occupied(200.0, "on"), live(200.5, lit)]
        header, log = runner.run(inputs, [incumbent.make_policy()], plant=False)
        self.assertEqual([d["t"] for d in of(log, "decision") if LIGHTS[0] in d["data"]["requested_scene"]], [200.0])
        found = spans(run.live_diff(header, log))
        # The other bulbs' vacancy OFFs (den_lighting asks even for unobserved bulbs) have no live command here.
        self.assertEqual(sorted({(bulb, start) for bulb, start, _ in found if bulb != LIGHTS[0]}),
                         [(bulb, 130.0) for bulb in sorted(LIGHTS[1:])])
        self.assertEqual([s for s in found if s[0] == LIGHTS[0]], [(LIGHTS[0], 129.0, 130.0), (LIGHTS[0], 200.0, log[-1]["t"])])
        cross = {"on": True, "brightness": 180, "kelvin": None}
        self.assertTrue(run._agree(cross, {"on": True, "brightness": None, "kelvin": 2700}))
        self.assertFalse(run._agree(cross, {"on": True, "brightness": 100, "kelvin": None}))


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class IncumbentTest(Temp):
    def test_a_touch(self):
        header, log = evening_log(touch=True)
        decisions = of(log, "decision")
        self.assertEqual([d["t"] - T0 for d in decisions], [-470, -60, 1570])
        off, on, last = (d["data"] for d in decisions)
        self.assertEqual((off["trigger"], last["trigger"]), ("time step", "time step"))
        for d, scene in ((off, {"on": False, "brightness": None, "kelvin": None}),
                         (on, {"on": True, "brightness": 180, "kelvin": 2700}), (last, {"on": False, "brightness": None, "kelvin": None})):
            self.assertEqual(d["requested_scene"], {e: scene for e in LIGHTS})
        held = [m["data"]["holds"] for m in of(log, "mode") if T0 + 720 <= m["t"] < T0 + 1570]
        self.assertTrue(held)
        for holds in held:
            self.assertEqual(holds, [{"entity_id": "light.den_rear_left", "pinned": {"on": True, "brightness": 80, "kelvin": 2202},
                                      "since": T0 + 720, "lifetime": "room_vacant_2_minutes"}])
        self.assertEqual([m["data"]["holds"] for m in of(log, "mode") if m["t"] == T0 + 1570], [[]])

    def test_b_game(self):
        _, log = evening_log(game=True)
        start = next(d["data"] for d in of(log, "decision") if d["t"] == T0 + 1090)
        self.assertEqual([start["requested_scene"][e] for e in LIGHTS],
                         [{"on": True, "brightness": 38, "kelvin": k} for k in (1000, 1000, 2202, 2202)])
        end = next(d["data"] for d in of(log, "decision") if d["t"] == T0 + 1230)
        self.assertEqual(end["requested_scene"], {e: {"on": True, "brightness": 180, "kelvin": 2700} for e in LIGHTS})
        self.assertEqual(set(end["transitions"].values()), {5})

    def test_c_touch_and_game(self):
        _, log = evening_log(touch=True, game=True)
        self.assertFalse([d for d in of(log, "decision") if T0 + 720 <= d["t"] < T0 + 1570
                          and "light.den_rear_left" in d["data"]["requested_scene"]])
        self.assertIn("game_ends", {h["lifetime"] for m in of(log, "mode") for h in m["data"]["holds"]})
        self.assertTrue(all(m["data"]["holds"] == [] for m in of(log, "mode") if m["t"] >= T0 + 1230))

    def test_d_decision_shape_and_header(self):
        header, log = evening_log(touch=True, game=True)
        for d in of(log, "decision"):
            self.assertEqual((d["data"]["policy"], d["data"]["actuator_domain"], d["data"]["actuation"]),
                             ("den_lighting@1.0.4", "hue-den-4", "synthetic"))
        self.assertEqual(header["policies"][0]["config"]["den_lighting_policy_sha256"],
                         events.sha256_file(incumbent.find_policy()))

    def test_e_shadow_follows_the_logged_run(self):
        items, _, _ = synthetic.evening(touch=True, game=True)
        header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                                 synthetic=True)
        shadow = incumbent.Shadow(header)
        replayed = [(e["t"], out["data"]["requested_scene"]) for e in log for out in shadow.feed(e)
                    if out["kind"] == "decision"]
        self.assertEqual(replayed, [(d["t"], d["data"]["requested_scene"]) for d in of(log, "decision")])
        self.assertEqual(shadow.acted, {e: OFF for e in LIGHTS})  # the evening ends with the vacancy OFF


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class CheckTest(Temp):
    def setUp(self):
        super().setUp()
        events.write(self.tmp / "log.jsonl", *evening_log(touch=True))
        self.lines = (self.tmp / "log.jsonl").read_text("ascii").split("\n")[:-1]

    def variant(self, lines):
        return check.check(self.write("variant.jsonl", lines))

    def test_ok(self):
        self.assertEqual(check.check(self.tmp / "log.jsonl"), {"ok": True, "first_mismatch": None,
                                                                "reason": "%d lines replayed byte-identically" % len(self.lines)})

    def test_one_byte_in_a_derived_line(self):
        n = next(i for i, line in enumerate(self.lines) if '"why":"den_lighting command"' in line)
        lines = list(self.lines)
        lines[n] = lines[n].replace("den_lighting command", "den_lighting commanD")
        result = self.variant(lines)
        self.assertEqual((result["ok"], result["first_mismatch"]["line"]), (False, n + 1))

    def test_changed_header_config_or_code(self):
        for key, value, reason in (("config", None, "refused its logged config"), ("code_sha256", "0" * 64, "code changed: incumbent")):
            header = json.loads(self.lines[0])
            if key == "config":
                header["policies"][0]["config"]["vacancy_seconds"] = 60
            else:
                header["policies"][0][key] = value
            result = self.variant([events.dumps(header)] + self.lines[1:])
            self.assertFalse(result["ok"])
            self.assertIn(reason, result["reason"])
            (self.tmp / "variant.jsonl").unlink()


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class RealFilesRunTest(Temp):
    """A facts + journal replay, compared with live commands, through run.py and check.py (invented files)."""

    def setUp(self):
        super().setUp()
        self.facts = self.write("la-fact-log-1-2026-11-04.jsonl", FactLogTest.LINES)
        self.journal = self.write("edges.txt", [edge(T0 - 5, "unknown", "on"), edge(T0 + 20, "on", "off"),
                                                edge(T0 + 200, "off", "on")])

    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            run.main(list(argv))
        return out.getvalue()

    def decisions(self, name):
        _, log = events.read(self.tmp / name / "log.jsonl")
        return [(e["t"], e["data"]["requested_scene"]) for e in of(log, "decision")], log

    def test_journal_live_commands_and_check(self):
        self.main("--facts", str(self.facts), "--journal", str(self.journal), "--policies", "need", "incumbent", "decide",
                  "--out", str(self.tmp / "plain"))
        plain, log = self.decisions("plain")
        self.assertEqual([(e["t"] - T0, e["data"]) for e in log if e["kind"] == "occupancy"],
                         [(dt, {"value": v, "via": "jetson_journal"}) for dt, v in ((-5, "on"), (20, "off"), (200, "on"))])
        self.assertTrue(all(e["source"] == "occupancy_journal" for e in log if e["kind"] == "occupancy"))
        # Nothing seen but the plant's own echoes: vacancy OFF 120 s after off, then last-level ON with no level.
        self.assertEqual([(t - T0, scenes) for t, scenes in plain],
                         [(141, {e: OFF for e in LIGHTS}),
                          (200, {e: {"on": True, "brightness": None, "kelvin": None} for e in LIGHTS})])

        # The live integration sent the same commands 0.5 s later, plus one before the replayed span.
        rows = [command(T0 - 3600, LIGHTS[0], OFF, "ctx-early")]
        rows += [command(t + 0.5, e, scene, "ctx-%d-%s" % (t, e)) for t, scenes in plain for e, scene in scenes.items()]
        commands = self.write("den-lighting-commands.jsonl", rows)
        text = self.main("--facts", str(self.facts), "--journal", str(self.journal), "--live-commands", str(commands),
                         "--policies", "need", "incumbent", "decide", "--out", str(self.tmp / "live"))
        self.assertIn("recorder_commands: kept 8 of 9 live command(s) inside the replayed span", text)
        self.assertIn("diff live den_lighting vs incumbent replay (a null level matches any): 8 interval(s); "
                      "commands live/replayed: "
                      "front_left 2/2, front_right 2/2, rear_left 2/2, rear_right 2/2", text)
        live, log = self.decisions("live")
        self.assertEqual(live, plain)
        header, _ = events.read(self.tmp / "live" / "log.jsonl")
        self.assertEqual(sorted({round(s["end"] - s["start"], 3) for s in run.live_diff(header, log)}), [0.5])
        self.assertEqual([s["name"] for s in header["sources"]],
                         [self.facts.name, self.journal.name, commands.name])
        self.assertEqual(header["sources"][2]["sha256"], events.sha256_file(commands))
        self.assertEqual(check.check(self.tmp / "live" / "log.jsonl")["ok"], True)

    def test_recorded_bulbs(self):
        """Bulbs on before the span, a person turning one off while the room is vacant, den_lighting's own OFFs."""
        lit = {"on": True, "brightness": 180, "kelvin": 2700}
        rows = [light_row(T0 - 3600, e, "on", context(10)[0]) for e in LIGHTS]
        rows.append(light_row(T0 + 50, LIGHTS[1], "unavailable", context(11)[0]))
        rows.append(light_row(T0 + 100, LIGHTS[2], "off", context(12)[0], user=context(13)[0]))
        commands = [command(T0 - 3600, LIGHTS[0], lit, context(20)[1])]
        for n, e in enumerate((LIGHTS[0], LIGHTS[1], LIGHTS[3])):
            commands.append(command(T0 + 141.5, e, OFF, context(30 + n)[1]))
            rows.append(light_row(T0 + 141.6, e, "off", context(30 + n)[0]))
        rows.append(light_row(T0 + 141.7, LIGHTS[3], "off", context(39)[0], parent=context(32)[0]))
        for n, e in enumerate(LIGHTS):
            commands.append(command(T0 + 200.5, e, lit, context(40 + n)[1]))
            rows.append(light_row(T0 + 200.6, e, "on", context(40 + n)[0]))  # after the last input: not replayed
        lights, commands = self.write("lights.jsonl", rows), self.write("commands.jsonl", commands)
        text = self.main("--facts", str(self.facts), "--journal", str(self.journal), "--live-commands", str(commands),
                         "--lights", str(lights), "--policies", "need", "incumbent", "decide", "--out", str(self.tmp / "lit"))
        self.assertIn("recorder_lights: kept 9 of 13 bulb state(s), 4 carried to the start; "
                      "context own 4, person 1, unknown 4\nplant: off", text)
        self.assertIn("diff live den_lighting vs incumbent replay (a null level matches any): 7 interval(s); "
                      "commands live/replayed: front_left 2/2, front_right 2/2, rear_left 1/1, rear_right 2/2", text)
        decisions, log = self.decisions("lit")
        on = {"on": True, "brightness": None, "kelvin": None}
        self.assertEqual([(t - T0, scenes) for t, scenes in decisions],
                         [(141, {e: OFF for e in LIGHTS if e != LIGHTS[2]}), (200, {e: on for e in LIGHTS})])
        self.assertEqual([(round(e["t"] - T0, 3), e["data"]["entity_id"], e["data"]["on"], e["data"]["context"])
                          for e in log if e["kind"] == "light_observed"],
                         [(-5, e, True, "unknown") for e in LIGHTS] + [(100, LIGHTS[2], False, "person")]
                         + [(141.6, e, False, "own") for e in (LIGHTS[0], LIGHTS[1], LIGHTS[3])]
                         + [(141.7, LIGHTS[3], False, "own")])
        self.assertTrue(all(e["source"] == "recorder_lights" for e in log if e["kind"] == "light_observed"))
        header, _ = events.read(self.tmp / "lit" / "log.jsonl")
        self.assertEqual(sorted({round(s["end"] - s["start"], 3) for s in run.live_diff(header, log)}), [0.5])
        self.assertFalse(header["plant"])
        self.assertEqual([s["name"] for s in header["sources"]],
                         [self.facts.name, self.journal.name, lights.name, commands.name])
        self.assertEqual(check.check(self.tmp / "lit" / "log.jsonl")["ok"], True)

    def test_refusals(self):
        commands = self.write("c.jsonl", [command(T0, LIGHTS[0], OFF, "c")])
        lights = self.write("l.jsonl", [light_row(T0, LIGHTS[0], "on")])
        for argv in (["--facts", str(self.facts), "--journal", str(self.journal), "--assume-occupied"],
                     ["--facts", str(self.facts), "--live-commands", str(commands), "--policies", "need"],
                     ["--facts", str(self.facts), "--lights", str(lights)],
                     ["--live-commands", str(commands)]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.main(*argv, "--out", str(self.tmp / "refused"))
        self.assertFalse((self.tmp / "refused").exists())


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class PurityTest(Temp):
    def test_no_clock_no_network(self):
        sources.lab()
        forbidden = mock.Mock(side_effect=AssertionError("clock or network used"))
        journal = self.write("edges.txt", [edge(T0, "unknown", "on")])
        commands = self.write("commands.jsonl", [command(T0, LIGHTS[0], OFF, "c")])
        lights = self.write("lights.jsonl", [light_row(T0, LIGHTS[0], "on", context(1)[0])])
        with mock.patch("socket.socket", forbidden), mock.patch("time.time", forbidden), \
                mock.patch("time.monotonic", forbidden):
            self.assertEqual(len(sources.occupancy_journal([journal])[0] + sources.recorder_commands([commands])
                                 + sources.recorder_lights([lights])), 3)
            synthetic.evening()
            events.write(self.tmp / "log.jsonl", *evening_log(touch=True, game=True))
            self.assertTrue(check.check(self.tmp / "log.jsonl")["ok"])

    def test_same_seed_same_bytes(self):
        def once(seed):
            items, truth, fact_lines = synthetic.evening(seed=seed, touch=True, game=True)
            header, log = runner.run(runner.tagged(items, "synthetic"), [incumbent.make_policy()], synthetic=True)
            return fact_lines + [events.dumps(truth)] + [events.dumps(v) for v in [header, *log]]
        self.assertEqual(once(7), once(7))
        self.assertNotEqual(once(7), once(8))


if __name__ == "__main__":
    unittest.main()
