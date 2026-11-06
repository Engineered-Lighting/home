"""Tests for rung 9's coordinator, the light-model adapter and the fake model. From the worktree root:

python -B -m unittest discover -s tools/den-core -p "test_*.py"

Everything is synthetic. Nothing here reaches a host, a camera or a light.
"""
import ast
import copy
import hashlib
import json
import random
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check  # noqa: E402
import coordinator  # noqa: E402
import events  # noqa: E402
import fake_light  # noqa: E402
import hold_fixtures  # noqa: E402
import incumbent  # noqa: E402
import light_adapter  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import score  # noqa: E402
import synthetic  # noqa: E402
import test_live_inputs  # noqa: E402
import test_score  # noqa: E402
from hold_fixtures import T0, SLOTS, person_state, scene  # noqa: E402

FL, FR, RL, RR = events.LIGHTS
HAVE_POLICY = incumbent.find_policy() is not None
GATE = {"id": "test", "sha256": None, "slots": SLOTS, "open": []}
# Whole-log sha256 of the four synthetic evenings at 78a6b6a2, the commit before rung 9: run.py --synthetic evening
# with these flags and --policies need incumbent decide. The header is in the hash, so it also pins decide.py, need.py
# and incumbent.py: an edit to any of them makes every log written before it refuse with `code changed`.
BEFORE_RUNG_9 = {
    (): "6401d2e09c5d06a868b68331471e97bdefd5f50e452a2afb30c02bc36ae62ffe",
    ("--touch",): "73a951b6d0bdce2cdecfc519d2ebfb98206e29b48dbd84a8b40ce250ea51eb12",
    ("--game",): "d67c33fbb454b69ffc9cd440aebde91de58ab142f0e15463b8cf511bb9a8c24f",
    ("--touch", "--game"): "b337678a4bda0885d200d143f2f06537d37ea357c16dcce1bb5e05ed663facef",
}


def policy(**config):
    return coordinator.make_policy({"gate": GATE, **config})


def ev(t, kind, data, source="synthetic"):
    return {"schema": "den-event-1", "t": float(t), "seq": 0, "kind": kind, "source": source, "data": data}


def ps(t, track="A", state="engaged", surface="counter-1"):
    return ev(t, "person_state", person_state(float(t), track, state, surface), "need")


def hold(bulb, b, since, kelvin=2700):
    return {"entity_id": bulb, "pinned": scene(b, kelvin), "since": float(since), "lifetime": "room_vacant_2_minutes"}


def mode(rev=1, holds=(), gaming=False):
    return ev(T0, "mode", {"policy": "den_lighting@1.0.4", "revision": rev, "gaming": gaming, "occupancy": "on",
                           "holds": list(holds), "normal": {b: scene(180) for b in events.LIGHTS}}, "incumbent")


def start(p):
    """Four lit bulbs, a mode, occupancy on: the plan is ready for what the test does next."""
    out = []
    for b in events.LIGHTS:
        out += p.handle(ev(T0, "light_observed", {"entity_id": b, **scene(180), "context": "unknown"}))
    out += p.handle(mode())
    out += p.handle(ev(T0 + 1, "occupancy", {"value": "on", "via": "t"}))
    return [o["data"] for o in out]


def feed(p, *items):
    return [o["data"] for item in items for o in p.handle(item)]


def touch(t, bulb, b, who="A"):
    """What the runner hands the coordinator for a person's touch: the incumbent's mode, then the correction itself."""
    return [ev(t, "mode", mode(2, [hold(bulb, b, t)])["data"], "incumbent"),
            ev(t, "correction", {"correction_id": "c", "entity_id": bulb, "after": scene(b), "source": "t",
                                 "who": {"track_id": who}})]


