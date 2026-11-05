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
        self.assertIn("counter-1", held["footprint"])

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
        self.assertEqual(base["predicted"]["surfaces"]["counter-1"], None)
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
