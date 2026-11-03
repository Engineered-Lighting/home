"""den-core spine tests. From the worktree root:
python -B -m unittest discover -s tools/den-core -p "test_*.py"

Incumbent tests skip only when den_lighting's policy.py is not found above this
folder; the real poll-log test skips only when those local files are absent.
"""
import json
import math
import shutil
import sys
import tempfile
import unittest
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

    def test_diff_leaves_out_gaming(self):
        def decision(t, source, brightness):
            return ev("decision", {"requested_scene": {LIGHTS[0]: {**SCENE, "brightness": brightness}}}, t, 0, source)
        log = [ev("mode", {"gaming": False}, 0.0, 0, "incumbent"), decision(1.0, "incumbent", 100),
               decision(2.0, "decide", 150), decision(5.0, "decide", 101), ev("mode", {"gaming": True}, 6.0, 0, "incumbent"),
               decision(7.0, "decide", 30), ev("mode", {"gaming": False}, 8.0, 0, "incumbent"), decision(9.0, "decide", 100)]
        self.assertEqual(run.diff(log), [{"entity_id": LIGHTS[0], "start": 1.0, "end": 5.0},
                                         {"entity_id": LIGHTS[0], "start": 8.0, "end": 9.0}])


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
class PurityTest(Temp):
    def test_no_clock_no_network(self):
        sources.lab()
        forbidden = mock.Mock(side_effect=AssertionError("clock or network used"))
        with mock.patch("socket.socket", forbidden), mock.patch("time.time", forbidden), \
                mock.patch("time.monotonic", forbidden):
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