class FakeModelTest(unittest.TestCase):
    def setUp(self):
        self.model = fake_light.build()

    def one(self, entity_id, level, kelvin):
        return fake_light.predict({fake_light.channel_key(entity_id): {"level": level, "cct_k": kelvin}}, self.model)

    def test_full_output_is_the_basis(self):
        out = self.one(FL, 1.0, 2700)
        self.assertEqual(out["surfaces"]["unit"], "lx")
        self.assertEqual((out["version"], out["daylight"]), (fake_light.VERSION, "excluded"))
        mean = sum(out["surfaces"]["counter-1/%d" % n] for n in range(4)) / 4
        self.assertAlmostEqual(mean, 300.0, places=6)

    def test_dimming_is_linear_between_levels_and_zero_when_off(self):
        at_96, at_128 = fake_light._curve(96), fake_light._curve(128)
        # brightness 112 is halfway between the fitted levels 96 and 128
        out = self.one(FL, 112 / 254, 2700)["surfaces"]["counter-1/0"]
        self.assertAlmostEqual(out, 300.0 * fake_light._ripple(0) * (at_96 + at_128) / 2, places=6)
        self.assertEqual(self.one(FL, 0, 2700)["surfaces"]["counter-1/0"], 0.0)

    def test_temperature_is_linear_in_mired_and_clamped(self):
        warm, cool = self.one(RL, 1.0, 2700)["surfaces"]["armchair-book/0"], self.one(RL, 1.0, 6500)["surfaces"]["armchair-book/0"]
        middle = 1e6 / ((1e6 / 2700 + 1e6 / 6500) / 2)
        self.assertAlmostEqual(self.one(RL, 1.0, middle)["surfaces"]["armchair-book/0"], (warm + cool) / 2, places=6)
        self.assertEqual(self.one(RL, 1.0, 9000)["surfaces"]["armchair-book/0"], cool)
        self.assertEqual(self.one(RL, 1.0, 1800)["surfaces"]["armchair-book/0"],
                         self.one(RL, 1.0, 2202)["surfaces"]["armchair-book/0"])

    def test_refuses_an_unknown_channel_and_an_on_with_no_temperature(self):
        with self.assertRaises(ValueError):
            fake_light.predict({"nope.white": {"level": 1, "cct_k": 2700}}, self.model)
        with self.assertRaises(ValueError):
            fake_light.predict({fake_light.channel_key(FL): {"level": 1, "cct_k": None}}, self.model)

    def test_a_block_the_fit_excluded_is_none(self):
        self.model["basis"][fake_light.channel_key(FL) + "@2700K"]["counter-1/0"] = None
        self.assertIsNone(self.one(FL, 1.0, 2700)["surfaces"]["counter-1/0"])


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.light = light_adapter.LightModel(fake_light.build())

    def test_hue_scenes_become_rung_8_scenes(self):
        with mock.patch.object(light_adapter, "_predict", wraps=light_adapter._predict) as call:
            self.light.predict({FL: scene(127, 2700)})
        (rung8_scene, _), _ = call.call_args
        self.assertEqual(rung8_scene, {"den_front_left.white": {"level": 0.5, "cct_k": 2700}})

    def test_255_is_full_and_off_gives_no_light(self):
        self.assertAlmostEqual(self.light.predict({FL: scene(255)})["counter-1"], 300.0)
        self.assertEqual(self.light.predict({FL: {"on": False, "brightness": None, "kelvin": None}})["counter-1"], 0.0)

    def test_no_level_is_none_never_a_guess(self):
        total = self.light.predict({FL: scene(180), FR: {"on": True, "brightness": None, "kelvin": 2700}})
        self.assertIsNone(total["counter-1"])
        self.assertIsNone(self.light.patch({FR: {"on": True, "brightness": None, "kelvin": None}}, "counter-1"))

    def test_the_sum_over_channels_is_the_model_on_the_whole_scene(self):
        whole = {b: scene(150 + n * 20, 2700 + n * 500) for n, b in enumerate(events.LIGHTS)}
        direct = fake_light.predict({fake_light.channel_key(b): {"level": s["brightness"] / 254, "cct_k": s["kelvin"]}
                                     for b, s in whole.items()}, self.light.model)["surfaces"]
        for surface, blocks in self.light.surfaces.items():
            self.assertAlmostEqual(self.light.predict(whole)[surface], sum(direct[p] for p in blocks) / len(blocks), places=6)

    def test_every_surface_map_keeps_room_and_a_real_model_with_none_has_only_room(self):
        self.assertIn(light_adapter.ROOM, self.light.surfaces)
        bare = light_adapter.LightModel(fake_light.build(surfaces=False))
        self.assertEqual(list(bare.surfaces), [light_adapter.ROOM])
        mapped = light_adapter.LightModel(fake_light.build(surfaces=False), surfaces={"desk": ["counter-1/0", "counter-1/1"]})
        self.assertEqual(sorted(mapped.surfaces), ["desk", light_adapter.ROOM])

    def test_a_map_can_say_what_room_is(self):
        blocks = ["counter-1/0", "counter-1/1"]
        whole = light_adapter.LightModel(fake_light.build(surfaces=False))
        mapped = light_adapter.LightModel(fake_light.build(surfaces=False), surfaces={"room": blocks})
        self.assertEqual(mapped.surfaces, {"room": blocks})
        self.assertEqual(len(whole.surfaces["room"]), 12)
        self.assertGreater(mapped.predict({FL: scene(254)})["room"], whole.predict({FL: scene(254)})["room"])

    def test_a_prediction_can_stop_at_the_surfaces_it_needs(self):
        whole = {b: scene(200) for b in events.LIGHTS}
        some = self.light.predict(whole, ["counter-1"])
        self.assertEqual(some, {"counter-1": self.light.predict(whole)["counter-1"]})
        self.assertEqual(self.light.predict(whole, []), {})

    def test_a_mask_is_the_same_length_for_any_blocks_and_reads_back(self):
        points = self.light.points
        self.assertEqual(self.light.mask([]), "000")
        self.assertEqual(self.light.mask([0]), "001")
        self.assertEqual(self.light.mask(range(12)), "fff")
        self.assertEqual(self.light.unmask(self.light.mask([0, 5, 11])), [points[0], points[5], points[11]])
        self.assertEqual(self.light.unmask("000"), [])

    def test_a_row_is_one_channels_part_at_every_block_in_model_order(self):
        row = self.light.row(FL, scene(254))
        self.assertEqual(len(row), len(self.light.points))
        self.assertAlmostEqual(row[self.light.points.index("counter-1/1")], 300.0 * fake_light._ripple(1))
        self.assertEqual(self.light.row(FL, {"on": False, "brightness": None, "kelvin": None}), [0.0] * 12)
        self.assertIsNone(self.light.row(FL, {"on": True, "brightness": None, "kelvin": 2700}))
        self.assertIsNone(self.light.row(FL, None))
        model = fake_light.build()
        model["basis"][fake_light.channel_key(FL) + "@2700K"]["counter-1/0"] = None
        excluded = light_adapter.LightModel(model).row(FL, scene(254))
        self.assertEqual(excluded[model["sample_points"].index({"point_id": "counter-1/0", "kind": None, "xyz": None, "normal": None})], 0.0)

    def test_only_the_adapter_calls_the_fake(self):
        for path in HERE.glob("*.py"):
            if path.name in ("light_adapter.py", "fake_light.py") or path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                     and n.value.id == "fake_light" and n.attr in ("predict", "build")]
            self.assertEqual(calls, [], path.name)

    def test_every_prediction_of_a_run_goes_through_the_one_call(self):
        with mock.patch.object(light_adapter, "_predict", wraps=light_adapter._predict) as call, \
                mock.patch.object(fake_light, "predict", wraps=fake_light.predict) as raw:
            p = policy()
            start(p)
            feed(p, ps(T0 + 10))
        self.assertGreater(call.call_count, 0)
        self.assertEqual(raw.call_count, call.call_count)

    def test_a_changed_light_model_refuses(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp / "light-model.json"
        path.write_text(json.dumps(fake_light.build()), encoding="utf-8")
        p = policy(light_model={"source": "file", "path": str(path)})
        path.write_text(json.dumps(fake_light.build(unit="relative")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "light model changed"):
            coordinator.make_policy(p.config)

    def test_crlf_and_a_bom_read_the_same(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        lf, crlf = tmp / "lf.json", tmp / "crlf.json"
        text = json.dumps(fake_light.build(), indent=1)
        lf.write_bytes(text.encode("utf-8"))
        crlf.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        a, _ = light_adapter.describe({"source": "file", "path": str(lf)})
        b, _ = light_adapter.describe({"source": "file", "path": str(crlf)})
        self.assertEqual(events.sha256_json(a), events.sha256_json(b))


class DecisionRecordTest(unittest.TestCase):
    def test_carries_requested_applied_recipients_and_inputs(self):
        p = policy()
        start(p)
        [d] = feed(p, ps(T0 + 10))
        self.assertEqual((d["policy"], d["actuator_domain"], d["status"]), ("coordinator@k16-v0", "hue-den-4", "applied"))
        self.assertEqual(set(d["applied_scene"]), set(d["requested_scene"]))
        self.assertEqual(d["hold_limited"], [])
        inputs = d["inputs"]
        self.assertEqual(inputs["person_state"], ["A@%.3f" % (T0 + 10)])
        self.assertEqual(inputs["light_model"], fake_light.VERSION)
        self.assertEqual(inputs["look"], "reading@0")
        self.assertEqual(inputs["preference"]["offsets"], {"level_factor": 1, "mired_shift": 0, "contrast_shift": 0})
        self.assertEqual(inputs["body_envelope"]["parameters"], None)
        for b, entry in d["applied_scene"].items():
            self.assertEqual(entry["status"], "sent")
            self.assertEqual(entry["readback_source"], "simulated: nothing is sent")
            [step] = entry["steps"]
            self.assertEqual((step["sent_at"], step["result"]["at"], step["transition_s"]), (T0 + 10, T0 + 12, 2))
            self.assertEqual((entry["readback"]["level"], entry["readback"]["cct_k"]), (200 / 254, 2700))
            self.assertEqual(inputs["t_exec"][b], T0 + 12)
        self.assertEqual(d["targets"]["A"]["surface_id"], "counter-1")

    def test_a_kept_channel_has_no_t_exec_and_says_why(self):
        p = policy()
        out = start(p)
        kept = out[0]  # four bulbs already at the base scene
        for b, entry in kept["applied_scene"].items():
            self.assertEqual((entry["status"], entry["steps"]), ("sent", []))
            self.assertIsNone(kept["inputs"]["t_exec"][b])
            self.assertIn("pending, freshness", kept["inputs"]["t_exec"][b + "_status"])
        self.assertEqual(kept["transitions"], {})

    def test_a_held_channel_is_held_at_its_pin_and_listed_as_skipped(self):
        p = policy(compensation={"option": "P1", "form": "mode"})
        start(p)
        feed(p, ps(T0 + 10))
        [d] = feed(p, *touch(T0 + 30, FL, 80))
        self.assertEqual(d["applied_scene"][FL]["status"], "held")
        self.assertEqual(d["applied_scene"][FL]["readback"], {**scene(80), "level": 80 / 254, "cct_k": 2700})
        self.assertEqual(d["requested_scene"][FL], scene(80))
        self.assertEqual(d["skipped"][0]["channel"], FL)
        self.assertEqual(d["trigger"], "hold change")
        held = d["inputs"]["hold_set"]["held"][0]
        self.assertEqual(held["direction"], "dim")
        self.assertIn("counter-1", held["footprint"]["surfaces"])

    def test_option_fields_only_under_their_option(self):
        p0 = policy(compensation={"option": "P0"})
        start(p0)
        feed(p0, ps(T0 + 10))
        [d] = feed(p0, *touch(T0 + 30, FL, 80))
        self.assertNotIn("hold_limited", d)
        self.assertNotIn("direction", d["inputs"]["hold_set"]["held"][0])
        p3 = policy(compensation={"option": "P3", "form": "mode"})
        start(p3)
        feed(p3, ps(T0 + 10))
        [d] = feed(p3, *touch(T0 + 30, FL, 80))
        self.assertEqual(d["inputs"]["hold_set"]["held"][0]["changer_track"], "A")
        self.assertIn("hold_limited", d)
        self.assertEqual(d["inputs"]["compensation"], "P3-mode/P1")

    def test_recipients_shares_add_up_and_name_who_is_served(self):
        p = policy()
        start(p)
        feed(p, ps(T0 + 8, "B", "none", "armchair-book"))
        [d] = feed(p, ps(T0 + 10))
        for surface, track in (("counter-1", "A"), ("armchair-book", "B")):
            shares = [row["share"] for r in d["recipients"].values() for row in r["targets"] if row["track_id"] == track]
            self.assertEqual(len(shares), 4)
            self.assertAlmostEqual(sum(shares), 1.0, places=2)
        self.assertEqual(d["recipients"][FL]["served"], ["A"])
        self.assertIn("B", d["recipients"][RL]["served"])
        self.assertNotIn("A", d["recipients"][RL]["served"])
        row = d["recipients"][FL]["targets"][0]
        self.assertEqual((row["kind"], d["recipients"][FL]["light_model"]), ("task", fake_light.VERSION))
        self.assertIsNone(row["interval"])  # no error score yet

    def test_a_share_carries_an_interval_once_the_model_has_an_error(self):
        p = policy(light_model={"source": "fake", "params": {"error": {"median_pct": 8, "p90_pct": 20}}})
        start(p)
        [d] = feed(p, ps(T0 + 10))
        row = d["recipients"][FL]["targets"][0]
        self.assertEqual(row["interval"], [round(row["share"] * 0.8, 4), round(row["share"] * 1.2, 4)])

    def test_a_decision_line_fits_with_two_people_two_holds_and_a_glare_table(self):
        glare = {b: {"A": {"G": 0.3, "sd": 0.01}, "B": {"G": 0.3, "sd": 0.01}} for b in events.LIGHTS}
        p = policy(compensation={"option": "P3", "form": "strict"}, light_model={"source": "fake", "params": {"glare": glare}})
        start(p)
        feed(p, ps(T0 + 8, "B", "engaged", "armchair-book"), ps(T0 + 10))
        out = feed(p, *touch(T0 + 30, FL, 80))
        out += feed(p, ev(T0 + 40, "mode", mode(3, [hold(FL, 80, T0 + 30), hold(RR, 240, T0 + 40)])["data"], "incumbent"),
                    ev(T0 + 40, "correction", {"correction_id": "d", "entity_id": RR, "after": scene(240), "source": "t",
                                               "who": {"track_id": "B"}}))
        for d in out:
            line = events.dumps({"schema": "den-event-1", "t": T0, "seq": 1, "kind": "decision", "source": "decide", "data": d})
            self.assertLess(len(line), events.MAX_LINE_BYTES)


class ExclusionReplayTest(unittest.TestCase):
    def setUp(self):
        glare = {FL: {"A": {"G": 0.6, "sd": 0.02}}, RL: {"A": {"G": 0.01, "sd": 0.0}}}
        self.p = policy(light_model={"source": "fake", "params": {"glare": glare}})
        start(self.p)
        [self.d] = feed(self.p, ps(T0 + 10))

    def test_eyes_reproduce_from_what_the_decision_logged(self):
        d = self.d
        result = {b: e["readback"] for b, e in d["applied_scene"].items()}
        self.assertEqual(coordinator.eye_rows(d["inputs"], result, self.p.light, SLOTS), d["predicted"]["eyes"])
        rows = {r["channel"]: r for r in d["predicted"]["eyes"]}
        self.assertEqual(len(rows), 4)
        # t_exec is the send + the fade; the head's support is one second older than the state.
        self.assertEqual(rows[FL]["support_age"], (T0 + 12) - (T0 + 9))
        self.assertEqual((rows[FL]["G"], rows[FL]["G_hi"]), (round(0.6 * 200 / 254, 6), round(0.6 * 200 / 254 + 2 * 0.02, 6)))
        self.assertEqual((rows[FR]["G"], rows[FR]["limit"], rows[FR]["excluded"]), (None, None, False))

    def test_exclusions_follow_the_logged_fields(self):
        d = copy.deepcopy(self.d)
        result = {b: e["readback"] for b, e in d["applied_scene"].items()}
        d["inputs"]["t_exec"][FL] = T0 + 20
        rows = {r["channel"]: r for r in coordinator.eye_rows(d["inputs"], result, self.p.light, SLOTS)}
        self.assertEqual(rows[FL]["support_age"], (T0 + 20) - (T0 + 9))
        d["inputs"]["person_state"], d["inputs"]["field_support"] = [], {}
        self.assertEqual(coordinator.eye_rows(d["inputs"], result, self.p.light, SLOTS), [])

    def test_an_unknown_envelope_version_refuses(self):
        inputs = {**self.d["inputs"], "body_envelope": {"version": "body-envelope/v9"}}
        with self.assertRaisesRegex(ValueError, "unknown body envelope"):
            coordinator.eye_rows(inputs, {}, self.p.light, SLOTS)

    def test_a_negligible_eye_is_not_exposed_unless_the_head_is_held(self):
        rows = self.d["predicted"]["eyes"]
        self.assertIn("A", self.d["recipients"][FL]["exposed"])  # G_hi over a tenth of the limit
        self.assertEqual(self.d["recipients"][RL]["exposed"], [])  # known, and negligible
        self.assertIn("A", self.d["recipients"][FR]["exposed"])  # no glare number: conservative
        held = [dict(r, support_status="held_unavailable") for r in rows]
        tasks = {"A": "counter-1"}
        scenes = {b: e["readback"] for b, e in self.d["applied_scene"].items()}
        self.assertIn("A", coordinator.recipients_of(scenes, tasks, held, self.p.light, SLOTS)[RL]["exposed"])


class RecordedLevelsTest(unittest.TestCase):
    """HA's recorder keeps on and off but no levels, so a real replay's bulbs and normal scene can have none."""

    def test_a_bulb_with_no_level_never_stops_a_decision_or_gets_a_guess(self):
        p = policy(compensation={"option": "P1", "form": "mode"})
        blank = {"on": True, "brightness": None, "kelvin": None}
        for b in events.LIGHTS:
            p.handle(ev(T0, "light_observed", {"entity_id": b, **blank, "context": "unknown"}))
        p.handle(ev(T0, "mode", {**mode()["data"], "normal": {b: blank for b in events.LIGHTS}}, "incumbent"))
        [base] = feed(p, ev(T0 + 1, "occupancy", {"value": "on", "via": "t"}))
        self.assertEqual(base["requested_scene"][FL], blank)
        self.assertEqual(base["predicted"]["surfaces"]["room"], None)
        [read] = feed(p, ps(T0 + 10))  # the look has levels, so reading is still solved
        self.assertEqual(read["requested_scene"][FR]["brightness"], 200)
        self.assertIsNotNone(read["predicted"]["surfaces"]["counter-1"])
        out = feed(p, *touch(T0 + 30, FL, 80))  # the bulb had no known level before, so the direction is unknown
        self.assertEqual(out[0]["inputs"]["hold_set"]["held"][0]["direction"], "unknown")

    def test_a_held_bulb_with_no_level_leaves_the_total_unknown(self):
        p = policy(compensation={"option": "P0"})
        start(p)
        feed(p, ps(T0 + 10))
        blank = {"entity_id": FL, "pinned": {"on": True, "brightness": None, "kelvin": None}, "since": T0 + 30, "lifetime": "x"}
        [d] = feed(p, ev(T0 + 30, "mode", mode(2, [blank])["data"], "incumbent"),
                   ev(T0 + 30, "correction", {"correction_id": "c", "entity_id": FL, "after": blank["pinned"], "source": "t"}))
        self.assertIsNone(d["predicted"]["surfaces"]["counter-1"])
        self.assertEqual(d["applied_scene"][FL]["status"], "held")


class GlareTest(unittest.TestCase):
    def test_no_glare_table_means_null_numbers_and_no_conflict(self):
        p = policy()
        start(p)
        [d] = feed(p, ps(T0 + 10))
        self.assertEqual(d["hard_conflicts"], [])
        row = d["predicted"]["eyes"][0]
        self.assertEqual((row["G"], row["G_hi"], row["limit"], row["model_status"]), (None, None, None, "unvalidated"))
        self.assertIn("glare null", d["predicted"]["eyes_note"])

    def test_a_free_channel_is_kept_under_the_limit(self):
        # FR at 200 would break the limit: G_hi = 1.5 * 200/254 + 2 * 0.1 = 1.38; the highest step under it is level 0.533
        p = policy(light_model={"source": "fake", "params": {"glare": {FR: {"A": {"G": 1.5, "sd": 0.1}}}}})
        start(p)
        [d] = feed(p, ps(T0 + 10))
        self.assertLessEqual(1.5 * d["requested_scene"][FR]["brightness"] / 254 + 0.2, 1.0)
        look = next(c for c in d["candidates"] if c["scene_id"] == "look:reading@0")
        self.assertIn("rejected: over the glare limit", look["why"])
        self.assertGreaterEqual(look["cost"], coordinator.HARD_PENALTY)
        self.assertEqual(d["hard_conflicts"], [])
        self.assertFalse(any(r["excluded"] for r in d["predicted"]["eyes"]))

    def test_a_rule_scene_is_capped_too(self):
        p = policy(light_model={"source": "fake", "params": {"glare": {FR: {"A": {"G": 2.0, "sd": 0.0}}}}})
        start(p)
        [d] = feed(p, ps(T0 + 10, state="none"))
        self.assertEqual(d["requested_scene"][FR]["brightness"], 127)
        p = policy(light_model={"source": "fake", "params": {"glare": {FR: {"A": {"G": 1.0, "sd": 0.6}}}}})
        start(p)
        [d] = feed(p, ps(T0 + 10, state="none"))
        self.assertEqual(d["requested_scene"][FR]["on"], False)  # the sd alone is over: no level passes

    def test_a_held_light_over_the_limit_is_a_conflict_and_stays_as_set(self):
        glare = {FL: {"A": {"G": 2.0, "sd": 0.0}}, FR: {"A": {"G": 0.5, "sd": 0.0}}}
        p = policy(compensation={"option": "P0"}, light_model={"source": "fake", "params": {"glare": glare}})
        start(p)
        [first] = feed(p, ps(T0 + 10))
        self.assertEqual(first["hard_conflicts"], [])
        [d] = feed(p, *touch(T0 + 30, FL, 254))
        self.assertEqual(d["requested_scene"][FL], scene(254))
        [conflict] = d["hard_conflicts"]
        self.assertEqual(conflict, {"constraint": "glare_at_eyes", "held_channel": FL, "track": "A", "eye_or_envelope": "eye-A",
                                    "G_hi": 2.0, "limit": 1.0, "model_status": "unvalidated"})
        self.assertEqual({r["channel"]: r["excluded"] for r in d["predicted"]["eyes"]}[FL], True)

    def test_free_channels_may_not_raise_their_part_of_a_conflict(self):
        # FL held at 120 is over the limit at A's eye. Reading then wants more light: P0 lets free bulbs make it up, but FR, which
        # also reaches that eye, stays where it is, and RR, which does not, rises.
        glare = {FL: {"A": {"G": 3.0, "sd": 0.0}}, FR: {"A": {"G": 0.5, "sd": 0.0}}}
        p = policy(compensation={"option": "P0"}, light_model={"source": "fake", "params": {"glare": glare}})
        start(p)
        feed(p, ps(T0 + 5, state="none"), *touch(T0 + 10, FL, 120))
        [d] = feed(p, ps(T0 + 20))
        self.assertEqual([c["held_channel"] for c in d["hard_conflicts"]], [FL])
        self.assertEqual(d["requested_scene"][FL], scene(120))
        self.assertLessEqual(d["requested_scene"][FR]["brightness"], 180)
        self.assertGreater(d["requested_scene"][RR]["brightness"], 180)
        self.assertEqual(p.config["manual_vs_safety"], "S0")


class StandInTest(unittest.TestCase):
    def test_the_fixtures_own_slots_are_the_gate_files_names(self):
        self.assertEqual(set(SLOTS), set(coordinator.load_gate()["slots"]))

    def test_a_missing_light_model_refuses_cleanly(self):
        with self.assertRaisesRegex(ValueError, "light model unreadable"):
            policy(light_model={"source": "file", "path": str(HERE / "no-such-model.json")})

    def test_the_seed_look_and_preference_are_logged_in_the_config(self):
        config = policy().config
        self.assertEqual(config["look"]["look_id"], "reading@0")
        self.assertEqual(config["look"]["targets"]["glare_at_eyes"]["limit"], 1.0)
        self.assertEqual(config["preference"]["offsets"], {"level_factor": 1, "mired_shift": 0, "contrast_shift": 0})
        self.assertEqual(config["policy"], "coordinator@k16-v0")

    def test_a_learned_preference_refuses(self):
        with self.assertRaisesRegex(ValueError, "fixed stand-in"):
            policy(preference={"offsets": {"level_factor": 0.8}})

    def test_options_not_built_refuse(self):
        for config, word in (({"manual_vs_safety": "S1"}, "S0"), ({"local_completion": "maybe"}, "local-completion"),
                             ({"gaming": "always"}, "gaming"), ({"compensation": {"option": "P9"}}, "compensation")):
            with self.assertRaisesRegex(ValueError, word):
                policy(**config)

    def test_a_changed_dependency_refuses(self):
        config = copy.deepcopy(policy().config)
        config["code"]["fake_light"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "code changed: fake_light"):
            coordinator.make_policy(config)

    def test_rebuilt_from_its_config_it_hashes_the_same(self):
        p = policy()
        self.assertEqual(events.sha256_json(coordinator.make_policy(p.config).config), events.sha256_json(p.config))

    def test_the_default_config_reads_the_gate_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.object(coordinator, "GATE_FILE", test_score.all_open(tmp)):
            p = coordinator.make_policy()
            gate = coordinator.load_gate()
        self.assertEqual(p.config["gate"]["sha256"], events.sha256_json(gate))
        self.assertEqual(p.config["gate"]["open"], sorted(gate["slots"]))
        self.assertEqual(p.config["compensation"]["option"], gate["slots"]["compensation_option"]["proposed"])

    def real_model(self):
        """A model file that does not call itself fake, as rung 8 writes it: no surfaces of its own."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        model = fake_light.build(surfaces=False)
        del model["fake"]
        path = tmp / "light-model.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        return {"source": "file", "path": str(path)}

    def test_the_gates_surface_map_is_the_run_unless_the_run_names_another(self):
        blocks = {"room": ["counter-1/0"], "desk": ["counter-1/1"]}
        slots = {**SLOTS, "surface_map": blocks}
        gate = {**GATE, "slots": slots}
        spec = self.real_model()
        p = coordinator.make_policy({"gate": gate, "light_model": spec})
        self.assertEqual(p.config["light_model"]["surfaces"], blocks)
        self.assertEqual(p.light.surfaces, blocks)
        self.assertEqual(events.sha256_json(coordinator.make_policy(p.config).config), events.sha256_json(p.config))
        other = {"room": ["counter-1/2"]}
        q = coordinator.make_policy({"gate": gate, "light_model": {**spec, "surfaces": other}})
        self.assertEqual(q.config["light_model"]["surfaces"], other)
        self.assertEqual(q.light.surfaces, other)
        # The proposal names no block: the model's own surfaces stand, as they always have for a preview.
        self.assertNotIn("surfaces", policy().config["light_model"])
        self.assertIn("counter-1", policy().light.surfaces)

    def test_the_gates_map_is_over_rung_8s_blocks_so_a_fake_run_keeps_its_own_surfaces(self):
        # The owner's map names rRRcCC blocks. On the default fake none of them exists, so applied there it left a run with
        # no targets and no light at 'room', and no word from run.py. A fake's surfaces are its own, and a fake is never a gate.
        gate = {**GATE, "slots": {**SLOTS, "surface_map": {"room": ["r00c00", "r00c01"]}}}
        p = coordinator.make_policy({"gate": gate})
        self.assertNotIn("surfaces", p.config["light_model"])
        self.assertIn("counter-1", p.light.surfaces)
        self.assertEqual(len(p.light.surfaces["room"]), 12)
        start(p)
        [d] = feed(p, ps(T0 + 10))
        self.assertEqual(d["targets"]["A"]["surface_id"], "counter-1")
        self.assertIn("reading_look: chose look:reading@0", d["why"])
        self.assertGreater(d["predicted"]["surfaces"]["counter-1"], 0.0)
        # A fake the run named a map for still takes it: that is a preview's own choice.
        named = {"room": ["counter-1/0"]}
        q = coordinator.make_policy({"gate": gate, "light_model": {"source": "fake", "params": {}, "surfaces": named}})
        self.assertEqual(q.light.surfaces, named)
        self.assertEqual(events.sha256_json(coordinator.make_policy(p.config).config), events.sha256_json(p.config))


class PurityTest(unittest.TestCase):
    def test_no_clock_randomness_socket_or_files_in_handle(self):
        def run_it(p):
            start(p)
            return feed(p, ps(T0 + 10), *touch(T0 + 30, FL, 80), ev(T0 + 32, "tick", {}, "runner"))

        expected = [events.dumps(d) for d in run_it(policy())]
        p = policy()
        boom = mock.Mock(side_effect=AssertionError("the coordinator must not touch this"))
        with mock.patch.object(time, "time", boom), mock.patch.object(time, "monotonic", boom), \
                mock.patch.object(random, "random", boom), mock.patch.object(socket, "socket", boom), \
                mock.patch("builtins.open", boom):
            got = [events.dumps(d) for d in run_it(p)]
        self.assertEqual(got, expected)
        boom.assert_not_called()

    def test_imports_are_offline(self):
        allowed = {"copy", "json", "pathlib", "decide", "events", "fake_light", "light_adapter"}
        for name in ("coordinator", "light_adapter", "fake_light"):
            tree = ast.parse((HERE / (name + ".py")).read_text(encoding="utf-8"))
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertLessEqual(names, allowed, name)


class TimingTest(unittest.TestCase):
    """Rung 9: each decision under 100 ms, aims included. On the real grid's 576 blocks, from a cold cache, with a simulated
    spot on one free channel: every Hue bulb has one aim (none), so its aim search is the null path; the spot's is not."""
    AIMS = {FR: {"aim-%02d" % n: {"counter-1": 300.0 - n, "armchair-book": 20.0 + n, "wall-west": 40.0} for n in range(32)}}

    def build(self):
        return policy(light_model={"source": "fake", "params": {"filler": 576 - 12, "spot": self.AIMS}},
                      compensation={"option": "P2", "form": "strict"})

    def scenario(self):
        ticks = [ev(T0 + 2 * k, "tick", {}, "runner") for k in range(1, 40)]
        return (ticks[:4] + [ps(T0 + 10), ps(T0 + 12, "B", "engaged", "armchair-book")] + ticks[4:14]
                + touch(T0 + 30, FL, 80) + ticks[14:20] + touch(T0 + 42, RR, 240) + ticks[20:30]
                + [ps(T0 + 60, "A", "none")] + ticks[30:])

    def round(self):
        p = self.build()
        start(p)
        spans = []
        for item in self.scenario():
            begin = time.perf_counter()
            out = p.handle(item)
            spans.append((time.perf_counter() - begin, bool(out)))
        return spans

    def test_each_decision_is_under_100_ms(self):
        rounds = [self.round() for _ in range(3)]
        self.assertGreaterEqual(sum(made for _, made in rounds[0]), 3)
        fastest = [min(r[n][0] for r in rounds) for n in range(len(rounds[0]))]  # scheduling hiccups are not the code
        self.assertLess(max(fastest), 0.1, "slowest %.1f ms" % (max(fastest) * 1000))

    def test_the_solve_really_searches_aims(self):
        p = self.build()
        start(p)
        [d] = feed(p, ps(T0 + 10))
        solved = next(c["scene"] for c in d["candidates"] if c["scene_id"] == "solve:reading@0")
        self.assertIn(solved[FR]["aim"], self.AIMS[FR])
        self.assertNotIn("aim", solved[FL])
        self.assertEqual(len(p.light.points), 576)

    def test_an_aim_goes_out_in_the_requested_scene(self):
        spot = {FR: {"near": {"counter-1": 900.0, "armchair-book": 0.0, "wall-west": 0.0},
                     "far": {"counter-1": 0.0, "armchair-book": 0.0, "wall-west": 900.0}}}
        p = policy(light_model={"source": "fake", "params": {"spot": spot}}, compensation={"option": "P0"})
        start(p)
        feed(p, ps(T0 + 10))
        [d] = feed(p, *touch(T0 + 30, FL, 10))  # the dim leaves a gap only the spot's near aim can fill
        self.assertEqual(d["requested_scene"][FR]["aim"], "near")
        self.assertEqual(d["applied_scene"][FR]["readback"]["aim"], "near")


class BlockFootprintTest(unittest.TestCase):
    """The register's P1 binds free light "at the points it moved". On rung 8's grid a point is a block, and a surface's mean
    can hide a change at a few of its blocks: a dim reaching 12 of a 144-block surface once gave footprint [] and direction
    unknown, so P1 chose P0's scene and free light rose at the very blocks the person had dimmed."""
    CORNER = 12

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def model(self, reach=150.0, glare=None):
        """Rung 8's 576 blocks with 'desk' at the first 144 and 'bench' at the next 144. Front left reaches only the first
        CORNER blocks of the desk, with `reach` lux at full; the other three reach every block."""
        model = fake_light.build(filler=576 - 12, surfaces=False, glare=glare)
        points = [p["point_id"] for p in model["sample_points"]]
        for key, row in model["basis"].items():
            own = key.startswith("den_front_left")
            for n, point in enumerate(points):
                row[point] = (reach if n < self.CORNER else 0.0) if own else 60.0
        path = self.tmp / ("model-%s.json" % reach)
        path.write_text(json.dumps(model), encoding="utf-8")
        return {"source": "file", "path": str(path), "surfaces": {"desk": points[:144], "bench": points[144:288]}}

    def touched(self, option, after=80, surface="desk", who="A", reach=150.0, form="mode", state="engaged", reader=None):
        """(policy, the Decision of the touch): A at `surface` touches front left, with B reading at `reader` if given."""
        p = policy(light_model=self.model(reach), compensation={"option": option, "form": form})
        start(p)
        feed(p, ps(T0 + 10, "A", state, surface))
        if reader:
            feed(p, ps(T0 + 12, "B", "engaged", reader))
        out = feed(p, *touch(T0 + 30, FL, after, who))
        return p, out[-1]

    def free_at_corner(self, p, requested):
        rows = [p.light.row(b, requested[b]) for b in (FR, RL, RR)]
        return sum(sum(row[:self.CORNER]) for row in rows)

    def test_p1_binds_at_the_blocks_a_dim_moved_though_the_surfaces_mean_hardly_moved(self):
        p0, d0 = self.touched("P0")
        p1, d1 = self.touched("P1")
        look = {b: scene(200) for b in (FR, RL, RR)}
        self.assertGreater(self.free_at_corner(p0, d0["requested_scene"]), self.free_at_corner(p0, look))  # free light rises
        self.assertLessEqual(self.free_at_corner(p1, d1["requested_scene"]), self.free_at_corner(p1, look))  # P1's does not
        self.assertNotEqual(d0["requested_scene"], d1["requested_scene"])
        held = d1["inputs"]["hold_set"]["held"][0]
        self.assertEqual(held["direction"], "dim")
        self.assertEqual((held["footprint"]["points"], held["footprint"]["of"]), (self.CORNER, 576))
        self.assertEqual(p1.light.unmask(held["footprint"]["mask"]), p1.light.points[:self.CORNER])
        self.assertEqual(held["footprint"]["surfaces"], ["desk"])
        _, both = self.touched("P1", reader="bench")  # someone else is tracked at a surface the dim never reached
        self.assertEqual(both["inputs"]["hold_set"]["held"][0]["footprint"]["surfaces"], ["desk"])

    def test_p1_binds_at_the_blocks_a_brighten_moved(self):
        # Front left's blocks go up, so the solve would bring the free bulbs down if only the desk's mean counted.
        p0, d0 = self.touched("P0", after=254, reach=900.0)
        p1, d1 = self.touched("P1", after=254, reach=900.0)
        look = {b: scene(200) for b in (FR, RL, RR)}
        self.assertLess(self.free_at_corner(p0, d0["requested_scene"]), self.free_at_corner(p0, look))
        self.assertGreaterEqual(self.free_at_corner(p1, d1["requested_scene"]), self.free_at_corner(p1, look))
        self.assertEqual(d1["inputs"]["hold_set"]["held"][0]["direction"], "brighten")

    def test_a_block_the_bulb_barely_reaches_is_outside_the_footprint(self):
        spec = self.model()
        model = json.loads(Path(spec["path"]).read_text(encoding="utf-8"))
        for key, row in model["basis"].items():
            if key.startswith("den_front_left"):
                row[model["sample_points"][20]["point_id"]] = 0.5  # a stray: moves well under 5% of the light there
        path = self.tmp / "stray.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        p = policy(light_model={**spec, "path": str(path)}, compensation={"option": "P1", "form": "mode"})
        start(p)
        feed(p, ps(T0 + 10, "A", "engaged", "desk"))
        [d] = feed(p, *touch(T0 + 30, FL, 80))
        self.assertEqual(p.light.unmask(d["inputs"]["hold_set"]["held"][0]["footprint"]["mask"]), p.light.points[:self.CORNER])

    def test_p2_makes_up_part_of_what_p0_makes_up(self):
        # A corner strong enough that the solve wants more than one step at the desk, so the penalty has something to hold back.
        look = {b: scene(200) for b in (FR, RL, RR)}
        rises = {}
        for option in ("P0", "P1", "P2"):
            p, d = self.touched(option, reach=300.0)
            rises[option] = self.free_at_corner(p, d["requested_scene"]) - self.free_at_corner(p, look)
        self.assertEqual(rises["P1"], 0.0)
        self.assertGreater(rises["P2"], rises["P1"])
        self.assertLess(rises["P2"], rises["P0"])

    def test_p3_binds_only_at_the_blocks_of_the_changers_own_surface(self):
        # The dim moved light only at the desk's corner. A at the desk is bound there. A at the bench is not, so B reading at
        # the desk gets it made up, as P1 would refuse.
        mine = self.touched("P3", surface="desk")[1]
        theirs = self.touched("P3", surface="bench", state="none", reader="desk")[1]
        p1 = self.touched("P1", surface="bench", state="none", reader="desk")[1]
        self.assertEqual(mine["inputs"]["hold_set"]["held"][0]["footprint"]["surfaces"], ["desk"])
        self.assertEqual(theirs["inputs"]["hold_set"]["held"][0]["changer_track"], "A")
        self.assertLessEqual(max(mine["requested_scene"][b]["brightness"] for b in (FR, RL, RR)), 200)
        self.assertGreater(max(theirs["requested_scene"][b]["brightness"] for b in (FR, RL, RR)), 200)
        self.assertLessEqual(max(p1["requested_scene"][b]["brightness"] for b in (FR, RL, RR)), 200)

    def test_hold_limited_names_the_target_surface_never_its_blocks(self):
        p, d = self.touched("P1", reach=900.0)
        self.assertEqual([row["point_id"] for row in d["hold_limited"]], ["desk"])
        self.assertIn(FL, d["hold_limited"][0]["reason"])
        self.assertIn("dim", d["hold_limited"][0]["reason"])
        p, d = self.touched("P1", reach=900.0, surface="bench")  # the bench is out of the footprint
        self.assertEqual(d["hold_limited"], [])

    def test_a_shortfall_the_hold_did_not_cause_is_not_hold_limited(self):
        # The glare limit keeps the three free bulbs low, so the bench is short of its target, and the dim never reached it.
        glare = {b: {"A": {"G": 3.0, "sd": 0.0}} for b in (FR, RL, RR)}
        p = policy(light_model=self.model(glare=glare), compensation={"option": "P1", "form": "mode"})
        start(p)
        feed(p, ps(T0 + 10, "A", "engaged", "bench"))
        [d] = feed(p, *touch(T0 + 30, FL, 80))
        self.assertLess(d["predicted"]["surfaces"]["bench"], 0.9 * d["targets"]["A"]["value"])
        self.assertEqual(d["hold_limited"], [])

    def test_a_share_exactly_at_the_threshold_follows_the_membership_rule(self):
        # Front left alone, from off to full, is 1 lx where a lone front right gives 20: a share of exactly 0.05.
        model = fake_light.build(surfaces=False)
        point = model["sample_points"][0]["point_id"]
        for key, row in model["basis"].items():
            for name in row:
                row[name] = 0.0
            row[point] = {"den_front_left": 1.0, "front_right": 20.0}.get(key.split(".")[0], 0.0)
        path = self.tmp / "exact.json"
        path.write_text(json.dumps(model), encoding="utf-8")
        off = {"on": False, "brightness": None, "kelvin": None}
        normal = {FL: off, FR: scene(254), RL: off, RR: off}
        for membership, points in (("at_or_above", 1), ("above", 0)):
            gate = {**GATE, "slots": {**SLOTS, "footprint_membership": membership}}
            p = coordinator.make_policy({"gate": gate, "light_model": {"source": "file", "path": str(path)},
                                         "compensation": {"option": "P1", "form": "mode"}})
            for b in events.LIGHTS:
                p.handle(ev(T0, "light_observed", {"entity_id": b, **normal[b], "context": "unknown"}))
            p.handle(ev(T0, "mode", {**mode()["data"], "normal": normal}, "incumbent"))
            p.handle(ev(T0 + 1, "occupancy", {"value": "on", "via": "t"}))
            hold_mode = {**mode(2, [hold(FL, 254, T0 + 30)])["data"], "normal": normal}
            [d] = feed(p, ev(T0 + 30, "mode", hold_mode, "incumbent"),
                       ev(T0 + 30, "correction", {"correction_id": "c", "entity_id": FL, "after": scene(254), "source": "t"}))
            self.assertEqual(d["inputs"]["hold_set"]["held"][0]["footprint"]["points"], points, membership)

    def test_a_held_bulb_with_no_level_binds_every_block_either_way(self):
        p = policy(light_model=self.model(), compensation={"option": "P1", "form": "mode"})
        start(p)
        feed(p, ps(T0 + 10, "A", "engaged", "desk"))
        blank = {"entity_id": FL, "pinned": {"on": True, "brightness": None, "kelvin": None}, "since": T0 + 30, "lifetime": "x"}
        [d] = feed(p, ev(T0 + 30, "mode", mode(2, [blank])["data"], "incumbent"),
                   ev(T0 + 30, "correction", {"correction_id": "c", "entity_id": FL, "after": blank["pinned"], "source": "t"}))
        held = d["inputs"]["hold_set"]["held"][0]
        self.assertEqual((held["direction"], held["footprint"]["points"]), ("unknown", 576))
        self.assertEqual(held["footprint"]["mask"], "f" * 144)
        self.assertEqual([d["requested_scene"][b]["brightness"] for b in (FR, RL, RR)], [200, 200, 200])  # nothing may move

    def test_every_block_in_every_hold_still_fits_the_line(self):
        p = policy(light_model=self.model(), compensation={"option": "P3", "form": "strict"})
        start(p)
        feed(p, ps(T0 + 10, "A", "engaged", "desk"))
        holds = []
        for n, b in enumerate(events.LIGHTS):
            holds.append({"entity_id": b, "pinned": {"on": True, "brightness": None, "kelvin": None}, "since": T0 + 30 + n, "lifetime": "x"})
            for d in feed(p, ev(T0 + 30 + n, "mode", mode(2 + n, holds)["data"], "incumbent"),
                          ev(T0 + 30 + n, "correction", {"correction_id": "c%d" % n, "entity_id": b, "after": holds[-1]["pinned"], "source": "t",
                                                         "who": {"track_id": "A"}})):
                events.validate(ev(T0 + 30, "decision", {**d, "actuation": "replay"}, "decide"))
        self.assertEqual(len(d["inputs"]["hold_set"]["held"]), 4)
        self.assertTrue(all(row["footprint"]["points"] == 576 for row in d["inputs"]["hold_set"]["held"]))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_it_replays_byte_for_byte(self):
        items, _, _ = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0, touch=True)
        config = {"light_model": self.model(), "compensation": {"option": "P1", "form": "mode"}}
        header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "coordinator"], {"coordinator": config}),
                                 synthetic=True, sources=[])
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        self.assertTrue(check.check(path)["ok"])


