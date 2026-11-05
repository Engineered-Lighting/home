"""Tests for rung 9: coordinator v0 (decide) scored on replay. From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic: the fake light model built from rung 8's plan, hand-made events, synthetic evenings and
the hold fixtures. Logs and score files go to temp folders only. Nothing reads a host, camera or light.
"""
import ast
import copy
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import decide  # noqa: E402
import events  # noqa: E402
import holds  # noqa: E402
import incumbent  # noqa: E402
import light_model  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402
import synthetic  # noqa: E402

FL, FR, RL, RR = events.LIGHTS
HAVE_POLICY = incumbent.find_policy() is not None
T0 = synthetic.T0
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}


def filled(config_slots):
    """decide's slots with every open one set to its proposed value."""
    return {name: {**entry, "value": entry["proposed"], "status": "set"} for name, entry in config_slots.items()}


def real_model():
    model = light_model.fake()
    model.update(version="test-lm-1", fake=False)
    return model


def evening_log(policies=("need", "incumbent", "decide"), synthetic_flag=True, configs=None, **options):
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, **options)
    return runner.run(runner.tagged(items, "synthetic"), runner.load_policies(policies, configs),
                      synthetic=synthetic_flag, sources=[{"name": "synthetic:evening", "params": options}])


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="den-core-rung9-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, header, log, name="log.jsonl"):
        events.write(self.tmp / name, header, log)
        return self.tmp / name