def full_grid(per=144, **params):
    """A config's light_model on rung 8's 32 x 18 = 576 blocks, with `per` of them in each of the fake's three surfaces."""
    filler = ["other/%d" % n for n in range(576 - 12)]
    surfaces = {name: blocks + filler[n * 140:n * 140 + per - len(blocks)]
                for n, (name, blocks) in enumerate(sorted(fake_light.build()["surfaces"].items()))}
    return {"source": "fake", "params": {"filler": len(filler), **params}, "surfaces": surfaces}


def longest_decision(log):
    return max(len(events.dumps(e)) for e in log if e["kind"] == "decision" and e["source"] == "decide")


class FullGridLineTest(unittest.TestCase):
    """events.py refuses a line over 16384 bytes, runner.run raises, and run.py writes no log, so a scored run has nothing
    to score. A Decision on the full grid must fit, and every line here goes through what run.py's lines go through."""

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_every_hold_fixture_logs_on_the_full_grid_under_every_option(self):
        for case in hold_fixtures.CASES:
            for option in hold_fixtures.OPTIONS:
                with self.subTest(case=case, option=option):
                    header, log = hold_fixtures.run_case(case, option, light_model=full_grid())
                    self.assertLess(longest_decision(log), events.MAX_LINE_BYTES)
        logged = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]
        self.assertEqual(len(coordinator.make_policy(logged).light.points), 576)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_decision_does_not_grow_with_the_blocks_in_a_surface(self):
        for case in ("V62", "V64"):
            small, large = (longest_decision(hold_fixtures.run_case(case, "P3-strict/P1", light_model=full_grid(per))[1])
                            for per in (4, 144))
            self.assertLess(abs(large - small), 100, case)  # the digits of a mean move a little; no block is listed

    def test_two_people_two_holds_a_glare_table_and_a_spot_fit_on_the_full_grid(self):
        # TimingTest's scenario, which calls handle directly, with every Decision validated as the runner validates it.
        glare = {b: {"A": {"G": 0.3, "sd": 0.01}, "B": {"G": 0.3, "sd": 0.01}} for b in events.LIGHTS}
        p = policy(light_model=full_grid(glare=glare, spot=TimingTest.AIMS), compensation={"option": "P3", "form": "strict"})
        start(p)
        made = 0
        for item in TimingTest.scenario(self):
            for out in p.handle(item):
                events.validate(ev(item["t"], "decision", {**out["data"], "actuation": "replay"}, "decide"))
                made += 1
        self.assertGreaterEqual(made, 3)


class GridLimitLineTest(unittest.TestCase):
    """How fine a grid a Decision's line can carry. Rung 8's block_px is an open slot, proposed 20, and must divide 640 and
    360. Under P1 to P3 each held bulb's footprint is a mask of one hex digit per 4 blocks, and a line cannot shed one, so
    with every bulb held it grows by a byte a block. These pin where README's "A Decision's size" says that stops."""

    REFUSED = r"^decision line over %d bytes$" % events.MAX_LINE_BYTES

    @staticmethod
    def touched(block_px, bulbs=4, option="P1-mode"):
        """One reader and a person's touch on each of these bulbs, through runner.run beside the real den_lighting."""
        case = {"script": [(T0 + 10, "A", "engaged", "counter-1")],
                "touches": [(30 + 10 * n, b, scene(60 + 20 * n)) for n, b in enumerate(events.LIGHTS[:bulbs])], "inputs": []}
        blocks = (640 // block_px) * (360 // block_px)
        with mock.patch.dict(hold_fixtures.CASES, {"touched": case}):
            return hold_fixtures.run_case("touched", option, light_model={"source": "fake", "params": {"filler": blocks - 12}})[1]

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_the_proposed_grid_and_block_px_10_and_8_fit_with_every_bulb_held(self):
        for block_px, blocks in ((20, 576), (10, 2304), (8, 3600)):
            with self.subTest(block_px=block_px):
                log = self.touched(block_px)
                mine = hold_fixtures.decisions(log)
                held = max((d["inputs"]["hold_set"]["held"] for d in mine), key=len)
                self.assertEqual(len(held), 4)
                self.assertEqual({row["footprint"]["of"] for row in held}, {blocks})
                self.assertEqual({len(row["footprint"]["mask"]) for row in held}, {blocks // 4})
                self.assertLess(longest_decision(log), events.MAX_LINE_BYTES)
                self.assertFalse([d for d in mine if "surfaces_not_logged" in d["predicted"]])  # the map stays whole

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_block_px_5_fits_three_held_bulbs_and_refuses_the_fourth_by_name(self):
        self.assertLess(longest_decision(self.touched(5, bulbs=3)), events.MAX_LINE_BYTES)
        with self.assertRaisesRegex(ValueError, self.REFUSED):
            self.touched(5)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_finer_grid_refuses_sooner(self):
        self.assertLess(longest_decision(self.touched(4, bulbs=2)), events.MAX_LINE_BYTES)
        with self.assertRaisesRegex(ValueError, self.REFUSED):
            self.touched(4, bulbs=3)
        with self.assertRaisesRegex(ValueError, self.REFUSED):
            self.touched(2, bulbs=1)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_p0_writes_no_mask_so_it_has_no_such_limit(self):
        log = self.touched(5, option="P0")
        self.assertEqual(max(len(d["inputs"]["hold_set"]["held"]) for d in hold_fixtures.decisions(log)), 4)
        self.assertFalse([d for d in hold_fixtures.decisions(log) if "footprint" in events.dumps(d["inputs"]["hold_set"])])

    def test_readme_and_the_fragment_state_the_limit_in_block_px(self):
        # Both once said a Decision's size did not depend on the grid, which held only on the grid they were measured on.
        said = (HERE / "README.md").read_text("utf-8").split("### A Decision's size")[1].split("\n### ")[0]
        said = " ".join(said.split())
        self.assertNotIn("the same length for any blocks", said)
        for words in ("one hex digit per 4 blocks", "`block_px` 8 or coarser", "`block_px` 5", "decision line over 16384 bytes"):
            self.assertIn(words, said)
        fragment = HERE.parents[1] / "changes" / "unreleased" / "den-core-rung-9-coordinator.md"
        if fragment.exists():  # a release moves it to the archive
            said = " ".join(fragment.read_text("utf-8").split())
            self.assertNotIn("whatever the grid", said)
            for words in ("one hex digit per 4 blocks", "`block_px` 8", "`block_px` 5", "decision line over 16384 bytes"):
                self.assertIn(words, said)


class WideMapLineTest(unittest.TestCase):
    """A surface map's width is the owner's. With every bulb held, a map once broke the line at 120 to 180 surfaces with
    10-character names and 40 to 60 with 30-character ones, and the run wrote no log."""

    def wide(self, count, width):
        blocks = ["other/%d" % n for n in range(576 - 12)]
        surfaces = {"s" + str(n).zfill(width - 1): [blocks[n % len(blocks)]] for n in range(count)}
        return {"source": "fake", "params": {"filler": len(blocks)}, "surfaces": {**surfaces, "room": blocks[:144]}}

    def every_bulb_held(self, p, surface):
        out = feed(p, ps(T0 + 5, "A", "engaged", surface), ps(T0 + 6, "B", "none", "room"))
        holds = []
        for n, b in enumerate(events.LIGHTS):
            holds.append(hold(b, 60 + 20 * n, T0 + 10 + n))
            out += feed(p, ev(T0 + 10 + n, "mode", mode(2 + n, holds)["data"], "incumbent"),
                        ev(T0 + 10 + n, "correction", {"correction_id": "c%d" % n, "entity_id": b, "after": scene(60 + 20 * n), "source": "t",
                                                       "who": {"track_id": "A"}}))
        return out

    def test_a_map_of_any_width_fits_with_every_bulb_held(self):
        for count, width in ((180, 10), (60, 30), (576, 10), (576, 30)):
            for option in ("P1", "P2", "P3"):
                with self.subTest(count=count, width=width, option=option):
                    spec = self.wide(count, width)
                    p = policy(light_model=spec, compensation={"option": option, "form": "strict"})
                    start(p)
                    out = self.every_bulb_held(p, next(iter(spec["surfaces"])))
                    self.assertEqual(len(out[-1]["inputs"]["hold_set"]["held"]), 4)
                    for d in out:
                        events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))

    def test_one_surface_per_block_fits(self):
        model = fake_light.build(filler=576 - 12, surfaces=False)
        names = [p["point_id"] for p in model["sample_points"]]
        spec = {"source": "fake", "params": {"filler": 576 - 12, "surfaces": False}, "surfaces": {"block-%s" % n: [n] for n in names}}
        p = policy(light_model=spec, compensation={"option": "P1", "form": "mode"})
        start(p)
        for d in self.every_bulb_held(p, "block-counter-1/0"):
            events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))
        self.assertLessEqual({"block-counter-1/0", "room", "unit"}, set(d["predicted"]["surfaces"]))
        self.assertGreater(d["predicted"]["surfaces_not_logged"], 0)

    def test_a_decision_lists_the_whole_map_while_it_fits(self):
        # rung 10 takes its verify points from these keys, so a surface nobody is at can only be missed if it is listed.
        whole = ["armchair-book", "counter-1", "room", "unit", "wall-west"]
        p = policy()
        self.assertEqual(sorted(start(p)[0]["predicted"]["surfaces"]), whole)  # nobody tracked yet
        [d] = feed(p, ps(T0 + 5, "A", "engaged", "counter-1"))
        self.assertEqual(sorted(d["predicted"]["surfaces"]), whole)
        self.assertNotIn("surfaces_not_logged", d["predicted"])
        at = p.light.predict(d["requested_scene"])
        self.assertEqual(d["predicted"]["surfaces"], {**{s: round(at[s], 3) for s in at}, "unit": "lx"})

    def test_an_ordinary_map_is_listed_whole_with_every_bulb_held(self):
        for count, width in ((180, 10), (60, 30)):  # the sizes that once broke the line
            with self.subTest(count=count, width=width):
                spec = self.wide(count, width)
                p = policy(light_model=spec, compensation={"option": "P1", "form": "strict"})
                start(p)
                d = self.every_bulb_held(p, next(iter(spec["surfaces"])))[-1]
                events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))
                self.assertEqual(set(d["predicted"]["surfaces"]) - {"unit"}, set(spec["surfaces"]))
                self.assertNotIn("surfaces_not_logged", d["predicted"])

    def test_a_map_too_wide_keeps_each_tracked_surface_and_counts_the_rest(self):
        spec = self.wide(576, 30)
        spec["surfaces"].update({"z": ["other/0"], "zz": ["other/1"]})  # short, but past where the line ran out, so it stays off
        names = sorted(spec["surfaces"])
        target = names[-1]  # the last by name, so only being someone's surface can keep it
        p = policy(light_model=spec, compensation={"option": "P1", "form": "strict"})
        start(p)
        d = self.every_bulb_held(p, target)[-1]
        events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))
        self.assertNotIn("tracks_not_logged", d["inputs"])  # surfaces nobody is at go before any track
        listed = set(d["predicted"]["surfaces"]) - {"unit"}
        self.assertLessEqual({"room", target}, listed)
        others = [name for name in names if name not in ("room", target)]
        kept = sorted(listed - {"room", target})
        self.assertEqual(kept, others[:len(kept)])
        self.assertEqual(d["predicted"]["surfaces_not_logged"], len(others) - len(kept))
        self.assertGreater(len(kept), 100)
        self.assertLessEqual(len(events.dumps(d)), coordinator.LINE_ROOM)
        self.assertLess(coordinator.LINE_ROOM - len(events.dumps(d)), 50)  # one more would not have fit
        result = {b: e["readback"] for b, e in d["applied_scene"].items()}
        at = p.light.predict(result)
        for name in listed:
            self.assertEqual(d["predicted"]["surfaces"][name], round(at[name], 3))

    def test_a_lone_track_or_none_on_a_map_too_wide_fits(self):
        spec = self.wide(576, 30)
        p = policy(light_model=spec)
        out = start(p) + feed(p, ps(T0 + 5, "A", "engaged", "room"))
        self.assertTrue(out)
        for d in out:
            events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))
            self.assertGreater(d["predicted"]["surfaces_not_logged"], 0)
            self.assertNotIn("tracks_not_logged", d["inputs"])

    def test_a_wide_map_stays_under_100_ms(self):
        def rounds():
            p = policy(light_model=self.wide(576, 30), compensation={"option": "P1", "form": "strict"})
            start(p)
            spans = []
            for item in TimingTest.scenario(self):
                begin = time.perf_counter()
                p.handle(item)
                spans.append(time.perf_counter() - begin)
            return spans
        fastest = [min(column) for column in zip(rounds(), rounds(), rounds())]
        self.assertLess(max(fastest), 0.1, "slowest %.1f ms" % (max(fastest) * 1000))