class LightModelTest(unittest.TestCase):
    def setUp(self):
        self.model = light_model.LightModel(light_model.fake())

    def test_the_plans_formula(self):
        m, raw = self.model, light_model.fake()
        g = raw["dimming"][FL]["g"]  # levels ... 176, 224 ... at indexes 10 and 11
        g180 = g[10] + (g[11] - g[10]) * (180 - 176) / 48
        at_2700 = raw["basis"][FL]["2202"]["desk-1"] + (raw["basis"][FL]["3290"]["desk-1"] - raw["basis"][FL]["2202"]["desk-1"]) \
            * (1e6 / 2700 - 1e6 / 2202) / (1e6 / 3290 - 1e6 / 2202)
        e = m.predict({FL: LIT, FR: OFF, RL: OFF, RR: OFF}, ["desk-1"])["desk-1"]
        self.assertAlmostEqual(e, g180 * at_2700, places=9)
        both = m.predict({FL: LIT, FR: LIT, RL: OFF, RR: OFF})
        parts = m.contributions({FL: LIT, FR: LIT, RL: OFF, RR: OFF})
        self.assertAlmostEqual(both["desk-1"], parts[FL]["desk-1"] + parts[FR]["desk-1"], places=12)

    def test_clamps_unknowns_and_off(self):
        m = self.model
        cool = m.contribution(FL, {"on": True, "brightness": 254, "kelvin": 20000}, ["desk-1"])["desk-1"]
        self.assertEqual(cool, light_model.fake()["basis"][FL]["6500"]["desk-1"])  # clamped at the cool end
        self.assertEqual(m.contribution(FL, {"on": True, "brightness": 255, "kelvin": 6500}, ["desk-1"]),
                         {"desk-1": cool})  # den_lighting's 255 goes out as 254
        self.assertEqual(m.contribution(FL, OFF, ["desk-1"]), {"desk-1": 0.0})
        self.assertEqual(m.predict({FL: {"on": True, "brightness": None, "kelvin": None}, FR: OFF, RL: OFF, RR: OFF},
                                   ["desk-1"]), {"desk-1": None})  # a recorded bulb with no level
        self.assertEqual(m.predict({FL: LIT}, ["desk-1"]), {"desk-1": None})  # a bulb never seen is unknown
        self.assertIsNone(m.glare({}, []))

    def test_check_and_describe(self):
        bad = light_model.fake()
        bad["dimming"][FL]["g"][-1] = 0.9
        with self.assertRaises(ValueError):
            light_model.LightModel(bad)
        self.assertEqual(self.model.describe(), {"version": "fake-plan-v0", "sha256": self.model.sha256, "fake": True,
                                                 "basis_unit": "relative",
                                                 "glare": "none: the model writes glare null"})

    def test_standard_library_only(self):
        tree = ast.parse(Path(light_model.__file__).read_text(encoding="ascii"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(names, {"copy", "hashlib", "json"})


def evening_decisions(**options):
    header, log = evening_log(**options)
    return header, log, [e for e in log if e["kind"] == "decision" and e["source"] == "decide"]


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class RecordTest(unittest.TestCase):
    """Every Decision carries its requested and applied scenes and their recipients, reproducible on replay."""

    def test_requested_applied_recipients_and_inputs(self):
        _, log, found = evening_decisions(touch=True, game=True)
        self.assertGreater(len(found), 5)
        for e in found:
            d = e["data"]
            held = {h["entity_id"]: h["pinned"] for h in d["inputs"]["hold_set"]["held"]}
            self.assertEqual(set(d["applied_scene"]), set(d["requested_scene"]))
            for b, applied in d["applied_scene"].items():
                if b in held:
                    self.assertEqual((applied["status"], applied["readback"]), ("held", held[b]))
                else:
                    self.assertEqual((applied["status"], applied["readback"]),
                                     ("not_sent", d["inputs"]["actuator_state"][b]))
            self.assertEqual(set(d["recipients"]), {b for b, s in d["requested_scene"].items() if s["on"]})
            shares = {}
            for row in (r for rec in d["recipients"].values() for r in rec["targets"]):
                shares[row["target_id"]] = shares.get(row["target_id"], 0) + row["share"]
            self.assertTrue(all(abs(v - 1) < 1e-3 for v in shares.values()), shares)
            inputs = d["inputs"]
            self.assertEqual(decide.replay_exclusions(inputs), inputs["exclusions"])
            self.assertEqual(inputs["exclusions"]["excluded"], [])
            self.assertEqual(inputs["light_model"]["version"], "fake-plan-v0")
            self.assertEqual(inputs["preference"]["offsets"], {"level_factor": 1, "mired_shift": 0, "contrast_shift": 0})
            self.assertEqual((inputs["look"], inputs["comfort_limits"], inputs["body_envelope"]["version"]),
                             ("reading@0", "cl-den-0", "body-envelope/v1"))
            self.assertEqual(d["hard_conflicts"], [])
            self.assertTrue(inputs["hard_constraints"]["glare"]["status"].startswith("unchecked"))
            if inputs["person_state"]:  # task points are read once someone is there
                self.assertIn("task_points", inputs["open_slots"])
            for b in d["requested_scene"]:
                if b in held:
                    self.assertIsNone(inputs["t_exec"][b])
                    self.assertTrue(inputs["t_exec"][b + "_status"].startswith("held"))
                elif inputs["t_exec"][b] is not None:
                    self.assertEqual(inputs["t_exec"][b], d["ts"])  # decide's transitions are 0 s
            self.assertNotIn("hold_limited", d)  # present only under P1-P3
            self.assertTrue(all("direction" not in h for h in inputs["hold_set"]["held"]))

    def test_t_exec_ages_every_field_to_each_channel(self):
        inputs = {"person_state": ["A@10.000"], "field_support": {"A@10.000": {
                      "focused_engagement": {"last_support_capture_ts": 9.0, "support_status": "observed"}}},
                  "body_envelope": {"version": "body-envelope/v1", "parameters": None,
                                    "parameters_status": "pending (envelope-parameters)"},
                  "t_exec": {FL: 12.5, FR: None, FR + "_status": "kept"}}
        got = decide.replay_exclusions(inputs)
        self.assertEqual(got["ages"], {"A@10.000": {"focused_engagement": {FL: 3.5}}})
        with self.assertRaises(ValueError):
            decide.replay_exclusions({**inputs, "body_envelope": {"version": "body-envelope/v2"}})

    def test_preference_stand_in_passes_through_the_guardrails(self):
        config = decide.make_policy().config
        look = config["looks"]["reading@0"]["scene"]
        self.assertEqual(decide.prefer(look, config), look)
        moved = decide._merge(config, {"preference": {"offsets": {"level_factor": 0.5, "mired_shift": 40}}})
        # level_factor clamps to the guardrail's 0.6; mired_shift has a null guardrail, so it is held at 0.
        self.assertEqual(decide.prefer({"on": True, "brightness": 200, "kelvin": 2700}, moved),
                         {"on": True, "brightness": 120, "kelvin": 2700})


class HardConstraintTest(unittest.TestCase):
    """The seed Look's glare limit on free channels; every conflict in hard_conflicts. Empty today: no glare model."""

    def setUp(self):
        self.model = light_model.LightModel(light_model.fake())
        self.eyes = [{"track_id": "A", "attention": {"head_xyz": [1, 1, 1.2]}}]

    def test_unchecked_today(self):
        self.assertEqual(decide.glare_conflicts(self.model, {FL: LIT}, {}, self.eyes, "unchecked")[0], [])
        self.assertEqual(decide.glare_conflicts(self.model, {FL: LIT}, {}, self.eyes, 0.5),
                         ([], "unchecked: the light model has no glare (rung 8 writes glare: null)"))

    def test_with_a_glare_model_a_held_light_is_logged_and_a_free_one_is_not_chosen(self):
        def glare(scenes, eyes):
            return {("A", b): (0.9 if s["on"] else 0.0) for b, s in scenes.items() if s is not None}
        with mock.patch.object(light_model.LightModel, "glare", side_effect=glare, autospec=False):
            conflicts, status = decide.glare_conflicts(self.model, {FL: LIT, FR: OFF}, {FL}, self.eyes, 0.5)
            self.assertEqual(status, "checked against 0.5")
            self.assertEqual(conflicts, [{"constraint": "glare", "held_channel": FL, "track": "A",
                                          "eye_or_envelope": "eye", "G_hi": 0.9, "limit": 0.5,
                                          "model_status": "unvalidated"}])
            self.assertFalse(decide.free_glare_ok(self.model, {FR: LIT}, {FL}, self.eyes, 0.5))
            self.assertTrue(decide.free_glare_ok(self.model, {FL: LIT, FR: OFF}, {FL}, self.eyes, 0.5))
            # In a plan: the free bulbs may not take a glaring candidate, so "off" (never glaring) wins.
            config = decide._merge(decide.DEFAULT_CONFIG, {"slots": {"glare_limit": {"value": 0.5, "status": "set"}}})
            state = {"occupancy": "on", "mode": {"revision": 1, "gaming": False, "holds": [], "normal":
                                                 {b: LIT for b in events.LIGHTS}},
                     "people": {"A": self.eyes[0]}, "actuator_state": {b: LIT for b in events.LIGHTS}}
            result = decide.plan(state, T0, config)
        self.assertEqual(result["chosen"], "off")
        self.assertEqual(result["requested_scene"], {b: OFF for b in events.LIGHTS})
        self.assertTrue(all(c["why"].startswith("rejected") for c in result["candidates"] if c["scene_id"] != "off"))


class ActuationTest(Temp):
    def inputs(self):
        return [(T0, "light_observed", {"entity_id": e, **LIT, "context": "unknown"}, "recorder_lights")
                for e in events.LIGHTS] + [(T0, "occupancy", {"value": "on", "via": "den_lighting_status"},
                                            "den_lighting_status"),
                                           (T0 + 30, "occupancy", {"value": "off", "via": "den_lighting_status"},
                                            "den_lighting_status")]

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_shadow_beside_synthetic_and_replay(self):
        header, log = runner.run(self.inputs(), runner.load_policies(["incumbent", "decide"]), plant=False,
                                 actuation="shadow")
        self.assertEqual(header["actuation"], "shadow")
        decisions = [e for e in log if e["kind"] == "decision"]
        self.assertTrue(decisions and all(e["data"]["actuation"] == "shadow" for e in decisions))
        self.assertTrue(check.check(self.write(header, log))["ok"])
        for kwargs in ({"plant": True, "actuation": "shadow"}, {"synthetic": True, "plant": False, "actuation": "shadow"},
                       {"plant": False, "actuation": "shown"}, {"actuation": "replay", "synthetic": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                runner.run(self.inputs(), [], **kwargs)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_run_py_flags(self):
        model = self.tmp / "light-model.json"
        model.write_bytes(json.dumps(real_model(), indent=1).replace("\n", "\r\n").encode("ascii"))  # CRLF reads too
        status = self.tmp / "status.jsonl"
        rows = [{"last_updated_ts": T0, "entity_id": "sensor.den_lighting_control", "state": "occupancy",
                 "attributes": {"automatic": True, "gaming_session": False, "occupancy": "on", "revision": 3}}]
        status.write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
        out = io.StringIO()
        with redirect_stdout(out):
            run.main(["--status", str(status), "--no-plant", "--actuation", "shadow", "--light-model", str(model),
                      "--compensation", "P1", "--policies", "incumbent", "decide", "--out", str(self.tmp / "a")])
        header, _ = events.read(self.tmp / "a" / "log.jsonl")
        config = next(p["config"] for p in header["policies"] if p["id"] == "decide")
        self.assertEqual((header["actuation"], config["light_model"]["version"], config["compensation"]["option"]),
                         ("shadow", "test-lm-1", "P1"))
        self.assertTrue(check.check(self.tmp / "a" / "log.jsonl")["ok"])
        for argv in (["--synthetic", "evening", "--actuation", "shadow"],
                     ["--status", str(status), "--actuation", "shadow"],  # the plant is on
                     ["--status", str(status), "--light-model", str(model), "--policies", "incumbent"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run.main(argv + ["--out", str(self.tmp / "refused")])
        self.assertFalse((self.tmp / "refused").exists())


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ScoreTest(Temp):
    def gate(self, **floors):
        gate = json.loads(score.GATE.read_text("utf-8"))
        for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
            entry.update(value=entry["proposed"], status="set")
        for name, value in floors.items():
            gate["surrogates"][name]["floor"]["value"] = value
        gate["parameters"]["compensation"]["value"] = "none-v0"
        path = self.tmp / "gate.json"
        path.write_text(json.dumps(gate), "ascii")
        return path

    def scored_log(self, name="scored.jsonl"):
        """A non-synthetic log (a test-only construct) with decide's slots filled and a non-fake model."""
        configs = {"decide": {"slots": filled(decide.DEFAULT_CONFIG["slots"]), "light_model": real_model()}}
        return self.write(*evening_log(synthetic_flag=False, configs=configs, touch=True, game=True), name=name)

    def test_refuses_a_scored_run_with_every_reason(self):
        path = self.write(*evening_log(touch=True))
        record, reasons = score.score(path)
        self.assertIsNone(record)
        text = "\n".join(reasons)
        for part in ("a synthetic log", "open slot(s) in the gate: parameters.anticipation_rise",
                     "open slot(s) in decide's logged config: compensation_form", "task_points",
                     "the light model is a fake built from rung 8's plan (fake-plan-v0)"):
            self.assertIn(part, text)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(score.main([str(path)]), 2)
        self.assertTrue(out.getvalue().startswith("refused: "))

    def test_plumbing_labels_everything_and_the_incumbent_scores_0_and_1(self):
        path = self.write(*evening_log(touch=True, game=True))
        record, reasons = score.score(path, plumbing=True)
        self.assertTrue(reasons)
        self.assertEqual(record["plumbing"], score.PLUMBING)
        self.assertIsNone(record["verdict"])
        arms = record["surrogates"]["arms"]
        self.assertEqual((arms["incumbent"]["anticipation"]["rate"], arms["incumbent"]["stability"]["rate"]), (0.0, 1.0))
        self.assertEqual((arms["candidate"]["anticipation"]["rate"], arms["candidate"]["stability"]["rate"]), (1.0, 1.0))
        self.assertEqual(arms["candidate"]["anticipation"]["scored"], 2)  # need's two reading blocks
        self.assertTrue(all(c["ok"] for c in record["checks"].values()), record["checks"])
        self.assertEqual(record["reported"]["status"], "n/a: no marks given (--marks)")
        self.assertTrue(all(line.startswith(("[plumbing]", "verdict: none")) for line in score.lines(record)))

    def test_a_filled_gate_passes_or_fails_on_its_floors(self):
        path = self.scored_log()
        record, reasons = score.score(path, self.gate())
        self.assertEqual(reasons, [])
        self.assertEqual(record["verdict"], "pass", record["checks"])
        record, _ = score.score(path, self.gate(stability=1.01))
        self.assertEqual(record["verdict"], "fail")
        self.assertFalse(record["checks"]["stability"]["ok"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(score.main([str(path), "--gate", str(self.gate()), "--out", str(self.tmp / "s.json")]), 0)
        self.assertEqual(json.loads((self.tmp / "s.json").read_text("ascii"))["verdict"], "pass")

    def test_another_compensation_option_is_refused(self):
        gate = json.loads(self.gate().read_text("ascii"))
        gate["parameters"]["compensation"]["value"] = "P1"
        (self.tmp / "p1.json").write_text(json.dumps(gate), "ascii")
        _, reasons = score.score(self.scored_log(), self.tmp / "p1.json")
        self.assertEqual(reasons, ["the log used compensation none-v0; the gate scores P1"])

    def test_predicted_service_at_marks_with_an_lx_model(self):
        model = real_model()
        model["basis_unit"] = "lx"
        for bulb in model["basis"].values():  # make the relative fake into lux: the normal scene gives the desk
            for basis in bulb.values():      # about 250 lx, under the fine floor, and the reading look about 450
                for p in basis:
                    basis[p] *= 300
        configs = {"decide": {"slots": filled(decide.DEFAULT_CONFIG["slots"]), "light_model": model}}
        path = self.write(*evening_log(synthetic_flag=False, configs=configs, touch=True), name="lx.jsonl")
        marks = self.tmp / "marks.jsonl"
        marks.write_text("\n".join(json.dumps(m) for m in (
            {"mark_id": "m1", "track_id": "room", "start": T0 + 180, "end": T0 + 480, "band": "fine", "points": ["desk-1"]},
            {"mark_id": "m2", "track_id": "room", "start": T0 + 0, "end": T0 + 60, "band": "casual",
             "points": ["armchair-1"]})) + "\n", "ascii")
        record, _ = score.score(path, self.gate(), marks)
        reported = record["reported"]
        self.assertIsNone(reported["status"])
        self.assertEqual({arm: v["label"] for arm, v in reported["arms"].items()},
                         {"candidate": "predicted, replay surrogate", "incumbent": "predicted, replay surrogate"})
        # m2 ends before its 120 s quiet window does, so only m1 is scored; the reading look serves it.
        self.assertGreater(reported["arms"]["candidate"]["coverage"], reported["arms"]["incumbent"]["coverage"])
        self.assertEqual(record["verdict"], "pass")  # reported, never gated


class FixturesTest(unittest.TestCase):
    """V6-V8 and V62-V64 under every option. Their outcomes are not admitted; what holds under every option is."""

    def test_every_case_under_every_option(self):
        report = holds.report()
        self.assertEqual(list(report), ["V6", "V7", "V8", "V62", "V63", "V64"])
        for case, entry in report.items():
            self.assertFalse(entry["admitted"])
            self.assertIn("compensation", entry["pending"])
            self.assertEqual(list(entry["options"]), ["none-v0", "P0", "P1", "P2", "P3"])
        self.assertEqual(report, holds.report())  # deterministic

    def test_what_holds_under_every_option(self):
        for case in holds.CASES:
            for option in holds.OPTIONS:
                with self.subTest(case=case, option=option):
                    fed, out = holds.run_case(case, option)
                    mode = None
                    stream = sorted(fed + out, key=lambda e: e["seq"])
                    for e in stream:
                        if e["kind"] == "mode":
                            mode = e["data"]
                        if e["source"] != "decide":
                            continue
                        d = e["data"]
                        self.assertFalse(mode["gaming"])  # silent in a game
                        for h in mode["holds"]:
                            self.assertEqual(d["requested_scene"][h["entity_id"]], h["pinned"])
                            self.assertIn(h["entity_id"], {s["channel"] for s in d["skipped"]})
                            self.assertEqual(d["applied_scene"][h["entity_id"]]["status"], "held")
                        self.assertEqual(d["hard_conflicts"], [])
                        bounded = option in ("P1", "P2", "P3")
                        self.assertEqual("hold_limited" in d, bounded)
                        self.assertTrue(all(("direction" in h) == bounded for h in d["inputs"]["hold_set"]["held"]))
                        self.assertEqual(all(("changer_track" in h) for h in d["inputs"]["hold_set"]["held"]),
                                         option == "P3" or all("changer_track" in h for h in mode["holds"]))
                        self.assertEqual(decide.replay_exclusions(d["inputs"]), d["inputs"]["exclusions"])

    def test_options_are_distinct_where_the_register_says_they_differ(self):
        """Information for the owner, not admission: P1 leaves B hold-limited in V62 where P3 re-serves B."""
        report = holds.report()["V62"]["options"]
        self.assertTrue(report["P1"][-1]["hold_limited"])
        self.assertEqual(report["P3"][-1]["hold_limited"], [])
        self.assertNotEqual(report["P0"][-1]["scene"], report["none-v0"][-1]["scene"])


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class TimingTest(Temp):
    """Every decision replays, and each takes under 100 ms (A9), aims included: hue-den-4 has none."""

    def timed(self, policy, stream):
        spans, made = [], 0
        for e in stream:
            start = time.perf_counter()
            out = policy.handle(e)
            spans.append(time.perf_counter() - start)
            made += bool(out)
        return max(spans), made

    def test_every_decide_decision_under_100_ms(self):
        for case in holds.CASES:
            for option in holds.OPTIONS:
                policy = decide.make_policy({"compensation": {"option": option}})
                slowest, made = self.timed(policy, [dict(e, seq=n) for n, e in enumerate(holds.events_of(case))])
                with self.subTest(case=case, option=option):
                    self.assertGreater(made, 0)
                    self.assertLess(slowest, 0.1)
        for option in holds.OPTIONS:
            path = self.write(*evening_log(configs={"decide": {"compensation": {"option": option}}}, touch=True,
                                           game=True), name="%s.jsonl" % option)
            result, spans, slowest = score.replay_timed(path)
            with self.subTest(evening=option):
                self.assertTrue(result["ok"], result["reason"])  # every decision replays
                self.assertGreater(len(spans), 5)
                self.assertLess(max(spans), 0.1)

    def test_a_full_size_light_model_stays_under_100_ms(self):
        """Rung 8's grid is 32 by 18 blocks: 576 points per bulb and temperature."""
        model = light_model.fake()
        names = ["b%03d" % n for n in range(576)]
        model["sample_points"] = [{"point_id": p, "kind": None, "xyz": None, "normal": None, "rect": None}
                                  for p in names]
        for e, temps in model["basis"].items():
            for k in temps:
                temps[k] = {p: round(0.001 * ((n * 7 + len(e)) % 997), 6) for n, p in enumerate(names)}
        slots = {"task_points": {"value": {"A": names[:4], "B": names[100:104], "room": names[:4],
                                           "desk-1": names[:4], "armchair-1": names[100:104]}, "status": "set"}}
        for option in holds.OPTIONS:
            policy = decide.make_policy({"light_model": model, "slots": slots, "compensation": {"option": option}})
            slowest, made = self.timed(policy, [dict(e, seq=n) for n, e in enumerate(holds.events_of("V64"))])
            with self.subTest(option=option):
                self.assertGreater(made, 0)
                self.assertLess(slowest, 0.1)


if __name__ == "__main__":
    unittest.main()