class ManyTracksLineTest(unittest.TestCase):
    """A track id is never reused and a PersonState says nothing when its track ends, so the coordinator keeps every track
    it has seen. Seven once broke the line, and the run wrote no log. need v0 has one track; rungs 5 and 6 would get there."""
    FIELDS = ("position", "working_region", "attention", "heading", "posture", "motion", "visual_demand", "focused_engagement")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def full_state(self, t, track, surface, state="engaged"):
        """A PersonState with every field_support the schema names, each with every key."""
        person = person_state(t, track, state, surface)
        person["field_support"] = {name: {"last_support_capture_ts": t - 1.0, "value_set_ts": t, "support_status": "observed",
                                          "hold_reason": "association", "deadband": 0.1, "cov_ts": t - 1.0} for name in self.FIELDS}
        return person

    def valid(self, decisions):
        for d in decisions:
            events.validate(ev(T0, "decision", {**d, "actuation": "replay"}, "decide"))
        return decisions

    def seen(self, n, reading=(), config=None, where=lambda k: "counter-1"):
        p = policy(compensation={"option": "P1", "form": "mode"}, **(config or {}))
        start(p)
        out = []
        for k in range(n):
            track = "T%d" % k
            out += self.valid(feed(p, ev(T0 + 2 + k, "person_state",
                                         self.full_state(T0 + 2 + k, track, where(k), "engaged" if k in reading else "none"), "need")))
        return p, out

    def tracks(self, d):
        return [state_id.split("@")[0] for state_id in d["inputs"]["person_state"]]

    def test_a_decision_fits_however_many_tracks_were_seen(self):
        p, out = self.seen(50, reading=[49])
        d = out[-1]
        self.assertEqual(d["targets"]["T49"]["surface_id"], "counter-1")
        self.assertIn("T49", self.tracks(d))
        self.assertGreater(d["inputs"]["tracks_not_logged"], 0)
        self.assertEqual(len(self.tracks(d)) + d["inputs"]["tracks_not_logged"], 50)

    def test_few_tracks_leave_nothing_out(self):
        _, out = self.seen(2, reading=[1])
        self.assertEqual(self.tracks(out[-1]), ["T0", "T1"])
        for d in out:
            self.assertNotIn("tracks_not_logged", d["inputs"])

    def test_the_tracks_longest_unheard_from_go_first_and_readers_last(self):
        p, _ = self.seen(30, reading=[0])  # T0 reads and is the oldest; the rest idle
        feed(p, ev(T0 + 40, "person_state", self.full_state(T0 + 40, "T1", "counter-1", "none"), "need"))  # T1 is heard from again
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T0")))
        self.assertIn("T0", self.tracks(d))
        self.assertIn("T29", self.tracks(d))
        self.assertIn("T1", self.tracks(d))
        self.assertNotIn("T2", self.tracks(d))

    def test_what_a_decision_logs_of_a_track_is_all_or_nothing(self):
        glare = {b: {"T%d" % k: {"G": 3.0, "sd": 0.01} for k in range(30)} for b in events.LIGHTS}
        p, _ = self.seen(30, reading=[29], config={"light_model": {"source": "fake", "params": {"glare": glare}}})
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 200, "T29")))  # held over the limit, so a conflict at every eye
        kept = set(self.tracks(d))
        self.assertLess(len(kept), 30)
        self.assertEqual(set(state.split("@")[0] for state in d["inputs"]["field_support"]), kept)
        self.assertEqual({row["track_id"] for row in d["predicted"]["eyes"]}, kept)
        self.assertEqual({row["track_id"] for r in d["recipients"].values() for row in r["targets"]}, kept)
        self.assertEqual({row["track"] for row in d["hard_conflicts"]}, kept)
        self.assertLessEqual(set(d["targets"]), kept)
        result = {b: e["readback"] for b, e in d["applied_scene"].items()}
        self.assertEqual(coordinator.eye_rows(d["inputs"], result, p.light, SLOTS), d["predicted"]["eyes"])

    def test_a_surface_only_a_left_out_track_was_at_leaves_the_tracks_lists(self):
        # The oldest readers are at the armchair and the newest at the counter, so only the armchair's readers go.
        p, _ = self.seen(30, reading=range(30), where=lambda k: "armchair-book" if k < 15 else "counter-1")
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T29")))
        self.assertEqual({row["surface_id"] for row in d["targets"].values()}, {"counter-1"})
        self.assertEqual([row["point_id"] for row in d["hold_limited"]], ["counter-1"])
        self.assertEqual(d["inputs"]["hold_set"]["held"][0]["footprint"]["surfaces"], ["counter-1"])
        self.assertLessEqual({"counter-1", "room", "unit"}, set(d["predicted"]["surfaces"]))
        self.assertLess(d["predicted"]["surfaces"]["counter-1"], d["targets"]["T29"]["value"])

    def wide_map_seen(self, near, far):
        """30 tracks on a map too wide for the line. The oldest are at `far`, the newest at `near`."""
        blocks = ["other/%d" % n for n in range(576 - 12)]
        surfaces = {"a-surface-with-a-thirty-char-%03d" % n: [blocks[n % len(blocks)]] for n in range(576)}
        surfaces.update({near: blocks[:4], far: blocks[4:8]})
        spec = {"source": "fake", "params": {"filler": len(blocks)}, "surfaces": surfaces}
        p, _ = self.seen(30, reading=range(30), config={"light_model": spec}, where=lambda k: far if k < 15 else near)
        return p, surfaces

    def test_what_room_the_tracks_leave_goes_back_to_the_map(self):
        p, surfaces = self.wide_map_seen("a-near", "z-far")
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T29")))
        self.assertGreater(d["inputs"]["tracks_not_logged"], 0)
        listed = set(d["predicted"]["surfaces"]) - {"unit"}
        self.assertEqual(len(listed) + d["predicted"]["surfaces_not_logged"], len(surfaces) + 1)  # 'room' as well
        self.assertLess(coordinator.LINE_ROOM - len(events.dumps(d)), 50)  # the line is full, not cut short at the tracks

    def test_a_left_out_tracks_surface_is_owed_no_place_on_a_full_line(self):
        p, _ = self.wide_map_seen("a-near", "z-far")  # z-far sorts last, so only a track there could have kept it
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T29")))
        self.assertIn("a-near", d["predicted"]["surfaces"])
        self.assertIn("room", d["predicted"]["surfaces"])  # after every a-surface by name, so it stays by rule
        self.assertNotIn("z-far", d["predicted"]["surfaces"])
        self.assertEqual({row["surface_id"] for row in d["targets"].values()}, {"a-near"})

    def test_a_map_that_fits_again_once_tracks_are_gone_has_no_count(self):
        p, out = self.seen(50, reading=[49])
        self.assertTrue(any("tracks_not_logged" in d["inputs"] for d in out))
        self.assertFalse(any("surfaces_not_logged" in d["predicted"] for d in out if "tracks_not_logged" in d["inputs"]))

    def test_as_few_tracks_as_will_do_are_left_out_and_never_the_last(self):
        p, _ = self.seen(30, reading=[29])
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T29")))
        self.assertEqual(self.tracks(d), sorted("T%d" % k for k in range(30 - len(self.tracks(d)), 30)))
        kept = {**d, "inputs": {**d["inputs"], "tracks_not_logged": 0}}
        self.assertLess(len(events.dumps(kept)), coordinator.LINE_ROOM)
        self.assertGreater(len(events.dumps(kept)) + 2500, coordinator.LINE_ROOM)  # one more track would not have fit
        lone = policy()
        start(lone)
        name = "A" * 20000  # no use on any line, but it is the only track there is
        [d] = feed(lone, ev(T0 + 2, "person_state", person_state(T0 + 2, name, "engaged", "counter-1"), "need"))
        self.assertEqual(self.tracks(d), [name])
        self.assertNotIn("tracks_not_logged", d["inputs"])

    def test_hundreds_of_tracks_stay_under_100_ms(self):
        def rounds():
            p, _ = self.seen(300, reading=[299])
            spans = []
            for item in touch(T0 + 400, FL, 80, "T299") + [ps(T0 + 410, "T299", "none"), ps(T0 + 420, "T299", "engaged")]:
                begin = time.perf_counter()
                self.valid([o["data"] for o in p.handle(item)])
                spans.append(time.perf_counter() - begin)
            return spans
        fastest = [min(column) for column in zip(rounds(), rounds(), rounds())]
        self.assertLess(max(fastest), 0.1, "slowest %.1f ms" % (max(fastest) * 1000))

    def test_the_room_left_on_the_line_covers_the_runners_envelope(self):
        event = ev(T0 + 1234567.125, "decision", {"actuation": "synthetic"}, "decide")
        event["seq"] = 10 ** 9
        self.assertLessEqual(coordinator.LINE_ROOM + len(events.dumps(event)), events.MAX_LINE_BYTES)

    def test_a_track_left_out_of_the_line_is_still_in_the_plan(self):
        # Only the oldest track's eye is near front right, so only the plan can know to keep that bulb under the limit.
        glare = {FR: {"T0": {"G": 1.5, "sd": 0.1}}}
        p, _ = self.seen(30, reading=[29], config={"light_model": {"source": "fake", "params": {"glare": glare}}})
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 80, "T29")))
        self.assertNotIn("T0", self.tracks(d))
        self.assertLessEqual(1.5 * d["requested_scene"][FR]["brightness"] / 254 + 0.2, 1.0)

    def glare_at(self, bulb, *tracks):
        return {"light_model": {"source": "fake", "params": {"glare": {bulb: {track: {"G": 3.0, "sd": 0.01} for track in tracks}}}}}

    def test_a_track_a_held_light_is_over_the_limit_at_stays_on_the_line(self):
        # T0 is the oldest and never reads, so it would be the first off the line, and its conflict with it.
        p, _ = self.seen(30, reading=[29], config=self.glare_at(FL, "T0"))
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 240, "T29")))
        self.assertGreater(d["inputs"]["tracks_not_logged"], 0)
        self.assertIn("T0", self.tracks(d))
        self.assertEqual([(row["held_channel"], row["track"]) for row in d["hard_conflicts"]], [(FL, "T0")])
        self.assertNotIn("over_limit_not_logged", d["inputs"])

    def test_over_the_limit_means_a_conflict_or_an_excluded_row(self):
        # They differ at the edge: predicted.eyes rounds G_hi to six places and a conflict does not.
        record = {"hard_conflicts": [{"held_channel": FL, "track": "A"}],
                  "predicted": {"eyes": [{"channel": FR, "track_id": "B", "excluded": True}, {"channel": RL, "track_id": "C", "excluded": False}]}}
        self.assertEqual(coordinator._over_limit(record), {(FL, "A"), (FR, "B")})

    def test_a_track_in_conflict_outlasts_a_reader(self):
        p, _ = self.seen(30, reading=range(1, 30), config=self.glare_at(FL, "T0"))
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 240, "T29")))
        self.assertGreater(d["inputs"]["tracks_not_logged"], 0)
        self.assertNotIn("T1", self.tracks(d))  # the reader longest unheard from
        self.assertIn("T0", self.tracks(d))

    def test_conflicts_that_cannot_all_fit_are_counted(self):
        names = ["T%d" % k for k in range(30)]
        p, _ = self.seen(30, reading=[29], config=self.glare_at(FL, *names))
        [d] = self.valid(feed(p, *touch(T0 + 50, FL, 240, "T29")))
        self.assertEqual({row["track"] for row in d["hard_conflicts"]}, set(self.tracks(d)))
        self.assertEqual(d["inputs"]["over_limit_not_logged"], 30 - len(d["hard_conflicts"]))
        self.assertGreater(d["inputs"]["over_limit_not_logged"], 0)

    def superseded_with(self, config):
        """Two plans inside the send latency, so the first's steps never go out and a free channel stays where it was, over the
        limit at an eye, however the plan capped it. The Decision that says so was planned with all 30 tracks."""
        p = policy(compensation={"option": "P1", "form": "mode"}, send_latency_s=30, **config)
        start(p)
        for k in range(30):
            feed(p, ev(T0 + 2 + k * 0.1, "person_state", self.full_state(T0 + 2 + k * 0.1, "T%d" % k, "counter-1", "none"), "need"))
        feed(p, ev(T0 + 10, "person_state", self.full_state(T0 + 10, "T29", "counter-1"), "need"))
        [d] = self.valid(feed(p, ev(T0 + 11, "person_state", self.full_state(T0 + 11, "T28", "armchair-book"), "need")))
        self.assertEqual(d["status"], "superseded")
        return d

    def test_a_free_channel_over_the_limit_keeps_its_track_on_the_line(self):
        d = self.superseded_with(self.glare_at(FR, "T0"))
        self.assertGreater(d["inputs"]["tracks_not_logged"], 0)
        self.assertIn("T0", self.tracks(d))
        self.assertEqual([(r["channel"], r["track_id"]) for r in d["predicted"]["eyes"] if r["excluded"]], [(FR, "T0")])

    def test_free_channel_rows_that_cannot_all_fit_are_counted(self):
        d = self.superseded_with(self.glare_at(FR, *("T%d" % k for k in range(30))))
        shown = sum(r["excluded"] for r in d["predicted"]["eyes"])
        self.assertGreater(shown, 0)
        self.assertEqual(d["inputs"]["over_limit_not_logged"], 30 - shown)

    def scored(self, glare):
        """Rung 9's gate, closed, on 30 tracks with a touch that puts front left over the limit. Plumbing, behind a patched BACKEND."""
        names = ["T%d" % k for k in range(30)]
        script = [[T0 + 2 + k, name, "none", "counter-1"] for k, name in enumerate(names[:-1])] + [[T0 + 300, names[-1], "engaged", "counter-1"]]
        inputs = [(T0, "light_observed", {"entity_id": e, **scene(180), "context": "unknown"}) for e in events.LIGHTS]
        inputs += [(T0 + 1, "occupancy", {"value": "on", "via": "t"}), (T0 + 600, "occupancy", {"value": "on", "via": "t"})]
        inputs += [(T0 + 450, "correction", {"correction_id": "c1", "entity_id": FL, "after": scene(240), "source": "t"})]
        gate = test_score.closed_gate(self.tmp)
        config = {"gate": coordinator.gate_config(coordinator.load_gate(gate)),
                  "light_model": {"source": "file", "path": str(test_score.real_model(self.tmp, surfaces=False, glare=glare(names)))}}
        with mock.patch.object(light_adapter, "BACKEND", "den-light predict"), mock.patch.object(coordinator, "GATE_FILE", gate):
            header, log = runner.run(runner.tagged(sorted(inputs, key=lambda row: row[0]), "poll_log"),
                                     [hold_fixtures.ScriptedNeed({"script": script}), incumbent.make_policy(), coordinator.make_policy(config)],
                                     tail_s=10.0, sources=[{"name": "test"}])
            path = self.tmp / "log.jsonl"
            events.write(path, header, log)
            lines, code = score.report(path, gate_path=gate)
        return "\n".join(lines), code, [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_scored_run_with_tracks_left_off_still_logs_the_conflict(self):
        text, code, decisions = self.scored(lambda names: {FL: {"T0": {"G": 3.0, "sd": 0.01}}})
        touched = next(d for d in decisions if d["inputs"]["hold_set"]["held"])
        self.assertGreater(touched["inputs"]["tracks_not_logged"], 0)
        self.assertEqual([row["track"] for row in touched["hard_conflicts"]], ["T0"])
        self.assertRegex(text, r"hard constraints: OK \(free channels hold the glare limit; [1-9]\d* conflict\(s\) logged\)")
        self.assertEqual(code, 0, text)

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_scored_run_that_could_not_log_every_conflict_fails(self):
        text, code, decisions = self.scored(lambda names: {FL: {name: {"G": 3.0, "sd": 0.01} for name in names}})
        self.assertTrue(any(d["inputs"].get("over_limit_not_logged") for d in decisions))
        self.assertRegex(text, r"hard constraints: FAIL \(0 free channel row\(s\) over the glare limit, [1-9]\d* conflict\(s\) not logged\)")
        self.assertEqual(code, 1, text)

    def test_the_worst_shape_a_contract_record_allows_fits(self):
        names = ["track-%06d" % k for k in range(12)]
        surfaces = {"a-surface-with-a-thirty-char-%d" % n: ["other/%d" % (n * 10 + m) for m in range(10)] for n in range(8)}
        glare = {b: {name: {"G": 3.0, "sd": 0.01} for name in names} for b in events.LIGHTS}
        spec = {"source": "fake", "params": {"filler": 576 - 12, "glare": glare, "error": {"median_pct": 8.0, "p90_pct": 20.0}}, "surfaces": surfaces}
        p = policy(light_model=spec, compensation={"option": "P3", "form": "strict"})
        start(p)
        out, holds = [], []
        for k, name in enumerate(names):
            out += feed(p, ev(T0 + 2 + k, "person_state", self.full_state(T0 + 2 + k, name, list(surfaces)[k % 8]), "need"))
        for k, b in enumerate(events.LIGHTS):
            holds.append(hold(b, 100 + 30 * k, T0 + 30 + k))
            out += feed(p, ev(T0 + 30 + k, "mode", mode(2 + k, holds)["data"], "incumbent"),
                        ev(T0 + 30 + k, "correction", {"correction_id": "c%d" % k, "entity_id": b, "after": scene(100 + 30 * k), "source": "t",
                                                       "who": {"track_id": names[0]}}))
        self.valid(out)
        self.assertEqual(len(out[-1]["inputs"]["hold_set"]["held"]), 4)

    def test_field_support_is_the_schemas_two_keys(self):
        _, out = self.seen(1, reading=[0])
        [support] = out[-1]["inputs"]["field_support"].values()
        self.assertEqual(sorted(support), sorted(self.FIELDS))
        for field in support.values():
            self.assertEqual(sorted(field), ["last_support_capture_ts", "support_status"])

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_run_through_the_runner_writes_replays_and_says_so(self):
        script = [[T0 + 2 + k, "T%d" % k, "engaged" if k == 39 else "none", "counter-1"] for k in range(40)]
        inputs = [(T0, "light_observed", {"entity_id": e, **scene(180), "context": "unknown"}) for e in events.LIGHTS]
        inputs += [(T0 + 1, "occupancy", {"value": "on", "via": "t"}), (T0 + 60, "occupancy", {"value": "on", "via": "t"})]
        policies = [hold_fixtures.ScriptedNeed({"script": script}), incumbent.make_policy(), policy(compensation={"option": "P1", "form": "mode"})]
        header, log = runner.run(runner.tagged(inputs, "synthetic"), policies, synthetic=True, tail_s=10.0, sources=[])
        self.assertLess(longest_decision(log), events.MAX_LINE_BYTES)
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        self.assertTrue(check.check(path)["ok"])
        lines, code = score.report(path, preview=True)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertRegex("\n".join(lines), r"tracks not logged: \d+ decision\(s\) left out")
        for name, (ok, text) in score.structural(log, coordinator.make_policy(policies[2].config).light, SLOTS).items():
            self.assertTrue(ok, (name, text))

    @unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
    def test_a_report_with_every_track_logged_does_not_mention_it(self):
        header, log = hold_fixtures.run_case("V62", "P1-mode")
        path = self.tmp / "log.jsonl"
        events.write(path, header, log)
        text = "\n".join(score.report(path, preview=True)[0])
        self.assertNotIn("tracks not logged", text)
        self.assertNotIn("surfaces not logged", text)


def evening_log(tmp, name="log.jsonl", configs=None, source="synthetic", actuation=None, **options):
    items, _, _ = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0, **options)
    synthetic_run = source == "synthetic"
    header, log = runner.run(runner.tagged(items, source), runner.load_policies(["need", "incumbent", "coordinator"], configs),
                             synthetic=synthetic_run, actuation=actuation, plant=actuation != "shadow",
                             sources=[{"name": "test", "params": options}])
    path = Path(tmp) / name
    events.write(path, header, log)
    return path, header, log


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_every_decision_of_an_evening_lists_the_whole_map(self):
        # With the map cut to the tracked surfaces, rung 10 read 7 of 7 hits where the same frames, on the whole map, read
        # 21 of 28 and failed: it could not see that the light had missed counter-1, where nobody is.
        _, _, log = evening_log(self.tmp, touch=True)
        mine = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        self.assertTrue(mine)
        for d in mine:
            self.assertEqual(sorted(d["predicted"]["surfaces"]), ["armchair-book", "counter-1", "room", "unit", "wall-west"])

    def test_a_map_too_wide_for_the_line_replays_and_the_report_says_so(self):
        # The header's JSON sorts the map's keys, so what stays on a line must not hang on the order the map came in.
        spec = WideMapLineTest.wide(None, 576, 30)
        spec["surfaces"] = dict(reversed(list(spec["surfaces"].items())))
        path, _, log = evening_log(self.tmp, configs={"coordinator": {"light_model": spec}}, touch=True)
        self.assertTrue(check.check(path)["ok"])
        mine = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        self.assertTrue(all(d["predicted"]["surfaces_not_logged"] > 0 for d in mine))
        lines, code = score.report(path, preview=True)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertIn("surfaces not logged: %d decision(s) left out up to %d of the map's %d surfaces, those no logged track is at"
                      % (len(mine), max(d["predicted"]["surfaces_not_logged"] for d in mine), 577), "\n".join(lines))

    def test_a_run_replays_byte_for_byte_under_each_actuation(self):
        for name, source, actuation in (("a.jsonl", "synthetic", None), ("b.jsonl", "fact_log", "shadow"), ("c.jsonl", "fact_log", None)):
            path, header, log = evening_log(self.tmp, name, source=source, actuation=actuation, touch=True)
            self.assertTrue(check.check(path)["ok"], name)
            mine = [e for e in log if e["kind"] == "decision" and e["source"] == "decide"]
            self.assertTrue(mine)
            want = actuation or ("synthetic" if source == "synthetic" else "replay")
            self.assertEqual({d["data"]["actuation"] for d in mine}, {want})
            self.assertEqual(header["actuation"], want)

    def test_replay_refuses_a_changed_decision(self):
        path, _, _ = evening_log(self.tmp, touch=True)
        lines = path.read_text("ascii").split("\n")[:-1]
        n = next(i for i, line in enumerate(lines) if '"status":"applied"' in line and '"source":"decide"' in line)
        lines[n] = lines[n].replace('"status":"applied"', '"status":"partial"')
        variant = self.tmp / "variant.jsonl"
        variant.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
        result = check.check(variant)
        self.assertEqual((result["ok"], result["first_mismatch"]["line"]), (False, n + 1))

    def test_without_holds_the_scenes_are_decides_at_the_same_look(self):
        items, _, _ = synthetic.evening(unclear_rate=0.0, invalid_rate=0.0, fail_rate=0.0)
        look = {"looks": {"reading@0": {"scene": scene(200)}}}
        runs = {}
        for name, config in (("decide", look), ("coordinator", {})):
            _, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", name], {name: config}),
                                synthetic=True, sources=[])
            runs[name] = [(e["t"], e["data"]["requested_scene"]) for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        self.assertTrue(runs["decide"])
        self.assertEqual(runs["coordinator"], runs["decide"])

    def test_validate_reads_a_coordinator_log_with_a_solved_look(self):
        import validate
        path, _, _ = evening_log(self.tmp, configs={"coordinator": {"compensation": {"option": "P0"}}}, touch=True)
        lines, passed = validate.report(path)
        self.assertTrue(passed, "\n".join(lines))
        solved = [e["data"] for e in events.read(path)[1] if e["kind"] == "decision" and e["source"] == "decide"
                  and "solve:" in e["data"]["why"]]
        self.assertTrue(solved)


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ExportReplayTest(test_live_inputs.Temp):
    """The Home Assistant exports alone, with the coordinator beside the frozen den_lighting: no facts, so no need, and
    bulbs with no levels. This is the shape of the owner's first real evenings."""

    def test_the_exports_replay_and_score_with_no_level_guessed(self):
        status, lights, commands, corrections = self.captured(test_live_inputs.evening())
        text = self.main("--status", str(status), "--lights", str(lights), "--live-commands", str(commands),
                         "--corrections", str(corrections), "--policies", "incumbent", "coordinator", "--actuation", "shadow",
                         "--out", str(self.tmp / "out"))
        self.assertIn("actuation shadow", text)
        path = self.tmp / "out" / "log.jsonl"
        self.assertTrue(check.check(path)["ok"])
        header, log = events.read(path)
        self.assertEqual(header["actuation"], "shadow")
        mine = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        self.assertTrue(mine)
        for d in mine:
            self.assertEqual(set(d["applied_scene"]), set(d["requested_scene"]))
            self.assertEqual(d["actuation"], "shadow")
        held = [d for d in mine if d["inputs"]["hold_set"]["held"]]
        self.assertTrue(held)
        self.assertEqual(held[-1]["applied_scene"][test_live_inputs.L1]["status"], "held")
        lines, code = score.report(path, preview=True)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertIn("anticipation (replay surrogate): insufficient", "\n".join(lines))


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class ByteIdenticalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_the_four_synthetic_evenings_are_what_they_were_before_rung_9(self):
        for n, (flags, sha) in enumerate(BEFORE_RUNG_9.items()):
            with self.subTest(flags=flags), mock.patch("builtins.print"):
                out = self.tmp / str(n)
                run.main(["--synthetic", "evening", *flags, "--policies", "need", "incumbent", "decide", "--out", str(out)])
                self.assertEqual(hashlib.sha256((out / "log.jsonl").read_bytes()).hexdigest(), sha)
                self.assertTrue(check.check(out / "log.jsonl")["ok"])


@unittest.skipUnless(HAVE_POLICY, "den_lighting policy.py not found")
class RunFlagsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def main(self, *argv):
        with mock.patch("builtins.print") as printed:
            run.main(["--out", str(self.tmp / "out"), *argv])
        return "\n".join(str(call.args[0]) for call in printed.call_args_list)

    def test_a_coordinator_run_says_what_it_stood_on(self):
        with mock.patch.object(coordinator, "GATE_FILE", test_score.all_open(self.tmp)):
            text = self.main("--synthetic", "evening", "--policies", "need", "incumbent", "coordinator", "--compensation", "P3",
                             "--compensation-form", "strict")
        self.assertIn("light model lm-fake-rung8-plan-1 (fake: plumbing only, never a gate)", text)
        self.assertIn("compensation P3-strict/P1 (pending: named option, not a choice)", text)
        self.assertIn("open slot(s) at their proposals", text)
        self.assertTrue(check.check(self.tmp / "out" / "log.jsonl")["ok"])

    def test_a_light_model_file_and_a_surface_map(self):
        model, surfaces = self.tmp / "light-model.json", self.tmp / "surfaces.json"
        model.write_text(json.dumps(fake_light.build(surfaces=False, unit="relative")), encoding="utf-8")
        surfaces.write_text(json.dumps({"desk": ["counter-1/0", "counter-1/1"]}), encoding="utf-8")
        text = self.main("--synthetic", "evening", "--policies", "need", "incumbent", "coordinator",
                         "--light-model", str(model), "--surfaces", str(surfaces))
        self.assertIn("lm-fake-rung8-plan-1", text)
        header, _ = events.read(self.tmp / "out" / "log.jsonl")
        logged = next(p for p in header["policies"] if p["module"] == "coordinator")["config"]["light_model"]
        self.assertEqual((logged["source"], logged["unit"], logged["surfaces"]),
                         ("file", "relative", {"desk": ["counter-1/0", "counter-1/1"]}))
        self.assertTrue(check.check(self.tmp / "out" / "log.jsonl")["ok"])

    def test_readmes_plumbing_run_is_the_same_once_the_owner_closes_the_map(self):
        # The verifier's scenario: the slot closed at rRRcCC blocks, then README's own command on the default fake.
        def mine(name):
            _, log = events.read(self.tmp / name / "log.jsonl")
            return [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]

        argv = ["--synthetic", "evening", "--touch", "--policies", "need", "incumbent", "coordinator"]
        with mock.patch("builtins.print"):
            run.main(["--out", str(self.tmp / "open"), *argv])
        gate = coordinator.load_gate()
        gate["slots"]["surface_map"].update(status="closed", value={"room": ["r%02dc00" % n for n in range(24)]})
        closed = self.tmp / "gate.json"
        closed.write_text(json.dumps(gate), encoding="utf-8")
        with mock.patch("builtins.print") as printed, mock.patch.object(coordinator, "GATE_FILE", closed):
            run.main(["--out", str(self.tmp / "closed"), *argv])
            lines, code = score.report(self.tmp / "closed" / "log.jsonl", preview=True, gate_path=closed)
        text = "\n".join(lines)
        self.assertNotIn("does not have", "\n".join(str(call.args[0]) for call in printed.call_args_list))
        self.assertEqual(code, 0, text)
        self.assertEqual([d["requested_scene"] for d in mine("closed")], [d["requested_scene"] for d in mine("open")])
        self.assertEqual(sum(bool(d["targets"]) for d in mine("closed")), sum(bool(d["targets"]) for d in mine("open")))
        self.assertIn("surfaces: armchair-book 4, counter-1 4, room 12, wall-west 4, of 12 blocks", text)
        self.assertNotIn("another surface map", text)
        self.assertIn("fake light model: plumbing only, never a gate", text)

    def test_a_map_naming_a_block_the_model_lacks_is_said_out_loud(self):
        surfaces = self.tmp / "surfaces.json"
        surfaces.write_text(json.dumps({"room": ["r00c00"], "desk": ["counter-1/0"], "bare": []}), encoding="utf-8")
        text = self.main("--synthetic", "evening", "--policies", "need", "incumbent", "coordinator", "--surfaces", str(surfaces))
        self.assertIn("coordinator: surface map: bare, room: no block, or a block the light model does not have", text)
        lines, _ = score.report(self.tmp / "out" / "log.jsonl", preview=True)
        self.assertIn("surface map: bare, room: no block, or a block the light model does not have", "\n".join(lines))

    def test_a_run_on_the_full_grid_under_the_proposed_option_writes_its_log(self):
        # The review's scenario as the owner meets it: on the cloud build this stopped with
        # `run.py: decision line over 16384 bytes` and no log, from the decisions taken while a bulb was held.
        model, surfaces = self.tmp / "light-model.json", self.tmp / "surfaces.json"
        model.write_text(json.dumps(fake_light.build(filler=576 - 12, surfaces=False)), encoding="utf-8")
        surfaces.write_text(json.dumps(full_grid()["surfaces"]), encoding="utf-8")
        self.main("--synthetic", "evening", "--touch", "--policies", "need", "incumbent", "coordinator", "--light-model", str(model),
                  "--surfaces", str(surfaces), "--compensation", "P1", "--compensation-form", "mode")
        path = self.tmp / "out" / "log.jsonl"
        _, log = events.read(path)
        mine = [e["data"] for e in log if e["kind"] == "decision" and e["source"] == "decide"]
        held = [d["trigger"] for d in mine if d["inputs"]["hold_set"]["held"]]
        self.assertIn("hold change", held)
        self.assertIn("person_state change", held)
        self.assertLess(longest_decision(log), events.MAX_LINE_BYTES)
        self.assertTrue(check.check(path)["ok"])

    def test_refusals(self):
        for argv in (["--synthetic", "evening", "--actuation", "shadow"],
                     ["--poll", str(self.tmp / "poll.txt"), "--actuation", "shadow"],  # the plant is on
                     ["--synthetic", "evening", "--policies", "incumbent", "decide", "coordinator"],
                     ["--synthetic", "evening", "--policies", "incumbent", "--light-model", "fake"],
                     ["--synthetic", "evening", "--policies", "incumbent", "--compensation", "P0"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                self.main(*argv)


if __name__ == "__main__":
    unittest.main()
